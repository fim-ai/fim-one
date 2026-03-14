"""Import conflict resolver — detect unresolved external references in blueprints."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .types import NodeType, WorkflowBlueprint


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ResolvedReference:
    """A reference that exists and is accessible to the importing user."""

    node_id: str
    node_type: str
    field_name: str
    referenced_id: str
    resource_type: str
    resource_name: str | None = None


@dataclass
class UnresolvedReference:
    """A reference to a resource that doesn't exist or isn't accessible."""

    node_id: str
    node_type: str
    field_name: str
    referenced_id: str
    resource_type: str


@dataclass
class ImportResolution:
    """Result of resolving all external references in a blueprint."""

    resolved: list[ResolvedReference] = field(default_factory=list)
    unresolved: list[UnresolvedReference] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Reference extraction
# ---------------------------------------------------------------------------

# Maps NodeType -> list of (field_name, resource_type) to check
_REFERENCE_FIELDS: dict[NodeType, list[tuple[str, str]]] = {
    NodeType.AGENT: [("agent_id", "agent")],
    NodeType.CONNECTOR: [("connector_id", "connector")],
    NodeType.KNOWLEDGE_RETRIEVAL: [
        ("knowledge_base_id", "knowledge_base"),
        ("kb_id", "knowledge_base"),
    ],
    NodeType.SUB_WORKFLOW: [("workflow_id", "workflow")],
    NodeType.MCP: [("server_id", "mcp_server")],
}


@dataclass
class _ExtractedRef:
    """Internal: a single extracted reference from a node."""

    node_id: str
    node_type: str
    field_name: str
    referenced_id: str
    resource_type: str


def _extract_references(blueprint: WorkflowBlueprint) -> list[_ExtractedRef]:
    """Scan all nodes and extract external resource references."""
    refs: list[_ExtractedRef] = []

    for node in blueprint.nodes:
        field_specs = _REFERENCE_FIELDS.get(node.type)
        if not field_specs:
            continue

        for field_name, resource_type in field_specs:
            ref_id = node.data.get(field_name)
            if ref_id and isinstance(ref_id, str) and ref_id.strip():
                refs.append(
                    _ExtractedRef(
                        node_id=node.id,
                        node_type=node.type.value,
                        field_name=field_name,
                        referenced_id=ref_id.strip(),
                        resource_type=resource_type,
                    )
                )

    return refs


# ---------------------------------------------------------------------------
# DB queries — batch by resource type
# ---------------------------------------------------------------------------


async def _query_accessible_ids(
    db: AsyncSession,
    resource_type: str,
    candidate_ids: set[str],
    user_id: str,
    user_org_ids: list[str],
    subscribed_ids: list[str] | None = None,
) -> dict[str, str | None]:
    """Query the DB for which candidate IDs exist and are accessible.

    Returns a mapping of id -> name (or None) for accessible resources.
    """
    from fim_one.web.visibility import build_visibility_filter

    if not candidate_ids:
        return {}

    model = _get_model_for_resource_type(resource_type)
    if model is None:
        return {}

    stmt = select(model.id, model.name).where(
        model.id.in_(candidate_ids),
        build_visibility_filter(
            model, user_id, user_org_ids, subscribed_ids=subscribed_ids
        ),
    )
    result = await db.execute(stmt)
    return {row[0]: row[1] for row in result.all()}


def _get_model_for_resource_type(resource_type: str) -> Any:
    """Return the ORM model class for a given resource type string."""
    from fim_one.web.models import Agent, Connector, KnowledgeBase, MCPServer, Workflow

    mapping: dict[str, Any] = {
        "agent": Agent,
        "connector": Connector,
        "knowledge_base": KnowledgeBase,
        "workflow": Workflow,
        "mcp_server": MCPServer,
    }
    return mapping.get(resource_type)


# ---------------------------------------------------------------------------
# Main resolver
# ---------------------------------------------------------------------------


async def resolve_blueprint_references(
    blueprint: WorkflowBlueprint,
    db: AsyncSession,
    user_id: str,
    user_org_ids: list[str] | None = None,
    subscribed_ids: list[str] | None = None,
) -> ImportResolution:
    """Resolve all external resource references in a blueprint.

    Scans nodes for references to agents, connectors, knowledge bases,
    sub-workflows, and MCP servers. Queries the database to check which
    referenced IDs exist and are accessible to the importing user.

    Parameters
    ----------
    blueprint:
        A parsed WorkflowBlueprint.
    db:
        An async database session.
    user_id:
        The ID of the user importing the workflow.
    user_org_ids:
        Organization IDs the user belongs to (for visibility filtering).
        If None, defaults to an empty list.
    subscribed_ids:
        Resource IDs the user has subscribed to from the Market.
        If None, the function will query the ResourceSubscription table
        to obtain them automatically.

    Returns
    -------
    ImportResolution
        Contains resolved references, unresolved references, and
        human-readable warning messages.
    """
    if user_org_ids is None:
        user_org_ids = []

    # Fetch subscribed resource IDs if not provided by the caller
    if subscribed_ids is None:
        from fim_one.web.models.resource_subscription import ResourceSubscription

        sub_result = await db.execute(
            select(ResourceSubscription.resource_id).where(
                ResourceSubscription.user_id == user_id,
            )
        )
        subscribed_ids = list(sub_result.scalars().all())

    refs = _extract_references(blueprint)
    if not refs:
        return ImportResolution()

    # Group references by resource_type to batch DB queries
    refs_by_type: dict[str, list[_ExtractedRef]] = {}
    for ref in refs:
        refs_by_type.setdefault(ref.resource_type, []).append(ref)

    # Query each resource type once
    accessible_by_type: dict[str, dict[str, str | None]] = {}
    for resource_type, type_refs in refs_by_type.items():
        candidate_ids = {r.referenced_id for r in type_refs}
        accessible_by_type[resource_type] = await _query_accessible_ids(
            db, resource_type, candidate_ids, user_id, user_org_ids,
            subscribed_ids=subscribed_ids,
        )

    # Classify each reference
    resolution = ImportResolution()
    for ref in refs:
        accessible = accessible_by_type.get(ref.resource_type, {})
        if ref.referenced_id in accessible:
            resolution.resolved.append(
                ResolvedReference(
                    node_id=ref.node_id,
                    node_type=ref.node_type,
                    field_name=ref.field_name,
                    referenced_id=ref.referenced_id,
                    resource_type=ref.resource_type,
                    resource_name=accessible[ref.referenced_id],
                )
            )
        else:
            resolution.unresolved.append(
                UnresolvedReference(
                    node_id=ref.node_id,
                    node_type=ref.node_type,
                    field_name=ref.field_name,
                    referenced_id=ref.referenced_id,
                    resource_type=ref.resource_type,
                )
            )

    # Build human-readable warnings
    for unref in resolution.unresolved:
        type_label = unref.resource_type.replace("_", " ")
        resolution.warnings.append(
            f"Node '{unref.node_id}' ({unref.node_type}) references "
            f"{type_label} '{unref.referenced_id}' which was not found "
            f"or is not accessible."
        )

    return resolution

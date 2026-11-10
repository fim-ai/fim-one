"""Import conflict resolver — detect unresolved external references in blueprints."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .types import NodeType, WorkflowBlueprint

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


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
    NodeType.KNOWLEDGE_RETRIEVAL: [("kb_ids", "knowledge_base")],
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
            value = node.data.get(field_name)
            # A field holds one id (``agent_id``) or a list (``kb_ids``).
            values = value if isinstance(value, list) else [value]
            for ref_id in values:
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
# Main resolver
# ---------------------------------------------------------------------------


async def resolve_blueprint_references(
    blueprint: WorkflowBlueprint,
    db: AsyncSession,
    user_id: str,
) -> ImportResolution:
    """Sort the blueprint's external references into usable and not.

    The importing user becomes the workflow's author, and a step uses a
    resource on its author's access. So a reference is resolved when that
    user passes the access rule the steps themselves ask
    (``core.assembly.visibility.authorize_use``): the resource is visible
    to them, switched on, and for a database connector or a knowledge base,
    their own. Anything else is unresolved, and its warning says why. The
    import goes ahead either way.

    Credentials are not looked at. A resolved connector or MCP server can
    still fail in a run with ``credential_required``: the credential is the
    runner's, who may be someone else, and is checked when the step runs.
    """
    from fim_one.core.assembly.errors import RESOURCE_NOT_FOUND
    from fim_one.core.assembly.principal import CallContext, provisional_principal
    from fim_one.core.assembly.visibility import ResourceKind, authorize_use

    refs = _extract_references(blueprint)
    if not refs:
        return ImportResolution()

    # The importer, with no binding yet: the workflow does not exist.
    ctx = CallContext(
        principal=provisional_principal(user_id), binding=None, entry="workflow_node"
    )

    # One decision per resource type.
    ids_by_type: dict[str, list[str]] = {}
    for ref in refs:
        ids_by_type.setdefault(ref.resource_type, []).append(ref.referenced_id)
    decisions = {
        resource_type: await authorize_use(db, ctx, ResourceKind(resource_type), ids)
        for resource_type, ids in ids_by_type.items()
    }

    resolution = ImportResolution()
    for ref in refs:
        decision = decisions[ref.resource_type]
        refusal = decision.refused.get(ref.referenced_id)
        row = next((r for r in decision.usable if r.id == ref.referenced_id), None)
        # A decision that neither returns the row nor refuses it does not
        # make the reference usable: it is unresolved, like one not found.
        if refusal is None and row is not None:
            resolution.resolved.append(
                ResolvedReference(
                    node_id=ref.node_id,
                    node_type=ref.node_type,
                    field_name=ref.field_name,
                    referenced_id=ref.referenced_id,
                    resource_type=ref.resource_type,
                    resource_name=row.name,
                )
            )
            continue
        resolution.unresolved.append(
            UnresolvedReference(
                node_id=ref.node_id,
                node_type=ref.node_type,
                field_name=ref.field_name,
                referenced_id=ref.referenced_id,
                resource_type=ref.resource_type,
            )
        )
        type_label = ref.resource_type.replace("_", " ")
        reason = (
            "which was not found or is not accessible."
            if refusal is None or refusal.error_code == RESOURCE_NOT_FOUND
            else f"which cannot be used: {refusal.detail}."
        )
        resolution.warnings.append(
            f"Node '{ref.node_id}' ({ref.node_type}) references "
            f"{type_label} '{ref.referenced_id}' {reason}"
        )

    return resolution

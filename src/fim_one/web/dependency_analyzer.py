"""Dependency analyzer for Market Solutions.

Resolves the full dependency tree for a Solution (agent, skill, workflow)
and extracts credential schemas required for onboarding.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

# Sensitive keyword fragments — env/header keys containing these (case-insensitive)
# are treated as secrets and rendered as password fields.
_SECRET_KEYWORDS = {"KEY", "SECRET", "TOKEN", "PASSWORD"}


@dataclass
class ContentDep:
    """Auto-subscribed dependency (KB, Skill) — no user action needed."""

    resource_type: str  # "knowledge_base" | "skill"
    resource_id: str
    resource_name: str


@dataclass
class ConnectionDep:
    """Dependency requiring credential configuration (Connector, MCP Server)."""

    resource_type: str  # "connector" | "mcp_server"
    resource_id: str
    resource_name: str
    credential_schema: dict[str, Any]  # field definitions for the onboarding form
    allow_fallback: bool = False  # if True, owner's credentials are shared as fallback


@dataclass
class DependencyManifest:
    """Complete dependency manifest for a Solution."""

    content_deps: list[ContentDep] = field(default_factory=list)
    connection_deps: list[ConnectionDep] = field(default_factory=list)

    def merge(self, other: DependencyManifest) -> None:
        """Merge another manifest into this one (for recursive resolution)."""
        self.content_deps.extend(other.content_deps)
        self.connection_deps.extend(other.connection_deps)

    def deduplicate(self) -> None:
        """Remove duplicate dependencies (same resource referenced multiple times)."""
        seen_content: set[str] = set()
        unique_content: list[ContentDep] = []
        for dep in self.content_deps:
            key = f"{dep.resource_type}:{dep.resource_id}"
            if key not in seen_content:
                seen_content.add(key)
                unique_content.append(dep)
        self.content_deps = unique_content

        seen_conn: set[str] = set()
        unique_conn: list[ConnectionDep] = []
        for conn_dep in self.connection_deps:
            key = f"{conn_dep.resource_type}:{conn_dep.resource_id}"
            if key not in seen_conn:
                seen_conn.add(key)
                unique_conn.append(conn_dep)
        self.connection_deps = unique_conn

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API response."""
        return {
            "content_deps": [
                {
                    "resource_type": d.resource_type,
                    "resource_id": d.resource_id,
                    "resource_name": d.resource_name,
                }
                for d in self.content_deps
            ],
            "connection_deps": [
                {
                    "resource_type": d.resource_type,
                    "resource_id": d.resource_id,
                    "resource_name": d.resource_name,
                    "credential_schema": d.credential_schema,
                    "allow_fallback": d.allow_fallback,
                }
                for d in self.connection_deps
            ],
        }


# ---------------------------------------------------------------------------
# Credential schema extraction
# ---------------------------------------------------------------------------


def extract_connector_credential_schema(connector: Any) -> dict[str, Any]:
    """Extract credential field definitions from a Connector's auth_type.

    Maps ``auth_type`` to the set of credential fields the subscriber must
    provide.  If the connector has a ``base_url``, it is included as an
    optional text field.  Additional keys found in ``auth_config`` are
    merged in as well.
    """
    auth_type: str = getattr(connector, "auth_type", "none") or "none"
    base_url: str | None = getattr(connector, "base_url", None)
    auth_config: dict[str, Any] | None = getattr(connector, "auth_config", None)

    schema: dict[str, dict[str, Any]] = {}

    if auth_type == "bearer":
        schema["api_key"] = {"type": "password", "label": "API Key", "required": True}
    elif auth_type == "basic":
        schema["username"] = {"type": "text", "label": "Username", "required": True}
        schema["password"] = {"type": "password", "label": "Password", "required": True}
    elif auth_type == "api_key":
        schema["api_key"] = {"type": "password", "label": "API Key", "required": True}
    elif auth_type == "oauth2":
        schema["client_id"] = {"type": "text", "label": "Client ID", "required": True}
        schema["client_secret"] = {
            "type": "password",
            "label": "Client Secret",
            "required": True,
        }
    # "none" → empty schema

    # Optional base_url field
    if base_url:
        schema["base_url"] = {
            "type": "text",
            "label": "Base URL",
            "required": False,
            "default": base_url,
        }

    # Incorporate any extra fields from auth_config
    if auth_config and isinstance(auth_config, dict):
        for key, value in auth_config.items():
            if key not in schema:
                is_secret = any(kw in key.upper() for kw in _SECRET_KEYWORDS)
                schema[key] = {
                    "type": "password" if is_secret else "text",
                    "label": key.replace("_", " ").title(),
                    "required": False,
                }

    return schema


def extract_mcp_credential_schema(server: Any) -> dict[str, Any]:
    """Extract credential field definitions from an MCP Server's env/headers config.

    Each key in ``env`` and ``headers`` becomes a credential field.  Keys
    whose names contain secret-like fragments (KEY, SECRET, TOKEN, PASSWORD)
    are rendered as password fields; all others are plain text.
    """
    env: dict[str, Any] | None = getattr(server, "env", None)
    headers: dict[str, Any] | None = getattr(server, "headers", None)

    schema: dict[str, dict[str, Any]] = {}

    if env and isinstance(env, dict):
        for key in env:
            is_secret = any(kw in key.upper() for kw in _SECRET_KEYWORDS)
            schema[key] = {
                "type": "password" if is_secret else "text",
                "label": key.replace("_", " ").title(),
                "required": True,
            }

    if headers and isinstance(headers, dict):
        for key in headers:
            if key not in schema:
                is_secret = any(kw in key.upper() for kw in _SECRET_KEYWORDS)
                schema[key] = {
                    "type": "password" if is_secret else "text",
                    "label": key.replace("_", " ").title(),
                    "required": True,
                }

    return schema


# ---------------------------------------------------------------------------
# Main resolver
# ---------------------------------------------------------------------------

# Node type string → list of (data_field, resource_type) to scan.
# Mirrors _REFERENCE_FIELDS from core/workflow/import_resolver.py but uses
# plain strings instead of the NodeType enum so we can operate on raw
# blueprint dicts without parsing into dataclass objects.
_WORKFLOW_NODE_REFS: dict[str, list[tuple[str, str]]] = {
    "AGENT": [("agent_id", "agent")],
    "CONNECTOR": [("connector_id", "connector")],
    "KNOWLEDGE_RETRIEVAL": [
        ("knowledge_base_id", "knowledge_base"),
        ("kb_id", "knowledge_base"),
    ],
    "SUB_WORKFLOW": [("workflow_id", "workflow")],
    "MCP": [("server_id", "mcp_server")],
}


async def _fetch_by_id(model_cls: Any, resource_id: str, db: AsyncSession) -> Any | None:
    """Fetch a single ORM instance by primary key, returning None if missing."""
    result: Any = await db.execute(select(model_cls).where(model_cls.id == resource_id))
    return result.scalar_one_or_none()


async def _resolve_agent(agent_id: str, db: AsyncSession) -> DependencyManifest:
    """Resolve dependencies for a single Agent."""
    from fim_one.web.models.agent import Agent
    from fim_one.web.models.connector import Connector
    from fim_one.web.models.knowledge_base import KnowledgeBase
    from fim_one.web.models.mcp_server import MCPServer
    from fim_one.web.models.skill import Skill

    manifest = DependencyManifest()

    agent = await _fetch_by_id(Agent, agent_id, db)
    if agent is None:
        return manifest

    # Knowledge bases
    kb_ids: list[str] = agent.kb_ids or []
    for kb_id in kb_ids:
        if not kb_id or not isinstance(kb_id, str):
            continue
        kb = await _fetch_by_id(KnowledgeBase, kb_id, db)
        if kb is not None:
            manifest.content_deps.append(
                ContentDep(
                    resource_type="knowledge_base",
                    resource_id=kb.id,
                    resource_name=kb.name,
                )
            )

    # Connectors
    connector_ids: list[str] = agent.connector_ids or []
    for conn_id in connector_ids:
        if not conn_id or not isinstance(conn_id, str):
            continue
        connector = await _fetch_by_id(Connector, conn_id, db)
        if connector is not None:
            manifest.connection_deps.append(
                ConnectionDep(
                    resource_type="connector",
                    resource_id=connector.id,
                    resource_name=connector.name,
                    credential_schema=extract_connector_credential_schema(connector),
                    allow_fallback=getattr(connector, "allow_fallback", False),
                )
            )

    # Skills
    skill_ids: list[str] = agent.skill_ids or []
    for sid in skill_ids:
        if not sid or not isinstance(sid, str):
            continue
        skill = await _fetch_by_id(Skill, sid, db)
        if skill is not None:
            manifest.content_deps.append(
                ContentDep(
                    resource_type="skill",
                    resource_id=skill.id,
                    resource_name=skill.name,
                )
            )
            # Recursively resolve skill's own deps (resource_refs)
            skill_manifest = await _resolve_skill(sid, db)
            manifest.merge(skill_manifest)

    # MCP servers
    mcp_server_ids: list[str] = agent.mcp_server_ids or []
    for server_id in mcp_server_ids:
        if not server_id or not isinstance(server_id, str):
            continue
        server = await _fetch_by_id(MCPServer, server_id, db)
        if server is not None:
            manifest.connection_deps.append(
                ConnectionDep(
                    resource_type="mcp_server",
                    resource_id=server.id,
                    resource_name=server.name,
                    credential_schema=extract_mcp_credential_schema(server),
                    allow_fallback=getattr(server, "allow_fallback", False),
                )
            )

    return manifest


async def _resolve_skill(skill_id: str, db: AsyncSession) -> DependencyManifest:
    """Resolve dependencies for a single Skill.

    Skills store all external references in a unified ``resource_refs`` JSON
    array.  Each entry has ``{"type": "<resource_type>", "id": "<uuid>", ...}``.
    We classify them the same way Agents/Workflows do:
    - ``knowledge_base`` → content dep (auto-included for subscribers)
    - ``agent`` → content dep + recursively resolve the agent's own deps
    - ``connector`` / ``mcp_server`` → connection dep (requires credentials)
    """
    from fim_one.web.models.agent import Agent
    from fim_one.web.models.connector import Connector
    from fim_one.web.models.knowledge_base import KnowledgeBase
    from fim_one.web.models.mcp_server import MCPServer
    from fim_one.web.models.skill import Skill

    manifest = DependencyManifest()

    skill = await _fetch_by_id(Skill, skill_id, db)
    if skill is None:
        return manifest

    resource_refs: list[dict[str, Any]] = skill.resource_refs or []
    for ref in resource_refs:
        if not isinstance(ref, dict):
            continue
        ref_type = ref.get("type")
        ref_id = ref.get("id")
        if not ref_id or not isinstance(ref_id, str):
            continue

        if ref_type == "connector":
            connector = await _fetch_by_id(Connector, ref_id, db)
            if connector is not None:
                manifest.connection_deps.append(
                    ConnectionDep(
                        resource_type="connector",
                        resource_id=connector.id,
                        resource_name=connector.name,
                        credential_schema=extract_connector_credential_schema(connector),
                        allow_fallback=getattr(connector, "allow_fallback", False),
                    )
                )

        elif ref_type == "knowledge_base":
            kb = await _fetch_by_id(KnowledgeBase, ref_id, db)
            if kb is not None:
                manifest.content_deps.append(
                    ContentDep(
                        resource_type="knowledge_base",
                        resource_id=kb.id,
                        resource_name=kb.name,
                    )
                )

        elif ref_type == "mcp_server":
            server = await _fetch_by_id(MCPServer, ref_id, db)
            if server is not None:
                manifest.connection_deps.append(
                    ConnectionDep(
                        resource_type="mcp_server",
                        resource_id=server.id,
                        resource_name=server.name,
                        credential_schema=extract_mcp_credential_schema(server),
                        allow_fallback=getattr(server, "allow_fallback", False),
                    )
                )

        elif ref_type == "agent":
            agent = await _fetch_by_id(Agent, ref_id, db)
            if agent is not None:
                # Agent itself is a content dep
                manifest.content_deps.append(
                    ContentDep(
                        resource_type="agent",
                        resource_id=agent.id,
                        resource_name=agent.name,
                    )
                )
                # Recursively resolve the agent's own KB/Connector deps
                agent_manifest = await _resolve_agent(ref_id, db)
                manifest.merge(agent_manifest)

    return manifest


async def _resolve_workflow(
    workflow_id: str,
    db: AsyncSession,
    *,
    _visited_workflows: set[str] | None = None,
) -> DependencyManifest:
    """Resolve dependencies for a Workflow by scanning its blueprint nodes.

    Handles recursive sub-workflow and agent references with cycle detection.
    """
    from fim_one.web.models.connector import Connector
    from fim_one.web.models.knowledge_base import KnowledgeBase
    from fim_one.web.models.mcp_server import MCPServer
    from fim_one.web.models.workflow import Workflow

    if _visited_workflows is None:
        _visited_workflows = set()

    manifest = DependencyManifest()

    if workflow_id in _visited_workflows:
        return manifest  # cycle guard
    _visited_workflows.add(workflow_id)

    workflow = await _fetch_by_id(Workflow, workflow_id, db)
    if workflow is None:
        return manifest

    blueprint: dict[str, Any] | None = workflow.blueprint
    if not blueprint or not isinstance(blueprint, dict):
        return manifest

    nodes: list[dict[str, Any]] = blueprint.get("nodes", [])
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type: str = node.get("type", "") or (node.get("data", {}).get("type", ""))
        data: dict[str, Any] = node.get("data", {})
        if not isinstance(data, dict):
            continue

        field_specs = _WORKFLOW_NODE_REFS.get(node_type.upper())
        if not field_specs:
            continue

        for field_name, resource_type in field_specs:
            ref_id = data.get(field_name)
            if not ref_id or not isinstance(ref_id, str) or not ref_id.strip():
                continue
            ref_id = ref_id.strip()

            if resource_type == "agent":
                # Recursively resolve agent deps
                agent_manifest = await _resolve_agent(ref_id, db)
                manifest.merge(agent_manifest)

            elif resource_type == "connector":
                connector = await _fetch_by_id(Connector, ref_id, db)
                if connector is not None:
                    manifest.connection_deps.append(
                        ConnectionDep(
                            resource_type="connector",
                            resource_id=connector.id,
                            resource_name=connector.name,
                            credential_schema=extract_connector_credential_schema(connector),
                            allow_fallback=getattr(connector, "allow_fallback", False),
                        )
                    )

            elif resource_type == "knowledge_base":
                kb = await _fetch_by_id(KnowledgeBase, ref_id, db)
                if kb is not None:
                    manifest.content_deps.append(
                        ContentDep(
                            resource_type="knowledge_base",
                            resource_id=kb.id,
                            resource_name=kb.name,
                        )
                    )

            elif resource_type == "mcp_server":
                server = await _fetch_by_id(MCPServer, ref_id, db)
                if server is not None:
                    manifest.connection_deps.append(
                        ConnectionDep(
                            resource_type="mcp_server",
                            resource_id=server.id,
                            resource_name=server.name,
                            credential_schema=extract_mcp_credential_schema(server),
                            allow_fallback=getattr(server, "allow_fallback", False),
                        )
                    )

            elif resource_type == "workflow":
                # Recursively resolve sub-workflow deps
                sub_manifest = await _resolve_workflow(
                    ref_id, db, _visited_workflows=_visited_workflows
                )
                manifest.merge(sub_manifest)

    return manifest


async def resolve_solution_dependencies(
    solution_type: str,
    solution_id: str,
    db: AsyncSession,
) -> DependencyManifest:
    """Resolve the full dependency tree for a Solution resource.

    Parameters
    ----------
    solution_type:
        One of ``"agent"``, ``"skill"``, ``"workflow"``.
    solution_id:
        The primary-key ID of the solution resource.
    db:
        An async database session.

    Returns
    -------
    DependencyManifest
        A deduplicated manifest of content deps (auto-subscribed) and
        connection deps (requiring credential configuration).
    """
    if solution_type == "agent":
        manifest = await _resolve_agent(solution_id, db)
    elif solution_type == "skill":
        manifest = await _resolve_skill(solution_id, db)
    elif solution_type == "workflow":
        manifest = await _resolve_workflow(solution_id, db)
    else:
        logger.warning("Unknown solution type %r for dependency resolution", solution_type)
        return DependencyManifest()

    manifest.deduplicate()
    return manifest

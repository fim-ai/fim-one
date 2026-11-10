"""Tests for the workflow import conflict resolver.

Covers: reference extraction from blueprints and classification of
resolved vs unresolved references.  DB queries are mocked to avoid
requiring a real database in unit tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.db.base import Base

from fim_one.core.workflow.types import (
    NodeType,
    WorkflowBlueprint,
    WorkflowNodeDef,
    WorkflowEdgeDef,
)
from fim_one.core.workflow.import_resolver import (
    ImportResolution,
    ResolvedReference,
    UnresolvedReference,
    _extract_references,
    resolve_blueprint_references,
)

from .assembly import matrix as m


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(node_id: str, node_type: NodeType, **data: Any) -> WorkflowNodeDef:
    return WorkflowNodeDef(
        id=node_id,
        type=node_type,
        data={"type": node_type.value, **data},
    )


def _minimal_blueprint(*extra_nodes: WorkflowNodeDef) -> WorkflowBlueprint:
    """Build a minimal valid blueprint with START -> END plus extra nodes."""
    start = _make_node("start_1", NodeType.START)
    end = _make_node("end_1", NodeType.END)
    nodes = [start, *extra_nodes, end]
    edges = [
        WorkflowEdgeDef(id="e1", source="start_1", target=extra_nodes[0].id)
        if extra_nodes
        else WorkflowEdgeDef(id="e1", source="start_1", target="end_1"),
    ]
    if extra_nodes:
        edges.append(
            WorkflowEdgeDef(
                id="e2", source=extra_nodes[-1].id, target="end_1"
            )
        )
    return WorkflowBlueprint(nodes=nodes, edges=edges)


# ---------------------------------------------------------------------------
# Reference extraction tests (pure, no DB)
# ---------------------------------------------------------------------------


class TestExtractReferences:
    def test_empty_blueprint(self) -> None:
        bp = WorkflowBlueprint(
            nodes=[
                _make_node("start_1", NodeType.START),
                _make_node("end_1", NodeType.END),
            ],
            edges=[WorkflowEdgeDef(id="e1", source="start_1", target="end_1")],
        )
        refs = _extract_references(bp)
        assert refs == []

    def test_agent_node(self) -> None:
        bp = _minimal_blueprint(
            _make_node("agent_1", NodeType.AGENT, agent_id="agt-123")
        )
        refs = _extract_references(bp)
        assert len(refs) == 1
        assert refs[0].node_id == "agent_1"
        assert refs[0].referenced_id == "agt-123"
        assert refs[0].resource_type == "agent"
        assert refs[0].field_name == "agent_id"

    def test_connector_node(self) -> None:
        bp = _minimal_blueprint(
            _make_node("conn_1", NodeType.CONNECTOR, connector_id="conn-456")
        )
        refs = _extract_references(bp)
        assert len(refs) == 1
        assert refs[0].resource_type == "connector"
        assert refs[0].referenced_id == "conn-456"

    def test_knowledge_retrieval_with_kb_ids(self) -> None:
        bp = _minimal_blueprint(
            _make_node("kr_1", NodeType.KNOWLEDGE_RETRIEVAL, kb_ids=["kb-789", "kb-790"])
        )
        refs = _extract_references(bp)
        assert [r.referenced_id for r in refs] == ["kb-789", "kb-790"]
        assert {r.field_name for r in refs} == {"kb_ids"}
        assert {r.resource_type for r in refs} == {"knowledge_base"}

    def test_mcp_node(self) -> None:
        bp = _minimal_blueprint(
            _make_node("mcp_1", NodeType.MCP, server_id="srv-xyz")
        )
        refs = _extract_references(bp)
        assert len(refs) == 1
        assert refs[0].resource_type == "mcp_server"
        assert refs[0].referenced_id == "srv-xyz"

    def test_multiple_nodes(self) -> None:
        bp = _minimal_blueprint(
            _make_node("agent_1", NodeType.AGENT, agent_id="agt-1"),
            _make_node("conn_1", NodeType.CONNECTOR, connector_id="conn-2"),
            _make_node("kr_1", NodeType.KNOWLEDGE_RETRIEVAL, kb_ids=["kb-3"]),
        )
        # Fix edges for chained nodes
        bp.edges = [
            WorkflowEdgeDef(id="e1", source="start_1", target="agent_1"),
            WorkflowEdgeDef(id="e2", source="agent_1", target="conn_1"),
            WorkflowEdgeDef(id="e3", source="conn_1", target="kr_1"),
            WorkflowEdgeDef(id="e4", source="kr_1", target="end_1"),
        ]
        refs = _extract_references(bp)
        assert len(refs) == 3
        resource_types = {r.resource_type for r in refs}
        assert resource_types == {"agent", "connector", "knowledge_base"}

    def test_ignores_empty_and_whitespace_ids(self) -> None:
        bp = _minimal_blueprint(
            _make_node("agent_1", NodeType.AGENT, agent_id=""),
            _make_node("conn_1", NodeType.CONNECTOR, connector_id="  "),
        )
        bp.edges = [
            WorkflowEdgeDef(id="e1", source="start_1", target="agent_1"),
            WorkflowEdgeDef(id="e2", source="agent_1", target="conn_1"),
            WorkflowEdgeDef(id="e3", source="conn_1", target="end_1"),
        ]
        refs = _extract_references(bp)
        assert len(refs) == 0

    def test_ignores_non_string_ids(self) -> None:
        bp = _minimal_blueprint(
            _make_node("agent_1", NodeType.AGENT, agent_id=12345),
        )
        refs = _extract_references(bp)
        assert len(refs) == 0

    def test_ignores_non_reference_nodes(self) -> None:
        bp = _minimal_blueprint(
            _make_node("llm_1", NodeType.LLM, prompt_template="hello"),
            _make_node("human_1", NodeType.HUMAN_INTERVENTION, prompt_message="ok"),
        )
        bp.edges = [
            WorkflowEdgeDef(id="e1", source="start_1", target="llm_1"),
            WorkflowEdgeDef(id="e2", source="llm_1", target="human_1"),
            WorkflowEdgeDef(id="e3", source="human_1", target="end_1"),
        ]
        refs = _extract_references(bp)
        assert len(refs) == 0


# ---------------------------------------------------------------------------
# Full resolver tests, against a seeded database
# ---------------------------------------------------------------------------


@pytest.fixture()
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'import.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


# node id -> (node type, reference field, the scene resource it names)
_STEPS: dict[str, tuple[NodeType, str, str]] = {
    "agent_1": (NodeType.AGENT, "agent_id", "agent"),
    "conn_1": (NodeType.CONNECTOR, "connector_id", "api_bearer"),
    "db_1": (NodeType.CONNECTOR, "connector_id", "db"),
    "mcp_1": (NodeType.MCP, "server_id", "mcp_http"),
    "kb_1": (NodeType.KNOWLEDGE_RETRIEVAL, "kb_ids", "kb"),
}


def _blueprint_of(scene: m.Scene) -> WorkflowBlueprint:
    """A blueprint with one step for each kind of resource in the scene."""
    return _minimal_blueprint(
        *(
            _make_node(
                node_id,
                node_type,
                **{
                    field: [scene.ids[resource]]
                    if field == "kb_ids"
                    else scene.ids[resource]
                },
            )
            for node_id, (node_type, field, resource) in _STEPS.items()
        )
    )


async def _import_as(
    factory: async_sessionmaker[AsyncSession], role: str, *, is_active: bool = True
) -> tuple[ImportResolution, m.Scene]:
    """Resolve the blueprint for the scene's actor, who would become its author."""
    scene = await m.seed_scene(
        factory, m.SceneKey(role=role, allow_fallback=True, is_active=is_active, has_personal=False)
    )
    async with factory() as db:
        resolution = await resolve_blueprint_references(_blueprint_of(scene), db, scene.actor)
    return resolution, scene


def _nodes(refs: list[Any]) -> set[str]:
    return {ref.node_id for ref in refs}


class TestResolveReferences:
    """A reference is resolved when the importer could use it in a step.

    The resolver used to carry its own copy of the visibility rule: it read
    the subscription table itself and asked only "can this user see it".
    A switched-off resource, and a database connector or knowledge base
    someone else owns, were reported as fine and then failed at the first
    run. It now asks the rule the steps ask.
    """

    async def test_no_references(self, factory: async_sessionmaker[AsyncSession]) -> None:
        """Blueprint with no external references returns empty resolution."""
        bp = WorkflowBlueprint(
            nodes=[
                _make_node("start_1", NodeType.START),
                _make_node("end_1", NodeType.END),
            ],
            edges=[WorkflowEdgeDef(id="e1", source="start_1", target="end_1")],
        )
        async with factory() as db:
            result = await resolve_blueprint_references(bp, db, "user-1")
        assert result == ImportResolution()

    async def test_the_owner_of_everything_has_nothing_unresolved(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        result, scene = await _import_as(factory, "owner")
        assert _nodes(result.resolved) == set(_STEPS)
        assert result.unresolved == []
        assert result.warnings == []
        by_node = {ref.node_id: ref for ref in result.resolved}
        assert by_node["agent_1"].resource_name == scene.names["agent"]
        assert by_node["kb_1"].field_name == "kb_ids"

    async def test_an_outsider_has_everything_unresolved(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        result, scene = await _import_as(factory, "outsider")
        assert result.resolved == []
        assert _nodes(result.unresolved) == set(_STEPS)
        assert len(result.warnings) == len(_STEPS)
        mcp = next(u for u in result.unresolved if u.node_id == "mcp_1")
        assert (mcp.referenced_id, mcp.resource_type) == (scene.ids["mcp_http"], "mcp_server")

    async def test_a_subscriber_keeps_what_is_shared_and_not_what_cannot_be(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """A database connector and a knowledge base are their owner's alone."""
        result, _ = await _import_as(factory, "subscriber")
        assert _nodes(result.resolved) == {"agent_1", "conn_1", "mcp_1"}
        assert _nodes(result.unresolved) == {"db_1", "kb_1"}
        (db_warning,) = [w for w in result.warnings if "'db_1'" in w]
        assert "can only be used by its owner" in db_warning

    async def test_a_switched_off_resource_is_unresolved_and_the_warning_says_so(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        result, scene = await _import_as(factory, "owner", is_active=False)
        assert result.resolved == []
        assert _nodes(result.unresolved) == set(_STEPS)
        (warning,) = [w for w in result.warnings if "'conn_1'" in w]
        assert warning == (
            f"Node 'conn_1' (CONNECTOR) references connector '{scene.ids['api_bearer']}' "
            f"which cannot be used: Connector '{scene.names['api_bearer']}' is disabled."
        )

    async def test_warning_message_format(self, factory: async_sessionmaker[AsyncSession]) -> None:
        """Warning messages include node ID, type, resource type, and ref ID."""
        bp = _minimal_blueprint(_make_node("mcp_1", NodeType.MCP, server_id="srv-gone"))
        async with factory() as db:
            result = await resolve_blueprint_references(bp, db, "user-1")
        assert result.warnings == [
            "Node 'mcp_1' (MCP) references mcp server 'srv-gone' "
            "which was not found or is not accessible."
        ]

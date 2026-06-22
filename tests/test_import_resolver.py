"""Tests for the workflow import conflict resolver.

Covers: reference extraction from blueprints and classification of
resolved vs unresolved references.  DB queries are mocked to avoid
requiring a real database in unit tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any

import pytest

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

    def test_knowledge_retrieval_with_kb_id(self) -> None:
        bp = _minimal_blueprint(
            _make_node("kr_1", NodeType.KNOWLEDGE_RETRIEVAL, kb_id="kb-789")
        )
        refs = _extract_references(bp)
        assert len(refs) == 1
        assert refs[0].resource_type == "knowledge_base"
        assert refs[0].field_name == "kb_id"

    def test_knowledge_retrieval_with_knowledge_base_id(self) -> None:
        bp = _minimal_blueprint(
            _make_node(
                "kr_1",
                NodeType.KNOWLEDGE_RETRIEVAL,
                knowledge_base_id="kb-abc",
            )
        )
        refs = _extract_references(bp)
        assert len(refs) == 1
        assert refs[0].resource_type == "knowledge_base"
        assert refs[0].field_name == "knowledge_base_id"

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
            _make_node("kr_1", NodeType.KNOWLEDGE_RETRIEVAL, kb_id="kb-3"),
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
# Full resolver tests (with mocked DB)
# ---------------------------------------------------------------------------


class TestResolveReferences:
    @pytest.mark.asyncio
    async def test_no_references(self) -> None:
        """Blueprint with no external references returns empty resolution."""
        bp = WorkflowBlueprint(
            nodes=[
                _make_node("start_1", NodeType.START),
                _make_node("end_1", NodeType.END),
            ],
            edges=[WorkflowEdgeDef(id="e1", source="start_1", target="end_1")],
        )
        mock_db = AsyncMock()
        result = await resolve_blueprint_references(bp, mock_db, "user-1", subscribed_ids=[])
        assert result.resolved == []
        assert result.unresolved == []
        assert result.warnings == []

    @pytest.mark.asyncio
    async def test_all_resolved(self) -> None:
        """When all referenced resources exist, they go into resolved list."""
        bp = _minimal_blueprint(
            _make_node("agent_1", NodeType.AGENT, agent_id="agt-123")
        )

        mock_db = AsyncMock()
        # Mock the execute result to return the agent as accessible
        mock_result = MagicMock()
        mock_result.all.return_value = [("agt-123", "My Agent")]
        mock_db.execute.return_value = mock_result

        with patch(
            "fim_one.web.visibility.build_visibility_filter",
            return_value=True,
        ):
            result = await resolve_blueprint_references(
                bp, mock_db, "user-1", ["org-1"]
            )

        assert len(result.resolved) == 1
        assert result.resolved[0].referenced_id == "agt-123"
        assert result.resolved[0].resource_name == "My Agent"
        assert len(result.unresolved) == 0
        assert len(result.warnings) == 0

    @pytest.mark.asyncio
    async def test_all_unresolved(self) -> None:
        """When referenced resources don't exist, they go into unresolved."""
        bp = _minimal_blueprint(
            _make_node("agent_1", NodeType.AGENT, agent_id="agt-missing")
        )

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []  # nothing found
        mock_db.execute.return_value = mock_result

        with patch(
            "fim_one.web.visibility.build_visibility_filter",
            return_value=True,
        ):
            result = await resolve_blueprint_references(
                bp, mock_db, "user-1", ["org-1"]
            )

        assert len(result.resolved) == 0
        assert len(result.unresolved) == 1
        assert result.unresolved[0].referenced_id == "agt-missing"
        assert result.unresolved[0].resource_type == "agent"
        assert len(result.warnings) == 1
        assert "agt-missing" in result.warnings[0]

    @pytest.mark.asyncio
    async def test_mixed_resolved_and_unresolved(self) -> None:
        """Some references resolve, some don't."""
        bp = _minimal_blueprint(
            _make_node("agent_1", NodeType.AGENT, agent_id="agt-found"),
            _make_node("conn_1", NodeType.CONNECTOR, connector_id="conn-gone"),
        )
        bp.edges = [
            WorkflowEdgeDef(id="e1", source="start_1", target="agent_1"),
            WorkflowEdgeDef(id="e2", source="agent_1", target="conn_1"),
            WorkflowEdgeDef(id="e3", source="conn_1", target="end_1"),
        ]

        mock_db = AsyncMock()

        # First call: agent query returns found
        agent_result = MagicMock()
        agent_result.all.return_value = [("agt-found", "Found Agent")]

        # Second call: connector query returns empty
        conn_result = MagicMock()
        conn_result.all.return_value = []

        mock_db.execute.side_effect = [agent_result, conn_result]

        with patch(
            "fim_one.web.visibility.build_visibility_filter",
            return_value=True,
        ):
            result = await resolve_blueprint_references(
                bp, mock_db, "user-1", ["org-1"], subscribed_ids=[]
            )

        assert len(result.resolved) == 1
        assert result.resolved[0].referenced_id == "agt-found"
        assert len(result.unresolved) == 1
        assert result.unresolved[0].referenced_id == "conn-gone"
        assert len(result.warnings) == 1

    @pytest.mark.asyncio
    async def test_default_empty_org_ids(self) -> None:
        """When user_org_ids is None, defaults to empty list."""
        bp = _minimal_blueprint(
            _make_node("agent_1", NodeType.AGENT, agent_id="agt-1")
        )

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_db.execute.return_value = mock_result

        with patch(
            "fim_one.web.visibility.build_visibility_filter",
            return_value=True,
        ):
            result = await resolve_blueprint_references(
                bp, mock_db, "user-1", None
            )

        assert len(result.unresolved) == 1

    @pytest.mark.asyncio
    async def test_warning_message_format(self) -> None:
        """Warning messages include node ID, type, resource type, and ref ID."""
        bp = _minimal_blueprint(
            _make_node("mcp_1", NodeType.MCP, server_id="srv-gone")
        )

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_db.execute.return_value = mock_result

        with patch(
            "fim_one.web.visibility.build_visibility_filter",
            return_value=True,
        ):
            result = await resolve_blueprint_references(
                bp, mock_db, "user-1"
            )

        assert len(result.warnings) == 1
        w = result.warnings[0]
        assert "mcp_1" in w
        assert "MCP" in w
        assert "mcp server" in w
        assert "srv-gone" in w

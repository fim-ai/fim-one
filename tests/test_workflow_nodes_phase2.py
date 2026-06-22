"""Tests for kept Phase 2/3 workflow node executors.

Covers HumanInterventionExecutor and MCPExecutor. (The remaining Phase 2/3
node executors were removed in the node-type reduction.)
"""

from __future__ import annotations

import pytest
from typing import Any

from fim_one.core.workflow.nodes import (
    HumanInterventionExecutor,
    MCPExecutor,
)
from fim_one.core.workflow.types import (
    ExecutionContext,
    NodeStatus,
    NodeType,
    WorkflowNodeDef,
)
from fim_one.core.workflow.variable_store import VariableStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    node_id: str,
    node_type: NodeType,
    data: dict[str, Any] | None = None,
) -> WorkflowNodeDef:
    """Create a WorkflowNodeDef with the given id, type, and data."""
    return WorkflowNodeDef(
        id=node_id,
        type=node_type,
        data=data or {},
    )


def _make_ctx(**overrides: Any) -> ExecutionContext:
    """Create an ExecutionContext with sensible defaults."""
    defaults = {
        "run_id": "test-run-001",
        "user_id": "test-user-001",
        "workflow_id": "test-workflow-001",
        "env_vars": {},
    }
    defaults.update(overrides)
    return ExecutionContext(**defaults)



class TestHumanInterventionExecutor:
    """Tests for HumanInterventionExecutor — pauses workflow for human approval."""

    @pytest.mark.asyncio
    async def test_auto_approve_happy_path(self):
        """Human intervention auto-approves and stores result."""
        executor = HumanInterventionExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("human_1", NodeType.HUMAN_INTERVENTION, {
            "prompt_message": "Please review this data.",
            "assignee": "admin@example.com",
            "timeout_hours": 48,
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        output = result.output
        assert output["status"] == "approved"
        assert output["assignee"] == "admin@example.com"
        assert output["timeout_hours"] == 48
        assert "review this data" in output["message"]

    @pytest.mark.asyncio
    async def test_default_prompt_message(self):
        """Human intervention uses default prompt when none is provided."""
        executor = HumanInterventionExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("human_1", NodeType.HUMAN_INTERVENTION, {})

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert "Please review and approve" in result.output["message"]

    @pytest.mark.asyncio
    async def test_stores_in_all_locations(self):
        """Human intervention stores result in output_variable, node.output, and node.output_variable."""
        executor = HumanInterventionExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("human_1", NodeType.HUMAN_INTERVENTION, {
            "output_variable": "my_approval",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert await store.get("my_approval") is not None
        assert await store.get("human_1.output") is not None
        assert await store.get("human_1.my_approval") is not None

    @pytest.mark.asyncio
    async def test_prompt_interpolation(self):
        """Human intervention interpolates {{}} variables in prompt_message."""
        executor = HumanInterventionExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("order.id", "ORD-123")

        node = _make_node("human_1", NodeType.HUMAN_INTERVENTION, {
            "prompt_message": "Review order {{order.id}}",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert "ORD-123" in result.output["message"]

    @pytest.mark.asyncio
    async def test_default_output_variable(self):
        """Human intervention uses 'approval_result' as default output_variable."""
        executor = HumanInterventionExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("human_1", NodeType.HUMAN_INTERVENTION, {})

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert await store.get("approval_result") is not None



class TestMCPExecutor:
    """Tests for MCPExecutor — basic validation (real implementation).

    Comprehensive tests with DB/MCP mocking are in
    tests/test_workflow_mcp_executor.py.
    """

    @pytest.mark.asyncio
    async def test_missing_server_id_fails(self):
        """MCP executor fails when server_id is missing."""
        executor = MCPExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("mcp_1", NodeType.MCP, {
            "tool_name": "search",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "server_id" in result.error

    @pytest.mark.asyncio
    async def test_missing_tool_name_fails(self):
        """MCP executor fails when tool_name is missing."""
        executor = MCPExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("mcp_1", NodeType.MCP, {
            "server_id": "server-abc",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "tool_name" in result.error

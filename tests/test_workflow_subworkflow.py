"""Tests for the SubWorkflow executor — nested workflow execution."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.core.workflow.nodes import SubWorkflowExecutor
from fim_one.core.workflow.types import (
    ExecutionContext,
    NodeResult,
    NodeStatus,
    NodeType,
    WorkflowNodeDef,
)
from fim_one.core.workflow.variable_store import VariableStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    node_id: str = "sub-1",
    data: dict[str, Any] | None = None,
) -> WorkflowNodeDef:
    """Create a SUB_WORKFLOW node definition for testing."""
    return WorkflowNodeDef(
        id=node_id,
        type=NodeType.SUB_WORKFLOW,
        data=data or {},
    )


def _make_context(
    depth: int = 0,
    db_session_factory: Any = None,
) -> ExecutionContext:
    """Create a minimal execution context."""
    return ExecutionContext(
        run_id="test-run",
        user_id="user-1",
        workflow_id="parent-wf",
        env_vars={"API_KEY": "secret"},
        db_session_factory=db_session_factory,
        depth=depth,
    )


def _make_mock_session_factory(workflow_obj: Any = None):
    """Create a mock session factory that returns a given workflow object."""

    @asynccontextmanager
    async def _session_ctx():
        session = AsyncMock()
        # Build mock result
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = workflow_obj
        session.execute = AsyncMock(return_value=mock_result)
        yield session

    return _session_ctx


def _make_mock_workflow(
    workflow_id: str = "child-wf",
    is_active: bool = True,
    blueprint: dict[str, Any] | None = None,
) -> MagicMock:
    """Create a mock Workflow ORM object."""
    wf = MagicMock()
    wf.id = workflow_id
    wf.is_active = is_active
    wf.blueprint = blueprint or {
        "nodes": [
            {"id": "start-1", "data": {"type": "START"}},
            {"id": "end-1", "data": {"type": "END"}},
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "end-1"},
        ],
    }
    return wf


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSubWorkflowDepthLimit:
    """SubWorkflow must refuse execution beyond MAX_DEPTH."""

    @pytest.mark.asyncio
    async def test_depth_limit_exceeded(self):
        executor = SubWorkflowExecutor()
        node = _make_node(data={"workflow_id": "child-wf"})
        store = VariableStore()
        ctx = _make_context(depth=5)  # MAX_DEPTH == 5

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "depth" in result.error.lower()
        assert result.node_id == "sub-1"

    @pytest.mark.asyncio
    async def test_depth_at_boundary(self):
        """Exactly at MAX_DEPTH should fail."""
        executor = SubWorkflowExecutor()
        node = _make_node(data={"workflow_id": "child-wf"})
        store = VariableStore()
        ctx = _make_context(depth=SubWorkflowExecutor.MAX_DEPTH)

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "depth" in result.error.lower()


class TestSubWorkflowMissingDbFactory:
    """SubWorkflow must fail clearly when db_session_factory is None."""

    @pytest.mark.asyncio
    async def test_no_db_session_factory(self):
        executor = SubWorkflowExecutor()
        node = _make_node(data={"workflow_id": "child-wf"})
        store = VariableStore()
        ctx = _make_context(db_session_factory=None)

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "db_session_factory" in result.error


class TestSubWorkflowMissingWorkflowId:
    """SubWorkflow must fail when workflow_id is empty or missing."""

    @pytest.mark.asyncio
    async def test_empty_workflow_id(self):
        executor = SubWorkflowExecutor()
        factory = _make_mock_session_factory()
        node = _make_node(data={"workflow_id": ""})
        store = VariableStore()
        ctx = _make_context(db_session_factory=factory)

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "workflow_id" in result.error.lower()

    @pytest.mark.asyncio
    async def test_missing_workflow_id_key(self):
        executor = SubWorkflowExecutor()
        factory = _make_mock_session_factory()
        node = _make_node(data={})
        store = VariableStore()
        ctx = _make_context(db_session_factory=factory)

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "workflow_id" in result.error.lower()


class TestSubWorkflowNotFound:
    """SubWorkflow must fail when the target workflow doesn't exist in DB."""

    @pytest.mark.asyncio
    async def test_workflow_not_found(self):
        executor = SubWorkflowExecutor()
        factory = _make_mock_session_factory(workflow_obj=None)
        node = _make_node(data={"workflow_id": "nonexistent-wf"})
        store = VariableStore()
        ctx = _make_context(db_session_factory=factory)

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "not found" in result.error.lower()


class TestSubWorkflowInputMapping:
    """SubWorkflow must resolve input_mapping templates against the parent store."""

    @pytest.mark.asyncio
    async def test_input_mapping_resolves_variables(self):
        """Verify that {{var}} placeholders are interpolated from the parent store."""
        executor = SubWorkflowExecutor()
        mock_wf = _make_mock_workflow()
        factory = _make_mock_session_factory(workflow_obj=mock_wf)

        node = _make_node(data={
            "workflow_id": "child-wf",
            "input_mapping": {
                "name": "{{start_1.user_name}}",
                "static": "literal_value",
            },
        })
        store = VariableStore()
        await store.set("start_1.user_name", "Alice")

        ctx = _make_context(db_session_factory=factory)

        # Mock the engine's execute_streaming to verify inputs and return success
        async def mock_streaming(blueprint, inputs, context=None):
            # Verify inputs were properly resolved
            assert inputs["name"] == "Alice"
            assert inputs["static"] == "literal_value"
            yield "run_completed", {"status": "completed", "outputs": {"result": "ok"}}

        with patch(
            "fim_one.core.workflow.engine.WorkflowEngine"
        ) as MockEngine:
            instance = MockEngine.return_value
            instance.execute_streaming = mock_streaming
            result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == {"result": "ok"}


class TestSubWorkflowSuccessfulExecution:
    """SubWorkflow must run the child workflow and store its outputs."""

    @pytest.mark.asyncio
    async def test_successful_execution(self):
        executor = SubWorkflowExecutor()
        mock_wf = _make_mock_workflow()
        factory = _make_mock_session_factory(workflow_obj=mock_wf)

        node = _make_node(data={
            "workflow_id": "child-wf",
            "output_variable": "my_output",
        })
        store = VariableStore()
        ctx = _make_context(db_session_factory=factory)

        child_outputs = {"summary": "All done", "count": 42}

        async def mock_streaming(blueprint, inputs, context=None):
            yield "node_started", {"node_id": "start-1"}
            yield "node_completed", {"node_id": "start-1"}
            yield "run_completed", {
                "status": "completed",
                "outputs": child_outputs,
            }

        with patch(
            "fim_one.core.workflow.engine.WorkflowEngine"
        ) as MockEngine:
            instance = MockEngine.return_value
            instance.execute_streaming = mock_streaming
            result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == child_outputs

        # Verify store was updated with both output_variable and .output
        stored_custom = await store.get("sub-1.my_output")
        stored_output = await store.get("sub-1.output")
        assert stored_custom == child_outputs
        assert stored_output == child_outputs

    @pytest.mark.asyncio
    async def test_default_output_variable(self):
        """When output_variable is not specified, defaults to 'sub_result'."""
        executor = SubWorkflowExecutor()
        mock_wf = _make_mock_workflow()
        factory = _make_mock_session_factory(workflow_obj=mock_wf)

        node = _make_node(data={"workflow_id": "child-wf"})
        store = VariableStore()
        ctx = _make_context(db_session_factory=factory)

        async def mock_streaming(blueprint, inputs, context=None):
            yield "run_completed", {
                "status": "completed",
                "outputs": {"val": 1},
            }

        with patch(
            "fim_one.core.workflow.engine.WorkflowEngine"
        ) as MockEngine:
            instance = MockEngine.return_value
            instance.execute_streaming = mock_streaming
            result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        stored = await store.get("sub-1.sub_result")
        assert stored == {"val": 1}


class TestSubWorkflowFailedExecution:
    """SubWorkflow must propagate child failures."""

    @pytest.mark.asyncio
    async def test_child_workflow_failure(self):
        executor = SubWorkflowExecutor()
        mock_wf = _make_mock_workflow()
        factory = _make_mock_session_factory(workflow_obj=mock_wf)

        node = _make_node(data={"workflow_id": "child-wf"})
        store = VariableStore()
        ctx = _make_context(db_session_factory=factory)

        async def mock_streaming(blueprint, inputs, context=None):
            yield "node_failed", {"node_id": "llm-1", "error": "LLM timeout"}
            yield "run_failed", {
                "status": "failed",
                "error": "Nodes failed: ['llm-1']",
            }

        with patch(
            "fim_one.core.workflow.engine.WorkflowEngine"
        ) as MockEngine:
            instance = MockEngine.return_value
            instance.execute_streaming = mock_streaming
            result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "Sub-workflow execution failed" in result.error


class TestSubWorkflowContextPropagation:
    """SubWorkflow must create a sub-context with incremented depth."""

    @pytest.mark.asyncio
    async def test_depth_incremented(self):
        executor = SubWorkflowExecutor()
        mock_wf = _make_mock_workflow()
        factory = _make_mock_session_factory(workflow_obj=mock_wf)

        node = _make_node(data={"workflow_id": "child-wf"})
        store = VariableStore()
        ctx = _make_context(depth=2, db_session_factory=factory)

        captured_context = {}

        async def mock_streaming(blueprint, inputs, context=None):
            if context is not None:
                captured_context["depth"] = context.depth
                captured_context["db_session_factory"] = context.db_session_factory
                captured_context["user_id"] = context.user_id
                captured_context["run_id"] = context.run_id
            yield "run_completed", {"status": "completed", "outputs": {}}

        with patch(
            "fim_one.core.workflow.engine.WorkflowEngine"
        ) as MockEngine:
            instance = MockEngine.return_value
            instance.execute_streaming = mock_streaming
            await executor.execute(node, store, ctx)

        assert captured_context["depth"] == 3  # parent depth 2 + 1
        assert captured_context["db_session_factory"] is factory
        assert captured_context["user_id"] == "user-1"
        assert "sub:sub-1" in captured_context["run_id"]


class TestSubWorkflowDurationTracking:
    """SubWorkflow must track execution duration."""

    @pytest.mark.asyncio
    async def test_duration_recorded(self):
        executor = SubWorkflowExecutor()
        mock_wf = _make_mock_workflow()
        factory = _make_mock_session_factory(workflow_obj=mock_wf)

        node = _make_node(data={"workflow_id": "child-wf"})
        store = VariableStore()
        ctx = _make_context(db_session_factory=factory)

        async def mock_streaming(blueprint, inputs, context=None):
            yield "run_completed", {"status": "completed", "outputs": {}}

        with patch(
            "fim_one.core.workflow.engine.WorkflowEngine"
        ) as MockEngine:
            instance = MockEngine.return_value
            instance.execute_streaming = mock_streaming
            result = await executor.execute(node, store, ctx)

        assert result.duration_ms >= 0


class TestSubWorkflowRegistry:
    """SubWorkflow executor must be registered in the global registry."""

    def test_registered_in_registry(self):
        from fim_one.core.workflow.nodes import EXECUTOR_REGISTRY

        assert NodeType.SUB_WORKFLOW in EXECUTOR_REGISTRY
        assert EXECUTOR_REGISTRY[NodeType.SUB_WORKFLOW] is SubWorkflowExecutor

    def test_get_executor_returns_instance(self):
        from fim_one.core.workflow.nodes import get_executor

        executor = get_executor(NodeType.SUB_WORKFLOW)
        assert isinstance(executor, SubWorkflowExecutor)


class TestExecutionContextDefaults:
    """ExecutionContext new fields must have safe defaults."""

    def test_default_depth_is_zero(self):
        ctx = ExecutionContext(
            run_id="r", user_id="u", workflow_id="w"
        )
        assert ctx.depth == 0

    def test_default_db_session_factory_is_none(self):
        ctx = ExecutionContext(
            run_id="r", user_id="u", workflow_id="w"
        )
        assert ctx.db_session_factory is None

    def test_existing_fields_unaffected(self):
        ctx = ExecutionContext(
            run_id="r",
            user_id="u",
            workflow_id="w",
            env_vars={"K": "V"},
        )
        assert ctx.run_id == "r"
        assert ctx.user_id == "u"
        assert ctx.workflow_id == "w"
        assert ctx.env_vars == {"K": "V"}

"""Tests for the BuiltinToolExecutor workflow node."""

from __future__ import annotations

import pytest

from fim_one.core.tool.base import BaseTool, ToolResult
from fim_one.core.tool.registry import ToolRegistry
from fim_one.core.workflow.nodes import BuiltinToolExecutor
from fim_one.core.workflow.types import (
    ExecutionContext,
    NodeStatus,
    NodeType,
    WorkflowNodeDef,
)
from fim_one.core.workflow.variable_store import VariableStore
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeAddTool(BaseTool):
    """A simple mock tool that adds two numbers and returns the sum."""

    @property
    def name(self) -> str:
        return "add_numbers"

    @property
    def description(self) -> str:
        return "Add two numbers together."

    @property
    def category(self) -> str:
        return "test"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        }

    async def run(self, **kwargs: Any) -> str:
        a = float(kwargs.get("a", 0))
        b = float(kwargs.get("b", 0))
        return str(a + b)


class FakeRichTool(BaseTool):
    """A mock tool that returns a ToolResult instead of a plain string."""

    @property
    def name(self) -> str:
        return "rich_tool"

    @property
    def description(self) -> str:
        return "Returns a ToolResult."

    @property
    def category(self) -> str:
        return "test"

    async def run(self, **kwargs: Any) -> ToolResult:
        return ToolResult(
            content=f"rich output: {kwargs.get('input', '')}",
            content_type="text",
        )


class FakeErrorTool(BaseTool):
    """A mock tool that always raises an exception."""

    @property
    def name(self) -> str:
        return "error_tool"

    @property
    def description(self) -> str:
        return "Always fails."

    @property
    def category(self) -> str:
        return "test"

    async def run(self, **kwargs: Any) -> str:
        raise RuntimeError("Something went wrong inside the tool")


def _make_registry(*tools: BaseTool) -> ToolRegistry:
    """Build a ToolRegistry from the given tool instances."""
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def _make_context() -> ExecutionContext:
    return ExecutionContext(
        run_id="run-1",
        user_id="user-1",
        workflow_id="wf-1",
    )


def _make_node(
    tool_id: str,
    parameters: dict[str, Any] | None = None,
    output_variable: str = "",
    node_id: str = "node-bt-1",
) -> WorkflowNodeDef:
    data: dict[str, Any] = {"tool_id": tool_id}
    if parameters is not None:
        data["parameters"] = parameters
    if output_variable:
        data["output_variable"] = output_variable
    return WorkflowNodeDef(id=node_id, type=NodeType.BUILTIN_TOOL, data=data)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_execution() -> None:
    """Tool executes successfully and output is stored."""
    registry = _make_registry(FakeAddTool())
    store = VariableStore()
    executor = BuiltinToolExecutor(registry=registry)
    node = _make_node("add_numbers", parameters={"a": 3, "b": 7})

    result = await executor.execute(node, store, _make_context())

    assert result.status == NodeStatus.COMPLETED
    assert result.error is None
    # The tool returns structured dict with result "10.0"
    assert isinstance(result.output, dict)
    assert result.output["tool_id"] == "add_numbers"
    assert result.output["status"] == "completed"
    assert "10" in result.output["result"]

    # Verify the value was stored under the standard key (now a dict)
    stored = await store.get("node-bt-1.output")
    assert stored["result"] == "10.0"


@pytest.mark.asyncio
async def test_output_variable_stored() -> None:
    """When output_variable is specified, result is also stored under that key."""
    registry = _make_registry(FakeAddTool())
    store = VariableStore()
    executor = BuiltinToolExecutor(registry=registry)
    node = _make_node("add_numbers", parameters={"a": 1, "b": 2}, output_variable="my_sum")

    result = await executor.execute(node, store, _make_context())

    assert result.status == NodeStatus.COMPLETED
    # Standard key — now a structured dict
    stored = await store.get("node-bt-1.output")
    assert stored["result"] == "3.0"
    # User-defined variable — same structured dict
    user_var = await store.get("my_sum")
    assert user_var["result"] == "3.0"


@pytest.mark.asyncio
async def test_parameter_interpolation() -> None:
    """Parameters with {{var}} placeholders are interpolated from the store."""
    registry = _make_registry(FakeAddTool())
    store = VariableStore()
    await store.set("x_val", "5")
    await store.set("y_val", "15")
    executor = BuiltinToolExecutor(registry=registry)
    node = _make_node("add_numbers", parameters={"a": "{{x_val}}", "b": "{{y_val}}"})

    result = await executor.execute(node, store, _make_context())

    assert result.status == NodeStatus.COMPLETED
    assert "20" in result.output["result"]


@pytest.mark.asyncio
async def test_tool_not_found() -> None:
    """An error is returned when the tool_id does not exist in the registry."""
    registry = _make_registry(FakeAddTool())
    store = VariableStore()
    executor = BuiltinToolExecutor(registry=registry)
    node = _make_node("nonexistent_tool")

    result = await executor.execute(node, store, _make_context())

    assert result.status == NodeStatus.FAILED
    assert "not found" in (result.error or "").lower()
    assert "nonexistent_tool" in (result.error or "")


@pytest.mark.asyncio
async def test_tool_not_found_lists_available() -> None:
    """Error message for tool-not-found includes the available tool names."""
    registry = _make_registry(FakeAddTool(), FakeRichTool())
    store = VariableStore()
    executor = BuiltinToolExecutor(registry=registry)
    node = _make_node("missing_tool")

    result = await executor.execute(node, store, _make_context())

    assert result.status == NodeStatus.FAILED
    assert "add_numbers" in (result.error or "")
    assert "rich_tool" in (result.error or "")


@pytest.mark.asyncio
async def test_missing_tool_id() -> None:
    """An error is returned when tool_id is empty/missing."""
    registry = _make_registry()
    store = VariableStore()
    executor = BuiltinToolExecutor(registry=registry)
    node = WorkflowNodeDef(
        id="node-no-id",
        type=NodeType.BUILTIN_TOOL,
        data={},  # no tool_id
    )

    result = await executor.execute(node, store, _make_context())

    assert result.status == NodeStatus.FAILED
    assert "no tool_id" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_tool_execution_error() -> None:
    """An error is returned when the tool's run() raises."""
    registry = _make_registry(FakeErrorTool())
    store = VariableStore()
    executor = BuiltinToolExecutor(registry=registry)
    node = _make_node("error_tool")

    result = await executor.execute(node, store, _make_context())

    assert result.status == NodeStatus.FAILED
    assert "Something went wrong" in (result.error or "")


@pytest.mark.asyncio
async def test_rich_tool_result() -> None:
    """Tools returning a ToolResult have their .content extracted."""
    registry = _make_registry(FakeRichTool())
    store = VariableStore()
    executor = BuiltinToolExecutor(registry=registry)
    node = _make_node("rich_tool", parameters={"input": "hello"})

    result = await executor.execute(node, store, _make_context())

    assert result.status == NodeStatus.COMPLETED
    stored = await store.get("node-bt-1.output")
    assert stored["result"] == "rich output: hello"


@pytest.mark.asyncio
async def test_empty_parameters() -> None:
    """Tool runs with no parameters when none are provided."""
    registry = _make_registry(FakeAddTool())
    store = VariableStore()
    executor = BuiltinToolExecutor(registry=registry)
    node = _make_node("add_numbers")  # no parameters key

    result = await executor.execute(node, store, _make_context())

    assert result.status == NodeStatus.COMPLETED
    # Default values: a=0, b=0 => "0.0"
    stored = await store.get("node-bt-1.output")
    assert stored["result"] == "0.0"


@pytest.mark.asyncio
async def test_non_string_parameters_passed_through() -> None:
    """Non-string parameter values are passed as-is without interpolation."""
    registry = _make_registry(FakeAddTool())
    store = VariableStore()
    executor = BuiltinToolExecutor(registry=registry)
    node = _make_node("add_numbers", parameters={"a": 42, "b": 8})

    result = await executor.execute(node, store, _make_context())

    assert result.status == NodeStatus.COMPLETED
    stored = await store.get("node-bt-1.output")
    assert stored["result"] == "50.0"


@pytest.mark.asyncio
async def test_executor_in_registry() -> None:
    """BuiltinToolExecutor is registered in the EXECUTOR_REGISTRY for BUILTIN_TOOL."""
    from fim_one.core.workflow.nodes import EXECUTOR_REGISTRY

    assert NodeType.BUILTIN_TOOL in EXECUTOR_REGISTRY
    assert EXECUTOR_REGISTRY[NodeType.BUILTIN_TOOL] is BuiltinToolExecutor


@pytest.mark.asyncio
async def test_get_executor_returns_builtin_tool_executor() -> None:
    """get_executor returns a BuiltinToolExecutor instance for BUILTIN_TOOL."""
    from fim_one.core.workflow.nodes import get_executor

    executor = get_executor(NodeType.BUILTIN_TOOL)
    assert isinstance(executor, BuiltinToolExecutor)


@pytest.mark.asyncio
async def test_partial_interpolation() -> None:
    """Only parameters with {{}} are interpolated; others are left alone."""
    registry = _make_registry(FakeAddTool())
    store = VariableStore()
    await store.set("val_a", "100")
    executor = BuiltinToolExecutor(registry=registry)
    node = _make_node("add_numbers", parameters={"a": "{{val_a}}", "b": 25})

    result = await executor.execute(node, store, _make_context())

    assert result.status == NodeStatus.COMPLETED
    stored = await store.get("node-bt-1.output")
    assert stored["result"] == "125.0"


@pytest.mark.asyncio
async def test_duration_is_tracked() -> None:
    """NodeResult has a non-negative duration_ms."""
    registry = _make_registry(FakeAddTool())
    store = VariableStore()
    executor = BuiltinToolExecutor(registry=registry)
    node = _make_node("add_numbers", parameters={"a": 1, "b": 1})

    result = await executor.execute(node, store, _make_context())

    assert result.status == NodeStatus.COMPLETED
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_long_output_stored_in_full() -> None:
    """Long tool output is stored in full inside the structured dict."""

    class LongOutputTool(BaseTool):
        @property
        def name(self) -> str:
            return "long_output"

        @property
        def description(self) -> str:
            return "Returns a very long string."

        @property
        def category(self) -> str:
            return "test"

        async def run(self, **kwargs: Any) -> str:
            return "x" * 1000

    registry = _make_registry(LongOutputTool())
    store = VariableStore()
    executor = BuiltinToolExecutor(registry=registry)
    node = _make_node("long_output")

    result = await executor.execute(node, store, _make_context())

    assert result.status == NodeStatus.COMPLETED
    # NodeResult.output is now a structured dict
    assert isinstance(result.output, dict)
    assert len(result.output["result"]) == 1000
    # The full output is also stored in the variable store as a dict
    stored = await store.get("node-bt-1.output")
    assert len(stored["result"]) == 1000

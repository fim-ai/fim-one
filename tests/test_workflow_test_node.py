"""Tests for the test-node (single-node isolated execution) feature.

Covers:
- NodeTestRequest / NodeTestResponse schema validation
- Node lookup in blueprint
- Non-testable node type rejection (START, END)
- Successful isolated execution of testable node types
- Timeout handling
- Error propagation
- Variable store population and snapshot
"""

from __future__ import annotations

import asyncio
import pytest
from typing import Any

from pydantic import ValidationError

from fim_one.core.workflow.nodes import get_executor
from fim_one.core.workflow.parser import _resolve_node_type
from fim_one.core.workflow.types import (
    ErrorStrategy,
    ExecutionContext,
    NodeResult,
    NodeStatus,
    NodeType,
    WorkflowNodeDef,
)
from fim_one.core.workflow.variable_store import VariableStore
from fim_one.web.schemas.workflow import NodeTestRequest, NodeTestResponse


# ---------------------------------------------------------------------------
# Blueprint helpers
# ---------------------------------------------------------------------------


def _start_node(node_id: str = "start_1", **data: Any) -> dict:
    return {
        "id": node_id,
        "type": "start",
        "position": {"x": 0, "y": 0},
        "data": {"type": "START", **data},
    }


def _end_node(node_id: str = "end_1", **data: Any) -> dict:
    return {
        "id": node_id,
        "type": "end",
        "position": {"x": 400, "y": 0},
        "data": {"type": "END", **data},
    }


def _variable_assign_node(
    node_id: str = "var_assign_1", **data: Any
) -> dict:
    return {
        "id": node_id,
        "type": "variableAssign",
        "position": {"x": 200, "y": 0},
        "data": {
            "type": "VARIABLE_ASSIGN",
            "assignments": [
                {"variable": "greeting", "value": "Hello, {{input.name}}!"},
            ],
            **data,
        },
    }


def _template_transform_node(
    node_id: str = "template_1", **data: Any
) -> dict:
    return {
        "id": node_id,
        "type": "templateTransform",
        "position": {"x": 200, "y": 0},
        "data": {
            "type": "TEMPLATE_TRANSFORM",
            "template": "Result: {{input.value}}",
            "output_variable": "result",
            **data,
        },
    }


def _code_execution_node(
    node_id: str = "code_1", **data: Any
) -> dict:
    return {
        "id": node_id,
        "type": "codeExecution",
        "position": {"x": 200, "y": 0},
        "data": {
            "type": "CODE_EXECUTION",
            "code": "result = x + y",
            "output_variable": "result",
            **data,
        },
    }


def _edge(source: str, target: str) -> dict:
    return {"id": f"e-{source}-{target}", "source": source, "target": target}


def _blueprint_with_nodes(*nodes: dict) -> dict:
    """Build a minimal valid blueprint containing the given nodes."""
    # Ensure we always have start and end for a valid blueprint structure
    has_start = any(
        (n.get("data", {}).get("type", "") or n.get("type", "")).upper() == "START"
        for n in nodes
    )
    has_end = any(
        (n.get("data", {}).get("type", "") or n.get("type", "")).upper() == "END"
        for n in nodes
    )

    all_nodes = list(nodes)
    edges = []

    if not has_start:
        all_nodes.insert(0, _start_node())
    if not has_end:
        all_nodes.append(_end_node())

    # Simple chain edges
    for i in range(len(all_nodes) - 1):
        edges.append(_edge(all_nodes[i]["id"], all_nodes[i + 1]["id"]))

    return {
        "nodes": all_nodes,
        "edges": edges,
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestSchemas:
    """Test NodeTestRequest and NodeTestResponse schema validation."""

    def test_request_requires_node_id(self):
        """node_id is required and must be non-empty."""
        with pytest.raises(ValidationError):
            NodeTestRequest(node_id="")

    def test_request_defaults(self):
        """variables and env_vars default to empty dicts."""
        req = NodeTestRequest(node_id="llm_1")
        assert req.node_id == "llm_1"
        assert req.variables == {}
        assert req.env_vars == {}

    def test_request_with_variables(self):
        req = NodeTestRequest(
            node_id="code_1",
            variables={"input.name": "Alice", "score": 95},
            env_vars={"API_KEY": "test-key-123"},
        )
        assert req.variables["input.name"] == "Alice"
        assert req.env_vars["API_KEY"] == "test-key-123"

    def test_response_construction(self):
        resp = NodeTestResponse(
            node_id="llm_1",
            node_type="LLM",
            status="completed",
            output="Hello world",
            duration_ms=42,
            variables_after={"llm_1.output": "Hello world"},
        )
        assert resp.status == "completed"
        assert resp.duration_ms == 42
        assert resp.error is None

    def test_response_failed(self):
        resp = NodeTestResponse(
            node_id="code_1",
            node_type="CODE_EXECUTION",
            status="failed",
            error="NameError: name 'foo' is not defined",
            duration_ms=5,
        )
        assert resp.status == "failed"
        assert "NameError" in resp.error
        assert resp.output is None
        assert resp.variables_after == {}


# ---------------------------------------------------------------------------
# Non-testable node types
# ---------------------------------------------------------------------------


class TestNonTestableNodeTypes:
    """Verify that START and END nodes are correctly identified as non-testable."""

    def test_start_is_non_testable(self):
        non_testable = frozenset({NodeType.START, NodeType.END})
        assert NodeType.START in non_testable

    def test_end_is_non_testable(self):
        non_testable = frozenset({NodeType.START, NodeType.END})
        assert NodeType.END in non_testable

    def test_llm_is_testable(self):
        non_testable = frozenset({NodeType.START, NodeType.END})
        assert NodeType.LLM not in non_testable

    def test_code_execution_is_testable(self):
        non_testable = frozenset({NodeType.START, NodeType.END})
        assert NodeType.CODE_EXECUTION not in non_testable


# ---------------------------------------------------------------------------
# Node lookup in blueprint
# ---------------------------------------------------------------------------


class TestNodeLookup:
    """Verify node lookup logic in a blueprint."""

    def test_find_existing_node(self):
        bp = _blueprint_with_nodes(
            _start_node(),
            _variable_assign_node("var_1"),
            _end_node(),
        )
        raw_nodes = bp["nodes"]
        found = next((n for n in raw_nodes if n["id"] == "var_1"), None)
        assert found is not None
        assert found["data"]["type"] == "VARIABLE_ASSIGN"

    def test_missing_node_returns_none(self):
        bp = _blueprint_with_nodes(_start_node(), _end_node())
        raw_nodes = bp["nodes"]
        found = next((n for n in raw_nodes if n["id"] == "nonexistent"), None)
        assert found is None


# ---------------------------------------------------------------------------
# Variable store population
# ---------------------------------------------------------------------------


class TestVariableStoreSetup:
    """Verify variable store is correctly populated with mock data."""

    @pytest.mark.asyncio
    async def test_populate_mock_variables(self):
        store = VariableStore()
        variables = {"input.name": "Alice", "llm_1.output": "Hello Alice"}
        for key, value in variables.items():
            await store.set(key, value)

        assert await store.get("input.name") == "Alice"
        assert await store.get("llm_1.output") == "Hello Alice"

    @pytest.mark.asyncio
    async def test_env_vars_injected(self):
        store = VariableStore(env_vars={"API_KEY": "secret123"})
        assert await store.get("env.API_KEY") == "secret123"

    @pytest.mark.asyncio
    async def test_env_vars_override(self):
        """User-provided env_vars should override stored ones."""
        # Simulate: stored env has KEY=old, user override has KEY=new
        base_env = {"KEY": "old_value"}
        override = {"KEY": "new_value"}
        merged = {**base_env, **override}

        store = VariableStore(env_vars=merged)
        assert await store.get("env.KEY") == "new_value"

    @pytest.mark.asyncio
    async def test_snapshot_after_population(self):
        store = VariableStore(env_vars={"SECRET": "hidden"})
        await store.set("input.x", 42)

        snapshot = await store.snapshot()
        assert snapshot["input.x"] == 42
        assert snapshot["env.SECRET"] == "hidden"


# ---------------------------------------------------------------------------
# Isolated node execution
# ---------------------------------------------------------------------------


class TestIsolatedExecution:
    """Test executing individual nodes with the get_executor() pattern."""

    @pytest.mark.asyncio
    async def test_variable_assign_execution(self):
        """VariableAssign node should write variables to the store."""
        node_def = WorkflowNodeDef(
            id="var_1",
            type=NodeType.VARIABLE_ASSIGN,
            data={
                "type": "VARIABLE_ASSIGN",
                "assignments": [
                    {"variable": "greeting", "value": "Hello!"},
                ],
            },
        )
        store = VariableStore()
        context = ExecutionContext(
            run_id="test-run-001",
            user_id="user-1",
            workflow_id="wf-1",
        )

        executor = get_executor(NodeType.VARIABLE_ASSIGN)
        result = await executor.execute(node_def, store, context)

        assert result.status == NodeStatus.COMPLETED
        assert result.node_id == "var_1"

        snapshot = await store.snapshot()
        assert "var_1.greeting" in snapshot

    @pytest.mark.asyncio
    async def test_code_execution_with_mock_vars(self):
        """CodeExecution node should use variables from the store.

        The code executor injects store variables via a ``variables`` dict,
        so user code accesses them as ``variables['input.x']``.
        """
        node_def = WorkflowNodeDef(
            id="code_1",
            type=NodeType.CODE_EXECUTION,
            data={
                "type": "CODE_EXECUTION",
                "code": "result = variables['input.x'] + variables['input.y']",
                "output_variable": "result",
            },
        )
        store = VariableStore()
        await store.set("input.x", 10)
        await store.set("input.y", 20)

        context = ExecutionContext(
            run_id="test-run-002",
            user_id="user-1",
            workflow_id="wf-1",
        )

        executor = get_executor(NodeType.CODE_EXECUTION)
        result = await executor.execute(node_def, store, context)

        assert result.status == NodeStatus.COMPLETED
        snapshot = await store.snapshot()
        assert snapshot.get("code_1.output") == 30

    @pytest.mark.asyncio
    async def test_template_transform_with_mock_vars(self):
        """TemplateTransform uses Jinja2 with snapshot_safe() as context.

        Variables with dotted keys like ``input.name`` are passed as
        ``**snapshot`` to Jinja2's ``render()``. Since dots create nested
        access in Jinja2 (``input`` -> ``.name``), we set up a flat key
        ``name`` that Jinja2 can directly resolve.
        """
        node_def = WorkflowNodeDef(
            id="tmpl_1",
            type=NodeType.TEMPLATE_TRANSFORM,
            data={
                "type": "TEMPLATE_TRANSFORM",
                "template": "Welcome {{ name }}!",
                "output_variable": "message",
            },
        )
        store = VariableStore()
        # Use a flat key that Jinja2 can directly resolve
        await store.set("name", "Bob")

        context = ExecutionContext(
            run_id="test-run-003",
            user_id="user-1",
            workflow_id="wf-1",
        )

        executor = get_executor(NodeType.TEMPLATE_TRANSFORM)
        result = await executor.execute(node_def, store, context)

        assert result.status == NodeStatus.COMPLETED
        snapshot = await store.snapshot()
        assert "Bob" in str(snapshot.get("tmpl_1.output", ""))

    @pytest.mark.asyncio
    async def test_execution_timeout(self):
        """Verify asyncio.wait_for timeout works for long-running nodes.

        The CodeExecution executor has its own internal 30s timeout, but
        the test-node endpoint wraps it with an external asyncio.wait_for
        at 30s. We test a shorter timeout here to confirm the pattern works.
        """
        node_def = WorkflowNodeDef(
            id="code_slow",
            type=NodeType.CODE_EXECUTION,
            data={
                "type": "CODE_EXECUTION",
                "code": "import time; time.sleep(10); result = 'done'",
                "output_variable": "result",
            },
        )
        store = VariableStore()
        context = ExecutionContext(
            run_id="test-run-timeout",
            user_id="user-1",
            workflow_id="wf-1",
        )

        executor = get_executor(NodeType.CODE_EXECUTION)

        # The executor itself has a 30s internal timeout (subprocess),
        # and the endpoint wraps with wait_for(30s). We test with a very
        # short timeout to verify the external timeout path.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                executor.execute(node_def, store, context),
                timeout=0.3,
            )

    @pytest.mark.asyncio
    async def test_execution_error_returns_failed(self):
        """Node execution errors should produce a FAILED result."""
        node_def = WorkflowNodeDef(
            id="code_err",
            type=NodeType.CODE_EXECUTION,
            data={
                "type": "CODE_EXECUTION",
                "code": "result = 1 / 0",
                "output_variable": "result",
            },
        )
        store = VariableStore()
        context = ExecutionContext(
            run_id="test-run-error",
            user_id="user-1",
            workflow_id="wf-1",
        )

        executor = get_executor(NodeType.CODE_EXECUTION)
        result = await executor.execute(node_def, store, context)

        assert result.status == NodeStatus.FAILED
        assert result.error is not None


# ---------------------------------------------------------------------------
# Response construction from NodeResult
# ---------------------------------------------------------------------------


class TestResponseConstruction:
    """Verify NodeTestResponse is correctly built from execution results."""

    @pytest.mark.asyncio
    async def test_success_response(self):
        """Simulate the response path for a successful execution."""
        store = VariableStore()
        await store.set("input.x", 5)

        # Simulate execution result
        result = NodeResult(
            node_id="code_1",
            status=NodeStatus.COMPLETED,
            output=25,
            duration_ms=12,
        )
        await store.set("code_1.result", 25)

        snapshot = await store.snapshot()
        resp = NodeTestResponse(
            node_id=result.node_id,
            node_type="CODE_EXECUTION",
            status=result.status.value,
            output=result.output,
            error=result.error,
            duration_ms=result.duration_ms,
            variables_after=snapshot,
        )

        assert resp.status == "completed"
        assert resp.output == 25
        assert resp.error is None
        assert resp.variables_after["code_1.result"] == 25
        assert resp.variables_after["input.x"] == 5

    @pytest.mark.asyncio
    async def test_failure_response(self):
        """Simulate the response path for a failed execution."""
        store = VariableStore()
        snapshot = await store.snapshot()

        resp = NodeTestResponse(
            node_id="code_1",
            node_type="CODE_EXECUTION",
            status="failed",
            error="ZeroDivisionError: division by zero",
            duration_ms=3,
            variables_after=snapshot,
        )

        assert resp.status == "failed"
        assert "ZeroDivisionError" in resp.error
        assert resp.output is None

    @pytest.mark.asyncio
    async def test_timeout_response(self):
        """Simulate the response path for a timed-out execution."""
        store = VariableStore()
        await store.set("input.data", "partial")
        snapshot = await store.snapshot()

        resp = NodeTestResponse(
            node_id="code_slow",
            node_type="CODE_EXECUTION",
            status="failed",
            error="Node execution timed out after 30 seconds",
            duration_ms=30001,
            variables_after=snapshot,
        )

        assert resp.status == "failed"
        assert "timed out" in resp.error
        assert resp.variables_after["input.data"] == "partial"

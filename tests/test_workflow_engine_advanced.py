"""Advanced tests for engine retry, variable store expression evaluation,
and variable store helper methods.

These tests are separated from test_workflow.py to allow concurrent development.
"""

from __future__ import annotations

import asyncio
import pytest
from typing import Any
from unittest.mock import AsyncMock, patch

from fim_one.core.workflow.engine import WorkflowEngine
from fim_one.core.workflow.parser import parse_blueprint
from fim_one.core.workflow.types import (
    ExecutionContext,
    NodeResult,
    NodeStatus,
    NodeType,
    WorkflowBlueprint,
    WorkflowEdgeDef,
    WorkflowNodeDef,
)
from fim_one.core.workflow.variable_store import VariableStore


# ---------------------------------------------------------------------------
# Helpers
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


def _edge(source: str, target: str, **kw: Any) -> dict:
    return {
        "id": f"{source}->{target}",
        "source": source,
        "target": target,
        **kw,
    }


async def _collect_events(engine: WorkflowEngine, bp: WorkflowBlueprint, inputs=None):
    """Run engine and collect all SSE events."""
    events = []
    async for event_name, event_data in engine.execute_streaming(bp, inputs):
        events.append((event_name, event_data))
    return events


# ---------------------------------------------------------------------------
# Variable Store: evaluate_expression
# ---------------------------------------------------------------------------

class TestVariableStoreExpressions:
    """Test the safe expression evaluation feature."""

    @pytest.mark.asyncio
    async def test_simple_arithmetic(self):
        store = VariableStore()
        await store.set("x", 10)
        result = await store.evaluate_expression("x + 5")
        assert result == 15

    @pytest.mark.asyncio
    async def test_comparison(self):
        store = VariableStore()
        await store.set("count", 3)
        assert await store.evaluate_expression("count < 5") is True
        assert await store.evaluate_expression("count > 5") is False
        assert await store.evaluate_expression("count == 3") is True

    @pytest.mark.asyncio
    async def test_string_operations(self):
        store = VariableStore()
        await store.set("name", "hello")
        result = await store.evaluate_expression("len(name)")
        assert result == 5

    @pytest.mark.asyncio
    async def test_builtin_functions(self):
        store = VariableStore()
        await store.set("values", [3, 1, 4, 1, 5])
        assert await store.evaluate_expression("len(values)") == 5
        assert await store.evaluate_expression("max(values)") == 5
        assert await store.evaluate_expression("min(values)") == 1
        assert await store.evaluate_expression("sum(values)") == 14
        assert await store.evaluate_expression("sorted(values)") == [1, 1, 3, 4, 5]

    @pytest.mark.asyncio
    async def test_dotted_key_access(self):
        store = VariableStore()
        await store.set("llm_1.result", "some output")
        # Both full key and short alias should work
        assert await store.evaluate_expression("len(result)") == 11

    @pytest.mark.asyncio
    async def test_env_vars_excluded(self):
        store = VariableStore(env_vars={"SECRET": "hidden"})
        await store.set("public_var", "visible")
        # env vars should NOT be accessible in expressions
        with pytest.raises(ValueError, match="Expression evaluation failed"):
            await store.evaluate_expression("len(SECRET)")

    @pytest.mark.asyncio
    async def test_boolean_logic(self):
        store = VariableStore()
        await store.set("a", True)
        await store.set("b", False)
        assert await store.evaluate_expression("a and not b") is True
        assert await store.evaluate_expression("a or b") is True
        assert await store.evaluate_expression("not a") is False

    @pytest.mark.asyncio
    async def test_type_casting(self):
        store = VariableStore()
        await store.set("num_str", "42")
        assert await store.evaluate_expression("int(num_str)") == 42
        assert await store.evaluate_expression("float(num_str)") == 42.0

    @pytest.mark.asyncio
    async def test_invalid_expression_raises(self):
        store = VariableStore()
        with pytest.raises(ValueError, match="Expression evaluation failed"):
            await store.evaluate_expression("undefined_var + 1")

    @pytest.mark.asyncio
    async def test_complex_expression(self):
        store = VariableStore()
        await store.set("items", [1, 2, 3, 4, 5])
        await store.set("threshold", 3)
        result = await store.evaluate_expression("len(items) > threshold")
        assert result is True


# ---------------------------------------------------------------------------
# Variable Store: helper methods
# ---------------------------------------------------------------------------

class TestVariableStoreHelpers:
    """Test has(), delete(), keys() helper methods."""

    @pytest.mark.asyncio
    async def test_has_existing_key(self):
        store = VariableStore()
        await store.set("x", 1)
        assert await store.has("x") is True

    @pytest.mark.asyncio
    async def test_has_missing_key(self):
        store = VariableStore()
        assert await store.has("nonexistent") is False

    @pytest.mark.asyncio
    async def test_delete_existing_key(self):
        store = VariableStore()
        await store.set("x", 1)
        result = await store.delete("x")
        assert result is True
        assert await store.has("x") is False

    @pytest.mark.asyncio
    async def test_delete_missing_key(self):
        store = VariableStore()
        result = await store.delete("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_keys(self):
        store = VariableStore()
        await store.set("a", 1)
        await store.set("b", 2)
        await store.set("c", 3)
        keys = await store.keys()
        assert sorted(keys) == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_keys_includes_env(self):
        store = VariableStore(env_vars={"API_KEY": "secret"})
        await store.set("x", 1)
        keys = await store.keys()
        assert "env.API_KEY" in keys
        assert "x" in keys


# ---------------------------------------------------------------------------
# Engine: retry support
# ---------------------------------------------------------------------------

class TestEngineRetry:
    """Test the engine's per-node retry mechanism."""

    def _make_flaky_blueprint(
        self, retry_count: int = 2, retry_delay_ms: int = 100
    ) -> dict:
        """Create a blueprint with an LLM node that uses retry config."""
        return {
            "nodes": [
                _start_node(),
                {
                    "id": "code_1",
                    "type": "llm",
                    "position": {"x": 200, "y": 0},
                    "data": {
                        "type": "LLM",
                        "prompt_template": "test",
                        "output_variable": "code_result",
                        "retry_count": retry_count,
                        "retry_delay_ms": retry_delay_ms,
                    },
                },
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "code_1"),
                _edge("code_1", "end_1"),
            ],
        }

    @pytest.mark.asyncio
    async def test_retry_config_is_parsed(self):
        """Verify retry_count and retry_delay_ms are read from node data."""
        raw = self._make_flaky_blueprint(retry_count=3, retry_delay_ms=500)
        bp = parse_blueprint(raw)
        code_node = next(n for n in bp.nodes if n.id == "code_1")
        assert code_node.data["retry_count"] == 3
        assert code_node.data["retry_delay_ms"] == 500

    @pytest.mark.asyncio
    async def test_no_retry_by_default(self):
        """Nodes without retry_count should not retry."""
        async def ok_execute(node, store, ctx):
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.COMPLETED,
                output="ok",
            )

        raw = {
            "nodes": [
                _start_node(),
                {
                    "id": "code_1",
                    "type": "llm",
                    "position": {"x": 200, "y": 0},
                    "data": {
                        "type": "LLM",
                        "prompt_template": "test",
                        "output_variable": "code_result",
                        # No retry_count
                    },
                },
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "code_1"),
                _edge("code_1", "end_1"),
            ],
        }
        bp = parse_blueprint(raw)
        engine = WorkflowEngine(max_concurrency=1)

        with patch(
            "fim_one.core.workflow.engine.get_executor"
        ) as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = ok_execute

            def get_exec_side_effect(node_type):
                if node_type == NodeType.LLM:
                    return mock_executor
                from fim_one.core.workflow.nodes import get_executor as real_get
                return real_get(node_type)

            mock_get.side_effect = get_exec_side_effect

            events = await _collect_events(engine, bp)

        # Should not see any node_retrying events
        retry_events = [e for e in events if e[0] == "node_retrying"]
        assert len(retry_events) == 0

    @pytest.mark.asyncio
    async def test_retry_emits_retrying_events(self):
        """When a node fails and has retry configured, node_retrying events
        should be emitted before each retry attempt."""
        # We'll mock the executor to fail twice, then succeed
        call_count = 0

        async def mock_execute(node, store, ctx):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error=f"Transient failure #{call_count}",
                )
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.COMPLETED,
                output="success after retries",
            )

        raw = self._make_flaky_blueprint(retry_count=3, retry_delay_ms=10)
        bp = parse_blueprint(raw)
        engine = WorkflowEngine(max_concurrency=1)

        with patch(
            "fim_one.core.workflow.engine.get_executor"
        ) as mock_get:
            # Return a mock executor that uses our custom execute function
            mock_executor = AsyncMock()
            mock_executor.execute = mock_execute

            def get_exec_side_effect(node_type):
                if node_type == NodeType.LLM:
                    return mock_executor
                # For other node types, use real executors
                from fim_one.core.workflow.nodes import get_executor as real_get
                return real_get(node_type)

            mock_get.side_effect = get_exec_side_effect

            events = await _collect_events(engine, bp)

        # Check for retrying events
        retry_events = [e for e in events if e[0] == "node_retrying"]
        assert len(retry_events) == 2  # Failed twice, retried twice

        # First retry
        assert retry_events[0][1]["attempt"] == 1
        assert retry_events[0][1]["max_retries"] == 3
        assert "Transient failure" in retry_events[0][1]["previous_error"]

        # Second retry
        assert retry_events[1][1]["attempt"] == 2

        # Should ultimately complete
        completed_events = [
            e for e in events
            if e[0] == "node_completed" and e[1].get("node_id") == "code_1"
        ]
        assert len(completed_events) == 1
        assert completed_events[0][1]["retries_used"] == 2

    @pytest.mark.asyncio
    async def test_retry_exhausted_fails(self):
        """When all retries are exhausted, the node should fail."""
        async def always_fail(node, store, ctx):
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error="Permanent failure",
            )

        raw = self._make_flaky_blueprint(retry_count=2, retry_delay_ms=10)
        bp = parse_blueprint(raw)
        engine = WorkflowEngine(max_concurrency=1)

        with patch(
            "fim_one.core.workflow.engine.get_executor"
        ) as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = always_fail

            def get_exec_side_effect(node_type):
                if node_type == NodeType.LLM:
                    return mock_executor
                from fim_one.core.workflow.nodes import get_executor as real_get
                return real_get(node_type)

            mock_get.side_effect = get_exec_side_effect

            events = await _collect_events(engine, bp)

        # Should have 2 retry events (not 3, since the initial attempt doesn't count)
        retry_events = [e for e in events if e[0] == "node_retrying"]
        assert len(retry_events) == 2

        # Should ultimately fail
        fail_events = [
            e for e in events
            if e[0] == "node_failed" and e[1].get("node_id") == "code_1"
        ]
        assert len(fail_events) == 1

    @pytest.mark.asyncio
    async def test_retry_respects_cancellation(self):
        """Retry loop should stop if the cancel event is set."""
        call_count = 0

        async def slow_fail(node, store, ctx):
            nonlocal call_count
            call_count += 1
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error="fail",
            )

        raw = self._make_flaky_blueprint(retry_count=10, retry_delay_ms=50)
        bp = parse_blueprint(raw)

        cancel_event = asyncio.Event()
        engine = WorkflowEngine(max_concurrency=1, cancel_event=cancel_event)

        with patch(
            "fim_one.core.workflow.engine.get_executor"
        ) as mock_get:
            mock_executor = AsyncMock()

            async def fail_and_cancel(node, store, ctx):
                nonlocal call_count
                call_count += 1
                if call_count >= 2:
                    cancel_event.set()
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error="fail",
                )

            mock_executor.execute = fail_and_cancel

            def get_exec_side_effect(node_type):
                if node_type == NodeType.LLM:
                    return mock_executor
                from fim_one.core.workflow.nodes import get_executor as real_get
                return real_get(node_type)

            mock_get.side_effect = get_exec_side_effect

            events = await _collect_events(engine, bp)

        # Should not have retried all 10 times
        retry_events = [e for e in events if e[0] == "node_retrying"]
        assert len(retry_events) < 10

    @pytest.mark.asyncio
    async def test_retry_count_zero_means_no_retry(self):
        """retry_count=0 (or missing) should mean no retries."""
        raw = self._make_flaky_blueprint(retry_count=0, retry_delay_ms=10)
        bp = parse_blueprint(raw)

        async def fail_once(node, store, ctx):
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error="single failure",
            )

        engine = WorkflowEngine(max_concurrency=1)

        with patch(
            "fim_one.core.workflow.engine.get_executor"
        ) as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = fail_once

            def get_exec_side_effect(node_type):
                if node_type == NodeType.LLM:
                    return mock_executor
                from fim_one.core.workflow.nodes import get_executor as real_get
                return real_get(node_type)

            mock_get.side_effect = get_exec_side_effect

            events = await _collect_events(engine, bp)

        retry_events = [e for e in events if e[0] == "node_retrying"]
        assert len(retry_events) == 0

    @pytest.mark.asyncio
    async def test_negative_retry_count_treated_as_zero(self):
        """Negative retry_count should be clamped to 0."""
        raw = self._make_flaky_blueprint(retry_count=-5, retry_delay_ms=10)
        bp = parse_blueprint(raw)
        engine = WorkflowEngine(max_concurrency=1)

        async def ok_execute(node, store, ctx):
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.COMPLETED,
                output="ok",
            )

        with patch(
            "fim_one.core.workflow.engine.get_executor"
        ) as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = ok_execute

            def get_exec_side_effect(node_type):
                if node_type == NodeType.LLM:
                    return mock_executor
                from fim_one.core.workflow.nodes import get_executor as real_get
                return real_get(node_type)

            mock_get.side_effect = get_exec_side_effect

            events = await _collect_events(engine, bp)

        retry_events = [e for e in events if e[0] == "node_retrying"]
        assert len(retry_events) == 0


# ---------------------------------------------------------------------------
# Workflow-Level Timeout
# ---------------------------------------------------------------------------


class TestWorkflowTimeout:
    """Test the workflow_timeout_ms engine parameter."""

    @pytest.mark.asyncio
    async def test_no_timeout_by_default(self):
        """With default workflow_timeout_ms=0, no timeout is applied."""
        raw = {
            "nodes": [
                _start_node(),
                _end_node(),
            ],
            "edges": [_edge("start_1", "end_1")],
        }
        bp = parse_blueprint(raw)
        engine = WorkflowEngine(max_concurrency=1)

        events = await _collect_events(engine, bp)
        event_names = [e[0] for e in events]
        assert "run_completed" in event_names
        assert "run_failed" not in event_names

    @pytest.mark.asyncio
    async def test_generous_timeout_completes(self):
        """A generous timeout should let the workflow complete normally."""
        raw = {
            "nodes": [
                _start_node(),
                _end_node(),
            ],
            "edges": [_edge("start_1", "end_1")],
        }
        bp = parse_blueprint(raw)
        engine = WorkflowEngine(max_concurrency=1, workflow_timeout_ms=60000)

        events = await _collect_events(engine, bp)
        event_names = [e[0] for e in events]
        assert "run_completed" in event_names

    @pytest.mark.asyncio
    async def test_tight_timeout_with_slow_node(self):
        """A very tight timeout should trigger when a node takes too long."""
        from unittest.mock import patch

        # Create a blueprint with a slow LLM node (will be mocked)
        raw = {
            "nodes": [
                _start_node(),
                {
                    "id": "slow_1",
                    "type": "custom",
                    "position": {"x": 200, "y": 0},
                    "data": {
                        "type": "LLM",
                        "prompt_template": "test",
                        "output_variable": "result",
                        "timeout_ms": 30000,
                    },
                },
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "slow_1"),
                _edge("slow_1", "end_1"),
            ],
        }
        bp = parse_blueprint(raw)

        # Mock the LLM executor to sleep for 5 seconds.
        # patch replaces the class method with a Mock, so side_effect
        # receives (node, store, ctx) — no ``self``.
        async def slow_execute(node, store, ctx):
            await asyncio.sleep(5)
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.COMPLETED,
                output="done",
            )

        # Use a 50ms workflow timeout — the slow executor will be interrupted
        engine = WorkflowEngine(max_concurrency=1, workflow_timeout_ms=50)

        with patch(
            "fim_one.core.workflow.nodes.LLMExecutor.execute",
            side_effect=slow_execute,
        ):
            events = await _collect_events(engine, bp)

        event_names = [e[0] for e in events]
        # Should have a run_failed event with timeout message
        assert "run_failed" in event_names
        fail_event = next(e for e in events if e[0] == "run_failed")
        assert "timed out" in fail_event[1].get("error", "")

    @pytest.mark.asyncio
    async def test_timeout_skips_pending_nodes(self):
        """When workflow times out, pending nodes should be skipped."""
        from unittest.mock import patch

        raw = {
            "nodes": [
                _start_node(),
                {
                    "id": "slow_1",
                    "type": "custom",
                    "position": {"x": 200, "y": 0},
                    "data": {
                        "type": "LLM",
                        "prompt_template": "test",
                        "output_variable": "r1",
                        "timeout_ms": 30000,
                    },
                },
                {
                    "id": "after_slow",
                    "type": "custom",
                    "position": {"x": 400, "y": 0},
                    "data": {
                        "type": "HUMAN_INTERVENTION",
                        "title": "review",
                    },
                },
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "slow_1"),
                _edge("slow_1", "after_slow"),
                _edge("after_slow", "end_1"),
            ],
        }
        bp = parse_blueprint(raw)

        async def slow_execute(node, store, ctx):
            await asyncio.sleep(5)
            return NodeResult(
                node_id=node.id, status=NodeStatus.COMPLETED, output="done"
            )

        engine = WorkflowEngine(max_concurrency=1, workflow_timeout_ms=50)

        with patch(
            "fim_one.core.workflow.nodes.LLMExecutor.execute",
            side_effect=slow_execute,
        ):
            events = await _collect_events(engine, bp)

        skip_events = [e for e in events if e[0] == "node_skipped"]
        skipped_reasons = [e[1].get("reason", "") for e in skip_events]
        # At least some nodes should be skipped due to timeout
        assert any("timeout" in r.lower() or "Workflow timeout" in r for r in skipped_reasons)


# ---------------------------------------------------------------------------
# Condition Branch Routing & Edge Activation
# ---------------------------------------------------------------------------


def _condition_branch_node(
    node_id: str,
    conditions: list[dict],
    default_handle: str = "source-default",
    **data: Any,
) -> dict:
    """Helper to build a CONDITION_BRANCH node definition."""
    return {
        "id": node_id,
        "type": "custom",
        "position": {"x": 200, "y": 0},
        "data": {
            "type": "CONDITION_BRANCH",
            "conditions": conditions,
            "default_handle": default_handle,
            **data,
        },
    }


def _variable_assign_node(
    node_id: str,
    assignments: list[dict] | None = None,
    **data: Any,
) -> dict:
    """Helper to build a lightweight pass-through "work" node.

    Uses a HUMAN_INTERVENTION node which, in headless test mode (no
    db_session_factory), auto-approves and completes immediately.  This
    gives the orchestration/branch-routing tests a generic node that
    simply runs to completion, without depending on any deleted node type.
    The ``assignments`` argument is accepted and ignored for call-site
    compatibility with the original branch-routing graphs.
    """
    return {
        "id": node_id,
        "type": "custom",
        "position": {"x": 300, "y": 0},
        "data": {
            "type": "HUMAN_INTERVENTION",
            "title": f"work-{node_id}",
        },
    }


def _events_by_type(events: list[tuple[str, dict]], event_type: str) -> list[dict]:
    """Filter collected events by event name, returning just the data dicts."""
    return [data for name, data in events if name == event_type]


def _completed_node_ids(events: list[tuple[str, dict]]) -> set[str]:
    """Return the set of node_ids that emitted node_completed events."""
    return {d["node_id"] for d in _events_by_type(events, "node_completed")}


def _skipped_node_ids(events: list[tuple[str, dict]]) -> set[str]:
    """Return the set of node_ids that emitted node_skipped events."""
    return {d["node_id"] for d in _events_by_type(events, "node_skipped")}


class TestConditionBranchRouting:
    """Test condition branching, multi-handle routing, and edge activation logic."""

    # ------------------------------------------------------------------
    # 1. Condition evaluates to TRUE — only the true branch runs
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_condition_true_branch_only(self):
        """Start -> ConditionBranch -> (true: NodeA, false: NodeB) -> End.

        The condition evaluates to true, so NodeA should run and NodeB
        should be skipped.
        """
        raw = {
            "nodes": [
                _start_node(),
                _condition_branch_node(
                    "cond_1",
                    conditions=[
                        {
                            "id": "true_branch",
                            "expression": "1 == 1",
                            "handle": "condition-true_branch",
                        },
                    ],
                    default_handle="source-default",
                ),
                _variable_assign_node(
                    "node_a",
                    assignments=[{"variable": "branch", "value": "A"}],
                ),
                _variable_assign_node(
                    "node_b",
                    assignments=[{"variable": "branch", "value": "B"}],
                ),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "cond_1"),
                # True branch edge: sourceHandle matches the condition handle
                _edge("cond_1", "node_a", sourceHandle="condition-true_branch"),
                # False/default branch edge
                _edge("cond_1", "node_b", sourceHandle="source-default"),
                _edge("node_a", "end_1"),
                _edge("node_b", "end_1"),
            ],
        }
        bp = parse_blueprint(raw)
        engine = WorkflowEngine(max_concurrency=1)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        skipped = _skipped_node_ids(events)

        assert "node_a" in completed, "NodeA (true branch) should have completed"
        assert "node_b" in skipped, "NodeB (false branch) should have been skipped"
        assert "end_1" in completed, "End node should have completed"

        # Verify the workflow completed successfully
        event_names = [name for name, _ in events]
        assert "run_completed" in event_names

    # ------------------------------------------------------------------
    # 2. Condition evaluates to FALSE — only the default branch runs
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_condition_false_branch_only(self):
        """Same graph as above but the condition evaluates to false, so
        NodeB (default branch) runs and NodeA is skipped.
        """
        raw = {
            "nodes": [
                _start_node(),
                _condition_branch_node(
                    "cond_1",
                    conditions=[
                        {
                            "id": "true_branch",
                            # This condition is always false
                            "expression": "1 == 0",
                            "handle": "condition-true_branch",
                        },
                    ],
                    default_handle="source-default",
                ),
                _variable_assign_node(
                    "node_a",
                    assignments=[{"variable": "branch", "value": "A"}],
                ),
                _variable_assign_node(
                    "node_b",
                    assignments=[{"variable": "branch", "value": "B"}],
                ),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "cond_1"),
                _edge("cond_1", "node_a", sourceHandle="condition-true_branch"),
                _edge("cond_1", "node_b", sourceHandle="source-default"),
                _edge("node_a", "end_1"),
                _edge("node_b", "end_1"),
            ],
        }
        bp = parse_blueprint(raw)
        engine = WorkflowEngine(max_concurrency=1)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        skipped = _skipped_node_ids(events)

        assert "node_a" in skipped, "NodeA (true branch) should have been skipped"
        assert "node_b" in completed, "NodeB (default branch) should have completed"
        assert "end_1" in completed, "End node should have completed"

        event_names = [name for name, _ in events]
        assert "run_completed" in event_names

    # ------------------------------------------------------------------
    # 3. Multi-handle condition with 3+ branches — only matching activates
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_multi_handle_condition(self):
        """ConditionBranch with handle_0, handle_1, handle_2, and default.

        Only the second condition (handle_1) should match, so only its
        target node runs; the other branch nodes are skipped.
        """
        raw = {
            "nodes": [
                _start_node(),
                _condition_branch_node(
                    "cond_1",
                    conditions=[
                        {
                            "id": "h0",
                            "expression": "1 > 10",  # false
                            "handle": "condition-h0",
                        },
                        {
                            "id": "h1",
                            "expression": "5 > 3",  # TRUE — first match wins
                            "handle": "condition-h1",
                        },
                        {
                            "id": "h2",
                            "expression": "True",  # would be true, but h1 wins first
                            "handle": "condition-h2",
                        },
                    ],
                    default_handle="source-default",
                ),
                _variable_assign_node("branch_0"),
                _variable_assign_node("branch_1"),
                _variable_assign_node("branch_2"),
                _variable_assign_node("branch_default"),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "cond_1"),
                _edge("cond_1", "branch_0", sourceHandle="condition-h0"),
                _edge("cond_1", "branch_1", sourceHandle="condition-h1"),
                _edge("cond_1", "branch_2", sourceHandle="condition-h2"),
                _edge("cond_1", "branch_default", sourceHandle="source-default"),
                _edge("branch_0", "end_1"),
                _edge("branch_1", "end_1"),
                _edge("branch_2", "end_1"),
                _edge("branch_default", "end_1"),
            ],
        }
        bp = parse_blueprint(raw)
        engine = WorkflowEngine(max_concurrency=1)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        skipped = _skipped_node_ids(events)

        assert "branch_1" in completed, "branch_1 (matching condition) should run"
        assert "branch_0" in skipped, "branch_0 should be skipped"
        assert "branch_2" in skipped, "branch_2 should be skipped"
        assert "branch_default" in skipped, "branch_default should be skipped"
        assert "end_1" in completed, "End node should still complete"

    # ------------------------------------------------------------------
    # 4. Diamond merge after condition — merge node runs regardless
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_diamond_merge_after_condition(self):
        """Start -> Condition -> (true: A, false: B) -> Merge -> End.

        The merge node receives input from both branches.  Regardless of
        which branch was taken, the merge node should run because at least
        one incoming edge remains active.
        """
        raw = {
            "nodes": [
                _start_node(),
                _condition_branch_node(
                    "cond_1",
                    conditions=[
                        {
                            "id": "true_branch",
                            "expression": "1 == 1",  # true
                            "handle": "condition-true_branch",
                        },
                    ],
                    default_handle="source-default",
                ),
                _variable_assign_node(
                    "node_a",
                    assignments=[{"variable": "result", "value": "from_A"}],
                ),
                _variable_assign_node(
                    "node_b",
                    assignments=[{"variable": "result", "value": "from_B"}],
                ),
                _variable_assign_node("merge_node"),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "cond_1"),
                _edge("cond_1", "node_a", sourceHandle="condition-true_branch"),
                _edge("cond_1", "node_b", sourceHandle="source-default"),
                # Both branches feed into the merge node
                _edge("node_a", "merge_node"),
                _edge("node_b", "merge_node"),
                _edge("merge_node", "end_1"),
            ],
        }
        bp = parse_blueprint(raw)
        engine = WorkflowEngine(max_concurrency=1)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        skipped = _skipped_node_ids(events)

        # True branch taken: node_a runs, node_b skipped
        assert "node_a" in completed
        assert "node_b" in skipped

        # Merge node must still run — it has one active incoming edge (from node_a)
        # even though the edge from node_b is inactive (node_b was skipped).
        # The engine considers a node ready when all predecessors are in
        # completed/failed/skipped AND at least one incoming edge is active.
        assert "merge_node" in completed, (
            "Merge node should run because at least one incoming edge is active"
        )
        assert "end_1" in completed

        event_names = [name for name, _ in events]
        assert "run_completed" in event_names

    # ------------------------------------------------------------------
    # 4b. Diamond merge — false branch
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_diamond_merge_after_condition_false_branch(self):
        """Same diamond but the condition evaluates to false. The merge
        node should still run, fed by node_b.
        """
        raw = {
            "nodes": [
                _start_node(),
                _condition_branch_node(
                    "cond_1",
                    conditions=[
                        {
                            "id": "true_branch",
                            "expression": "1 == 0",  # false
                            "handle": "condition-true_branch",
                        },
                    ],
                    default_handle="source-default",
                ),
                _variable_assign_node(
                    "node_a",
                    assignments=[{"variable": "result", "value": "from_A"}],
                ),
                _variable_assign_node(
                    "node_b",
                    assignments=[{"variable": "result", "value": "from_B"}],
                ),
                _variable_assign_node("merge_node"),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "cond_1"),
                _edge("cond_1", "node_a", sourceHandle="condition-true_branch"),
                _edge("cond_1", "node_b", sourceHandle="source-default"),
                _edge("node_a", "merge_node"),
                _edge("node_b", "merge_node"),
                _edge("merge_node", "end_1"),
            ],
        }
        bp = parse_blueprint(raw)
        engine = WorkflowEngine(max_concurrency=1)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        skipped = _skipped_node_ids(events)

        assert "node_a" in skipped
        assert "node_b" in completed
        assert "merge_node" in completed, (
            "Merge node should run via the active false-branch edge"
        )
        assert "end_1" in completed

    # ------------------------------------------------------------------
    # 5. Nested conditions
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_nested_conditions(self):
        """Start -> Cond1 -> (true: Cond2 -> (true: A, false: B), false: C) -> End.

        Both conditions evaluate to true, so the path is:
        Start -> Cond1 -> Cond2 -> A -> End.
        Nodes B and C should be skipped.
        """
        raw = {
            "nodes": [
                _start_node(),
                # Outer condition — evaluates to true (takes the true branch)
                _condition_branch_node(
                    "cond_1",
                    conditions=[
                        {
                            "id": "outer_true",
                            "expression": "10 > 5",
                            "handle": "condition-outer_true",
                        },
                    ],
                    default_handle="source-default-1",
                ),
                # Inner condition (on the true branch of cond_1) — also true
                _condition_branch_node(
                    "cond_2",
                    conditions=[
                        {
                            "id": "inner_true",
                            "expression": "3 == 3",
                            "handle": "condition-inner_true",
                        },
                    ],
                    default_handle="source-default-2",
                ),
                _variable_assign_node(
                    "node_a",
                    assignments=[{"variable": "path", "value": "A"}],
                ),
                _variable_assign_node(
                    "node_b",
                    assignments=[{"variable": "path", "value": "B"}],
                ),
                _variable_assign_node(
                    "node_c",
                    assignments=[{"variable": "path", "value": "C"}],
                ),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "cond_1"),
                # Outer true -> inner condition
                _edge("cond_1", "cond_2", sourceHandle="condition-outer_true"),
                # Outer false -> C
                _edge("cond_1", "node_c", sourceHandle="source-default-1"),
                # Inner true -> A
                _edge("cond_2", "node_a", sourceHandle="condition-inner_true"),
                # Inner false -> B
                _edge("cond_2", "node_b", sourceHandle="source-default-2"),
                _edge("node_a", "end_1"),
                _edge("node_b", "end_1"),
                _edge("node_c", "end_1"),
            ],
        }
        bp = parse_blueprint(raw)
        engine = WorkflowEngine(max_concurrency=1)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        skipped = _skipped_node_ids(events)

        assert "cond_1" in completed
        assert "cond_2" in completed, "Inner condition should run (outer was true)"
        assert "node_a" in completed, "Node A should run (both conditions true)"
        assert "node_b" in skipped, "Node B should be skipped (inner condition was true)"
        assert "node_c" in skipped, "Node C should be skipped (outer condition was true)"
        assert "end_1" in completed

    # ------------------------------------------------------------------
    # 5b. Nested conditions — outer false
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_nested_conditions_outer_false(self):
        """When the outer condition is false, cond_2 is skipped.

        A skipped branching node (CONDITION_BRANCH) deactivates all its
        outgoing edges, causing downstream nodes to cascade-skip as well.
        """
        raw = {
            "nodes": [
                _start_node(),
                _condition_branch_node(
                    "cond_1",
                    conditions=[
                        {
                            "id": "outer_true",
                            "expression": "10 < 5",  # FALSE
                            "handle": "condition-outer_true",
                        },
                    ],
                    default_handle="source-default-1",
                ),
                _condition_branch_node(
                    "cond_2",
                    conditions=[
                        {
                            "id": "inner_true",
                            "expression": "3 == 3",
                            "handle": "condition-inner_true",
                        },
                    ],
                    default_handle="source-default-2",
                ),
                _variable_assign_node("node_a"),
                _variable_assign_node("node_b"),
                _variable_assign_node("node_c"),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "cond_1"),
                _edge("cond_1", "cond_2", sourceHandle="condition-outer_true"),
                _edge("cond_1", "node_c", sourceHandle="source-default-1"),
                _edge("cond_2", "node_a", sourceHandle="condition-inner_true"),
                _edge("cond_2", "node_b", sourceHandle="source-default-2"),
                _edge("node_a", "end_1"),
                _edge("node_b", "end_1"),
                _edge("node_c", "end_1"),
            ],
        }
        bp = parse_blueprint(raw)
        engine = WorkflowEngine(max_concurrency=1)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        skipped = _skipped_node_ids(events)

        assert "cond_2" in skipped, "Inner condition should be skipped (outer was false)"
        # Skipped cond_2 deactivates all outgoing edges → node_a and node_b cascade-skip
        assert "node_a" in skipped, (
            "node_a should be skipped because cond_2 was skipped and deactivated its edges"
        )
        assert "node_b" in skipped, (
            "node_b should be skipped for the same reason"
        )
        assert "node_c" in completed, "Node C (outer default branch) should run"
        assert "end_1" in completed

    # ------------------------------------------------------------------
    # 5c. Nested conditions — outer true, inner false
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_nested_conditions_inner_false(self):
        """Outer condition true, inner condition false.  Path is:
        Start -> Cond1 -> Cond2 -> B -> End.
        """
        raw = {
            "nodes": [
                _start_node(),
                _condition_branch_node(
                    "cond_1",
                    conditions=[
                        {
                            "id": "outer_true",
                            "expression": "10 > 5",  # TRUE
                            "handle": "condition-outer_true",
                        },
                    ],
                    default_handle="source-default-1",
                ),
                _condition_branch_node(
                    "cond_2",
                    conditions=[
                        {
                            "id": "inner_true",
                            "expression": "3 == 99",  # FALSE
                            "handle": "condition-inner_true",
                        },
                    ],
                    default_handle="source-default-2",
                ),
                _variable_assign_node("node_a"),
                _variable_assign_node("node_b"),
                _variable_assign_node("node_c"),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "cond_1"),
                _edge("cond_1", "cond_2", sourceHandle="condition-outer_true"),
                _edge("cond_1", "node_c", sourceHandle="source-default-1"),
                _edge("cond_2", "node_a", sourceHandle="condition-inner_true"),
                _edge("cond_2", "node_b", sourceHandle="source-default-2"),
                _edge("node_a", "end_1"),
                _edge("node_b", "end_1"),
                _edge("node_c", "end_1"),
            ],
        }
        bp = parse_blueprint(raw)
        engine = WorkflowEngine(max_concurrency=1)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        skipped = _skipped_node_ids(events)

        assert "cond_2" in completed
        assert "node_a" in skipped, "Node A should be skipped (inner condition false)"
        assert "node_b" in completed, "Node B (inner default) should run"
        assert "node_c" in skipped, "Node C should be skipped (outer condition true)"
        assert "end_1" in completed

    # ------------------------------------------------------------------
    # 7. Condition with variable from store (input-driven branching)
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_condition_with_input_variable(self):
        """The condition expression references an input variable.

        Input score=85 -> condition checks 'score > 70' -> true branch runs.
        """
        raw = {
            "nodes": [
                _start_node(),
                _condition_branch_node(
                    "cond_1",
                    conditions=[
                        {
                            "id": "high_score",
                            "variable": "score",
                            "operator": ">",
                            "value": "70",
                            "handle": "condition-high_score",
                        },
                    ],
                    default_handle="source-default",
                ),
                _variable_assign_node(
                    "pass_node",
                    assignments=[{"variable": "result", "value": "pass"}],
                ),
                _variable_assign_node(
                    "fail_node",
                    assignments=[{"variable": "result", "value": "fail"}],
                ),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "cond_1"),
                _edge("cond_1", "pass_node", sourceHandle="condition-high_score"),
                _edge("cond_1", "fail_node", sourceHandle="source-default"),
                _edge("pass_node", "end_1"),
                _edge("fail_node", "end_1"),
            ],
        }
        bp = parse_blueprint(raw)
        engine = WorkflowEngine(max_concurrency=1)
        events = await _collect_events(engine, bp, inputs={"score": 85})

        completed = _completed_node_ids(events)
        skipped = _skipped_node_ids(events)

        assert "pass_node" in completed, "Score 85 > 70 should take the pass branch"
        assert "fail_node" in skipped

    # ------------------------------------------------------------------
    # 8. Condition with input variable — below threshold
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_condition_with_input_variable_below_threshold(self):
        """Input score=50 -> condition 'score > 70' is false -> default runs."""
        raw = {
            "nodes": [
                _start_node(),
                _condition_branch_node(
                    "cond_1",
                    conditions=[
                        {
                            "id": "high_score",
                            "variable": "score",
                            "operator": ">",
                            "value": "70",
                            "handle": "condition-high_score",
                        },
                    ],
                    default_handle="source-default",
                ),
                _variable_assign_node("pass_node"),
                _variable_assign_node("fail_node"),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "cond_1"),
                _edge("cond_1", "pass_node", sourceHandle="condition-high_score"),
                _edge("cond_1", "fail_node", sourceHandle="source-default"),
                _edge("pass_node", "end_1"),
                _edge("fail_node", "end_1"),
            ],
        }
        bp = parse_blueprint(raw)
        engine = WorkflowEngine(max_concurrency=1)
        events = await _collect_events(engine, bp, inputs={"score": 50})

        completed = _completed_node_ids(events)
        skipped = _skipped_node_ids(events)

        assert "pass_node" in skipped, "Score 50 <= 70 should skip the pass branch"
        assert "fail_node" in completed, "Default (fail) branch should run"

    # ------------------------------------------------------------------
    # 9. All conditions false — default handle activates
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_all_conditions_false_activates_default(self):
        """When none of the explicit conditions match, the default branch
        must activate.
        """
        raw = {
            "nodes": [
                _start_node(),
                _condition_branch_node(
                    "cond_1",
                    conditions=[
                        {
                            "id": "never_1",
                            "expression": "False",
                            "handle": "condition-never_1",
                        },
                        {
                            "id": "never_2",
                            "expression": "0 > 1",
                            "handle": "condition-never_2",
                        },
                    ],
                    default_handle="source-default",
                ),
                _variable_assign_node("branch_1"),
                _variable_assign_node("branch_2"),
                _variable_assign_node("branch_default"),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "cond_1"),
                _edge("cond_1", "branch_1", sourceHandle="condition-never_1"),
                _edge("cond_1", "branch_2", sourceHandle="condition-never_2"),
                _edge("cond_1", "branch_default", sourceHandle="source-default"),
                _edge("branch_1", "end_1"),
                _edge("branch_2", "end_1"),
                _edge("branch_default", "end_1"),
            ],
        }
        bp = parse_blueprint(raw)
        engine = WorkflowEngine(max_concurrency=1)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        skipped = _skipped_node_ids(events)

        assert "branch_1" in skipped
        assert "branch_2" in skipped
        assert "branch_default" in completed, "Default branch should activate"
        assert "end_1" in completed

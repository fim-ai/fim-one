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
        """Create a blueprint with a code_execution node that uses retry config."""
        return {
            "nodes": [
                _start_node(),
                {
                    "id": "code_1",
                    "type": "code_execution",
                    "position": {"x": 200, "y": 0},
                    "data": {
                        "type": "CODE_EXECUTION",
                        "language": "python",
                        "code": "result = 'success'",
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
        code_node = next(n for n in bp.nodes if n.type == NodeType.CODE_EXECUTION)
        assert code_node.data["retry_count"] == 3
        assert code_node.data["retry_delay_ms"] == 500

    @pytest.mark.asyncio
    async def test_no_retry_by_default(self):
        """Nodes without retry_count should not retry."""
        raw = {
            "nodes": [
                _start_node(),
                {
                    "id": "code_1",
                    "type": "code_execution",
                    "position": {"x": 200, "y": 0},
                    "data": {
                        "type": "CODE_EXECUTION",
                        "language": "python",
                        "code": "result = 'ok'",
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
                if node_type == NodeType.CODE_EXECUTION:
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
                if node_type == NodeType.CODE_EXECUTION:
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
                if node_type == NodeType.CODE_EXECUTION:
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
                if node_type == NodeType.CODE_EXECUTION:
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
                        "type": "VARIABLE_ASSIGN",
                        "assignments": [],
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

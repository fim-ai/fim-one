"""Integration tests for complex workflow engine scenarios.

Exercises the engine with realistic multi-node graphs combining:
- Parallel branches merging (diamond patterns)
- Mixed error strategies in a single graph
- Condition branching with downstream node execution
- Env variable injection and interpolation
- Cancellation mid-execution

Generic "do work" nodes use kept node types:
- A node that must SUCCEED headlessly -> HUMAN_INTERVENTION
  (auto-approves when context.db_session_factory is None, i.e. test mode).
- A node that must FAIL deterministically -> CONNECTOR with no
  connector_id/action_id (returns FAILED immediately).
"""

from __future__ import annotations

import asyncio
import pytest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fim_one.core.workflow.engine import WorkflowEngine
from fim_one.core.workflow.parser import parse_blueprint
from fim_one.core.workflow.types import (
    ErrorStrategy,
    ExecutionContext,
    NodeResult,
    NodeStatus,
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
        "position": {"x": 800, "y": 0},
        "data": {"type": "END", **data},
    }


def _llm_node(node_id: str, **data: Any) -> dict:
    return {
        "id": node_id,
        "type": "llm",
        "position": {"x": 200, "y": 0},
        "data": {
            "type": "LLM",
            "prompt_template": "Hello {{input.query}}",
            **data,
        },
    }


def _work_node(node_id: str, **data: Any) -> dict:
    """A generic node that SUCCEEDS headlessly.

    Uses HUMAN_INTERVENTION, which auto-approves (status COMPLETED) when no
    db_session_factory is present (test mode).
    """
    return {
        "id": node_id,
        "type": "humanIntervention",
        "position": {"x": 200, "y": 0},
        "data": {
            "type": "HUMAN_INTERVENTION",
            "title": "Auto step",
            **data,
        },
    }


def _fail_node(node_id: str, **data: Any) -> dict:
    """A generic node that FAILS deterministically.

    Uses CONNECTOR with no connector_id/action_id, which returns FAILED
    immediately without touching the database.
    """
    return {
        "id": node_id,
        "type": "connector",
        "position": {"x": 200, "y": 0},
        "data": {
            "type": "CONNECTOR",
            **data,
        },
    }


def _condition_node(
    node_id: str,
    conditions: list[dict] | None = None,
    **data: Any,
) -> dict:
    return {
        "id": node_id,
        "type": "conditionBranch",
        "position": {"x": 200, "y": 0},
        "data": {
            "type": "CONDITION_BRANCH",
            "conditions": conditions or [
                {"expression": "True", "handle": "true"},
            ],
            **data,
        },
    }


def _edge(source: str, target: str, **kw: Any) -> dict:
    return {
        "id": kw.pop("edge_id", f"{source}->{target}"),
        "source": source,
        "target": target,
        **kw,
    }


async def _collect_events(
    engine: WorkflowEngine,
    bp: WorkflowBlueprint,
    inputs: dict[str, Any] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Run engine and collect all SSE events."""
    events: list[tuple[str, dict[str, Any]]] = []
    async for event_name, event_data in engine.execute_streaming(bp, inputs):
        events.append((event_name, event_data))
    return events


def _events_by_type(
    events: list[tuple[str, dict]], event_type: str
) -> list[dict]:
    return [data for name, data in events if name == event_type]


def _completed_node_ids(events: list[tuple[str, dict]]) -> set[str]:
    return {d["node_id"] for name, d in events if name == "node_completed"}


def _skipped_node_ids(events: list[tuple[str, dict]]) -> set[str]:
    return {d["node_id"] for name, d in events if name == "node_skipped"}


def _failed_node_ids(events: list[tuple[str, dict]]) -> set[str]:
    return {d["node_id"] for name, d in events if name == "node_failed"}


# ---------------------------------------------------------------------------
# Test: Parallel Diamond (fan-out / fan-in)
# ---------------------------------------------------------------------------


class TestParallelDiamond:
    """Start → [A, B] → End (both branches merge at End)."""

    @pytest.mark.asyncio
    async def test_both_branches_complete(self):
        """Both parallel branches should execute and merge at End."""
        bp = parse_blueprint({
            "nodes": [
                _start_node(),
                _work_node("wa"),
                _work_node("wb"),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "wa"),
                _edge("start_1", "wb"),
                _edge("wa", "end_1"),
                _edge("wb", "end_1"),
            ],
        })

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp, {"query": "test"})

        completed = _completed_node_ids(events)
        assert "wa" in completed
        assert "wb" in completed
        assert "end_1" in completed

        # Run should complete successfully
        run_events = _events_by_type(events, "run_completed")
        assert len(run_events) == 1
        assert run_events[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_one_branch_fails_stop_workflow(self):
        """If one parallel branch fails with STOP_WORKFLOW, the other should be skipped."""
        bp = parse_blueprint({
            "nodes": [
                _start_node(),
                _work_node("wa"),
                _fail_node("fail_b", error_strategy="stop_workflow"),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "wa"),
                _edge("start_1", "fail_b"),
                _edge("wa", "end_1"),
                _edge("fail_b", "end_1"),
            ],
        })

        engine = WorkflowEngine(max_concurrency=1)  # Sequential for predictability
        events = await _collect_events(engine, bp)

        failed = _failed_node_ids(events)
        assert "fail_b" in failed

        run_events = _events_by_type(events, "run_failed")
        assert len(run_events) == 1

    @pytest.mark.asyncio
    async def test_one_branch_fails_continue(self):
        """With CONTINUE strategy, failure in one branch shouldn't stop the other."""
        bp = parse_blueprint({
            "nodes": [
                _start_node(),
                _work_node("wa"),
                _fail_node("fail_b", error_strategy="continue"),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "wa"),
                _edge("start_1", "fail_b"),
                _edge("wa", "end_1"),
                _edge("fail_b", "end_1"),
            ],
        })

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        failed = _failed_node_ids(events)
        completed = _completed_node_ids(events)
        assert "fail_b" in failed
        assert "wa" in completed
        # End should still complete since fail_b used CONTINUE
        assert "end_1" in completed


# ---------------------------------------------------------------------------
# Test: Condition + Diamond Merge
# ---------------------------------------------------------------------------


class TestConditionDiamondMerge:
    """Start → Condition → [true: NodeA, false: NodeB] → End."""

    @pytest.mark.asyncio
    async def test_true_branch_runs_false_skipped(self):
        """Only the true branch should execute."""
        bp = parse_blueprint({
            "nodes": [
                _start_node(),
                _condition_node("cond_1", conditions=[
                    {"id": "c1", "expression": "True"},
                ]),
                _work_node("w_true"),
                _work_node("w_false"),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "cond_1"),
                _edge("cond_1", "w_true", sourceHandle="condition-c1"),
                _edge("cond_1", "w_false", sourceHandle="source-default"),
                _edge("w_true", "end_1"),
                _edge("w_false", "end_1"),
            ],
        })

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp, {"value": 10})

        completed = _completed_node_ids(events)
        skipped = _skipped_node_ids(events)

        assert "w_true" in completed
        assert "w_false" in skipped
        assert "end_1" in completed

    @pytest.mark.asyncio
    async def test_default_branch_when_no_conditions_match(self):
        """When no conditions match, the default handle branch runs."""
        bp = parse_blueprint({
            "nodes": [
                _start_node(),
                _condition_node("cond_1", conditions=[
                    {"id": "c1", "expression": "False"},
                ]),
                _work_node("w_special"),
                _work_node("w_default"),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "cond_1"),
                _edge("cond_1", "w_special", sourceHandle="condition-c1"),
                _edge("cond_1", "w_default", sourceHandle="source-default"),
                _edge("w_special", "end_1"),
                _edge("w_default", "end_1"),
            ],
        })

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        skipped = _skipped_node_ids(events)

        assert "w_special" in skipped
        assert "w_default" in completed


# ---------------------------------------------------------------------------
# Test: Mixed Error Strategies
# ---------------------------------------------------------------------------


class TestMixedErrorStrategies:
    """Graph with different error strategies on different nodes."""

    @pytest.mark.asyncio
    async def test_fail_branch_skips_downstream_only(self):
        """FAIL_BRANCH on a middle node should skip its exclusive downstream.

        Note: _collect_downstream does BFS from the failed node, so ANY node
        reachable from it (including shared merge points like end_1) gets skipped.
        This is by design — FAIL_BRANCH propagates fully through the subgraph.
        """
        bp = parse_blueprint({
            "nodes": [
                _start_node(),
                _work_node("work_ok"),
                _fail_node("work_fail", error_strategy="fail_branch"),
                _work_node("w_after_fail"),
                _end_node("end_ok"),
                _end_node("end_fail"),
            ],
            "edges": [
                _edge("start_1", "work_ok"),
                _edge("start_1", "work_fail"),
                _edge("work_fail", "w_after_fail"),
                _edge("w_after_fail", "end_fail"),
                _edge("work_ok", "end_ok"),
            ],
        })

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        failed = _failed_node_ids(events)
        completed = _completed_node_ids(events)
        skipped = _skipped_node_ids(events)

        assert "work_fail" in failed
        assert "w_after_fail" in skipped  # downstream of fail_branch
        assert "end_fail" in skipped  # downstream of fail_branch
        assert "work_ok" in completed  # sibling branch unaffected
        assert "end_ok" in completed  # sibling end node completes


# ---------------------------------------------------------------------------
# Test: Env Variables
# ---------------------------------------------------------------------------


class TestEnvVariableInjection:
    """Test that encrypted env vars are accessible in the workflow."""

    @pytest.mark.asyncio
    async def test_env_vars_available_in_store(self):
        """Env vars should be injected into the store under env.* namespace."""
        store = VariableStore(env_vars={"API_KEY": "secret-123"})
        val = await store.get("env.API_KEY")
        assert val == "secret-123"

    @pytest.mark.asyncio
    async def test_env_vars_in_variable_store_interpolation(self):
        """Env vars should be interpolable via {{env.API_KEY}} in store.interpolate()."""
        store = VariableStore(env_vars={"API_KEY": "secret-123"})
        result = await store.interpolate("Key is {{env.API_KEY}}")
        assert result == "Key is secret-123"


# ---------------------------------------------------------------------------
# Test: Cancellation During Execution
# ---------------------------------------------------------------------------


class TestCancellationScenarios:
    """Test cancellation at various points during execution."""

    @pytest.mark.asyncio
    async def test_cancel_before_second_node(self):
        """Cancelling after first node should skip remaining nodes."""
        cancel = asyncio.Event()

        bp = parse_blueprint({
            "nodes": [
                _start_node(),
                _work_node("work_1"),
                _work_node("work_2"),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "work_1"),
                _edge("work_1", "work_2"),
                _edge("work_2", "end_1"),
            ],
        })

        engine = WorkflowEngine(max_concurrency=5, cancel_event=cancel)

        events: list[tuple[str, dict]] = []
        saw_work_1_started = False

        async for event_name, event_data in engine.execute_streaming(bp):
            events.append((event_name, event_data))
            if event_name == "node_started" and event_data.get("node_id") == "work_1":
                saw_work_1_started = True
            if event_name == "node_completed" and event_data.get("node_id") == "work_1":
                cancel.set()

        # work_1 should have started
        assert saw_work_1_started

        # After cancellation, remaining nodes should be skipped
        skipped = _skipped_node_ids(events)
        # work_2 and end_1 should be skipped
        assert "work_2" in skipped or "end_1" in skipped


# ---------------------------------------------------------------------------
# Test: Complex Multi-Level Graph
# ---------------------------------------------------------------------------


class TestComplexGraph:
    """Complex graph with multiple levels and mixed node types."""

    @pytest.mark.asyncio
    async def test_deep_linear_chain(self):
        """10-node linear chain should execute in order."""
        nodes = [_start_node()]
        edges = []

        for i in range(1, 9):
            nodes.append(_work_node(f"w_{i}"))

        nodes.append(_end_node())

        # Chain: start → w_1 → w_2 → ... → w_8 → end
        prev = "start_1"
        for i in range(1, 9):
            edges.append(_edge(prev, f"w_{i}"))
            prev = f"w_{i}"
        edges.append(_edge(prev, "end_1"))

        bp = parse_blueprint({"nodes": nodes, "edges": edges})
        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        for i in range(1, 9):
            assert f"w_{i}" in completed

        run_completed = _events_by_type(events, "run_completed")
        assert len(run_completed) == 1
        assert run_completed[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_wide_fanout(self):
        """Start fans out to 5 parallel nodes, all merge to End."""
        nodes = [_start_node()]
        edges = []

        for i in range(1, 6):
            nodes.append(_work_node(f"w_{i}"))
            edges.append(_edge("start_1", f"w_{i}"))
            edges.append(_edge(f"w_{i}", "end_1"))

        nodes.append(_end_node())

        bp = parse_blueprint({"nodes": nodes, "edges": edges})
        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        for i in range(1, 6):
            assert f"w_{i}" in completed
        assert "end_1" in completed

    @pytest.mark.asyncio
    async def test_multiple_end_nodes(self):
        """Graph with two End nodes — both should execute."""
        bp = parse_blueprint({
            "nodes": [
                _start_node(),
                _work_node("w_1"),
                _work_node("w_2"),
                _end_node("end_1"),
                _end_node("end_2"),
            ],
            "edges": [
                _edge("start_1", "w_1"),
                _edge("start_1", "w_2"),
                _edge("w_1", "end_1"),
                _edge("w_2", "end_2"),
            ],
        })

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        assert "end_1" in completed
        assert "end_2" in completed


# ---------------------------------------------------------------------------
# Test: Input Preview Capture
# ---------------------------------------------------------------------------


class TestInputPreview:
    """Test that node input previews are captured and emitted."""

    @pytest.mark.asyncio
    async def test_input_preview_in_events(self):
        """node_started events should include input_preview."""
        bp = parse_blueprint({
            "nodes": [
                _start_node(),
                _work_node("work_1"),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "work_1"),
                _edge("work_1", "end_1"),
            ],
        })

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp, {"name": "test"})

        started_events = _events_by_type(events, "node_started")
        # work_1 should have an input_preview (showing start_1 outputs)
        work_1_started = [e for e in started_events if e.get("node_id") == "work_1"]
        assert len(work_1_started) == 1
        # input_preview should be set (may be None for Start node's first output)
        assert "input_preview" in work_1_started[0]


# ---------------------------------------------------------------------------
# Test: Workflow Timeout
# ---------------------------------------------------------------------------


class TestWorkflowTimeoutIntegration:
    """Integration tests for workflow-level timeout."""

    @pytest.mark.asyncio
    async def test_timeout_emits_run_failed(self):
        """Workflow timeout should emit run_failed with timeout error."""
        async def slow_execute(node, store, ctx):
            await asyncio.sleep(5)
            return NodeResult(node_id=node.id, status=NodeStatus.COMPLETED)

        bp = parse_blueprint({
            "nodes": [
                _start_node(),
                _work_node("work_1"),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "work_1"),
                _edge("work_1", "end_1"),
            ],
        })

        engine = WorkflowEngine(max_concurrency=5, workflow_timeout_ms=100)

        with patch(
            "fim_one.core.workflow.nodes.HumanInterventionExecutor.execute",
            side_effect=slow_execute,
        ):
            events = await _collect_events(engine, bp)

        run_failed = _events_by_type(events, "run_failed")
        assert len(run_failed) == 1
        assert "timed out" in run_failed[0]["error"].lower()


# ---------------------------------------------------------------------------
# Test: Event Ordering
# ---------------------------------------------------------------------------


class TestEventOrdering:
    """Verify SSE events follow correct chronological order."""

    @pytest.mark.asyncio
    async def test_run_started_is_first(self):
        """run_started should always be the first event."""
        bp = parse_blueprint({
            "nodes": [
                _start_node(),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "end_1"),
            ],
        })

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        assert events[0][0] == "run_started"

    @pytest.mark.asyncio
    async def test_run_completed_is_last(self):
        """run_completed should be the last event."""
        bp = parse_blueprint({
            "nodes": [
                _start_node(),
                _work_node("work_1"),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "work_1"),
                _edge("work_1", "end_1"),
            ],
        })

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        last_event = events[-1]
        assert last_event[0] in ("run_completed", "run_failed")

    @pytest.mark.asyncio
    async def test_node_started_before_completed(self):
        """For each node, started should come before completed."""
        bp = parse_blueprint({
            "nodes": [
                _start_node(),
                _work_node("work_1"),
                _work_node("work_2"),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "work_1"),
                _edge("work_1", "work_2"),
                _edge("work_2", "end_1"),
            ],
        })

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        # Build order index
        event_positions: dict[str, dict[str, int]] = {}
        for idx, (name, data) in enumerate(events):
            nid = data.get("node_id")
            if nid:
                if nid not in event_positions:
                    event_positions[nid] = {}
                event_positions[nid][name] = idx

        for nid, positions in event_positions.items():
            if "node_started" in positions and "node_completed" in positions:
                assert positions["node_started"] < positions["node_completed"], (
                    f"Node {nid}: started@{positions['node_started']} "
                    f"should come before completed@{positions['node_completed']}"
                )

    @pytest.mark.asyncio
    async def test_predecessor_completes_before_successor_starts(self):
        """In a linear chain, predecessor completion precedes successor start."""
        bp = parse_blueprint({
            "nodes": [
                _start_node(),
                _work_node("work_1"),
                _work_node("work_2"),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "work_1"),
                _edge("work_1", "work_2"),
                _edge("work_2", "end_1"),
            ],
        })

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        # Find positions
        work_1_completed = None
        work_2_started = None
        for idx, (name, data) in enumerate(events):
            nid = data.get("node_id")
            if name == "node_completed" and nid == "work_1":
                work_1_completed = idx
            if name == "node_started" and nid == "work_2":
                work_2_started = idx

        assert work_1_completed is not None
        assert work_2_started is not None
        assert work_1_completed < work_2_started


# ---------------------------------------------------------------------------
# Test: Empty Workflow
# ---------------------------------------------------------------------------


class TestMinimalWorkflows:
    """Edge cases with minimal workflows."""

    @pytest.mark.asyncio
    async def test_start_to_end_only(self):
        """Simplest possible workflow: Start → End."""
        bp = parse_blueprint({
            "nodes": [
                _start_node(),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "end_1"),
            ],
        })

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        run_completed = _events_by_type(events, "run_completed")
        assert len(run_completed) == 1
        assert run_completed[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_start_to_end_with_inputs(self):
        """Start → End with inputs should pass through via output_mapping dict."""
        bp = parse_blueprint({
            "nodes": [
                _start_node(),
                _end_node(output_mapping={
                    "echo": "{{input.message}}",
                }),
            ],
            "edges": [
                _edge("start_1", "end_1"),
            ],
        })

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp, {"message": "hello"})

        run_completed = _events_by_type(events, "run_completed")
        assert len(run_completed) == 1
        assert run_completed[0]["status"] == "completed"
        # Output should contain the echoed message
        outputs = run_completed[0].get("outputs", {})
        assert outputs.get("echo") == "hello"


# ---------------------------------------------------------------------------
# Test: Concurrency Limits
# ---------------------------------------------------------------------------


class TestConcurrencyLimits:
    """Verify semaphore correctly limits concurrent execution."""

    @pytest.mark.asyncio
    async def test_max_concurrency_one(self):
        """With max_concurrency=1, nodes should execute sequentially.

        Uses CONNECTOR nodes here (not HUMAN_INTERVENTION): the engine
        deliberately does NOT hold the concurrency semaphore for
        HUMAN_INTERVENTION (long human waits must not block the pool), so
        only a semaphore-gated node type exercises this guarantee. The
        patched executor ignores the connector_id/action_id entirely.
        """
        execution_log: list[tuple[str, str]] = []

        async def tracking_execute(node, store, ctx):
            execution_log.append((node.id, "start"))
            await asyncio.sleep(0.01)
            execution_log.append((node.id, "end"))
            return NodeResult(node_id=node.id, status=NodeStatus.COMPLETED, output="ok")

        bp = parse_blueprint({
            "nodes": [
                _start_node(),
                _fail_node("w_1", connector_id="c", action_id="a"),
                _fail_node("w_2", connector_id="c", action_id="a"),
                _fail_node("w_3", connector_id="c", action_id="a"),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "w_1"),
                _edge("start_1", "w_2"),
                _edge("start_1", "w_3"),
                _edge("w_1", "end_1"),
                _edge("w_2", "end_1"),
                _edge("w_3", "end_1"),
            ],
        })

        engine = WorkflowEngine(max_concurrency=1)

        with patch(
            "fim_one.core.workflow.nodes.ConnectorExecutor.execute",
            side_effect=tracking_execute,
        ):
            events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        assert "w_1" in completed
        assert "w_2" in completed
        assert "w_3" in completed

        # With concurrency=1, no two work nodes should overlap
        # Check that each "start" is followed by its "end" before next "start"
        w_entries = [(nid, action) for nid, action in execution_log
                     if nid.startswith("w_")]
        for i in range(0, len(w_entries) - 1, 2):
            nid_start, action_start = w_entries[i]
            nid_end, action_end = w_entries[i + 1]
            assert nid_start == nid_end, f"Expected matching pair, got {nid_start}/{nid_end}"
            assert action_start == "start"
            assert action_end == "end"

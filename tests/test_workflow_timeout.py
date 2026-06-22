"""Tests for workflow global run timeout."""

from __future__ import annotations

import asyncio

import pytest

from fim_one.core.workflow.engine import WorkflowEngine
from fim_one.core.workflow.nodes import get_executor as _real_get_executor
from fim_one.core.workflow.types import (
    NodeResult,
    NodeStatus,
    NodeType,
    WorkflowBlueprint,
    WorkflowEdgeDef,
    WorkflowNodeDef,
)


class _SleepExecutor:
    """Test-only executor that sleeps for a configurable duration."""

    def __init__(self, delay_seconds: float) -> None:
        self._delay = delay_seconds

    async def execute(self, node, store, context):  # type: ignore[no-untyped-def]
        await asyncio.sleep(self._delay)
        await store.set(f"{node.id}.output", "done")
        return NodeResult(node_id=node.id, status=NodeStatus.COMPLETED, output="done")


def _patched_get_executor(delays: dict[str, float]):
    """Return a get_executor replacement that injects sleep delays by node id.

    Falls back to the real executor for node types that aren't sleeping.
    """

    def _factory(node_type: NodeType):  # type: ignore[no-untyped-def]
        # The engine calls get_executor(node.type); we can't see the id here,
        # so we route by type: LLM nodes become sleep executors.
        if node_type == NodeType.LLM:
            return _SleepExecutor(delays.get("__default__", 30.0))
        return _real_get_executor(node_type)

    return _factory


def _make_slow_blueprint(delay_seconds: float = 5.0) -> WorkflowBlueprint:
    """Create a minimal blueprint whose LLM node sleeps longer than the timeout."""
    return WorkflowBlueprint(
        nodes=[
            WorkflowNodeDef(id="start", type=NodeType.START, data={}),
            WorkflowNodeDef(id="slow", type=NodeType.LLM, data={}),
            WorkflowNodeDef(id="end", type=NodeType.END, data={}),
        ],
        edges=[
            WorkflowEdgeDef(id="e1", source="start", target="slow"),
            WorkflowEdgeDef(id="e2", source="slow", target="end"),
        ],
    )


def _make_fast_blueprint() -> WorkflowBlueprint:
    """Create a blueprint that completes quickly."""
    return WorkflowBlueprint(
        nodes=[
            WorkflowNodeDef(id="start", type=NodeType.START, data={}),
            WorkflowNodeDef(id="end", type=NodeType.END, data={}),
        ],
        edges=[
            WorkflowEdgeDef(id="e1", source="start", target="end"),
        ],
    )


@pytest.mark.asyncio
async def test_timeout_cancels_workflow(monkeypatch) -> None:
    """A workflow exceeding the timeout should emit run_failed with timeout message."""
    monkeypatch.setattr(
        "fim_one.core.workflow.engine.get_executor",
        _patched_get_executor({"__default__": 10.0}),
    )
    engine = WorkflowEngine(
        workflow_timeout_ms=1000,  # 1 second timeout
        run_id="test-run-timeout",
        user_id="test-user",
        workflow_id="test-wf",
    )

    blueprint = _make_slow_blueprint(delay_seconds=10.0)

    events: list[tuple[str, dict]] = []
    async for event_name, event_data in engine.execute_streaming(blueprint):
        events.append((event_name, event_data))

    event_names = [e[0] for e in events]

    # Should have a run_failed event
    assert "run_failed" in event_names, f"Expected run_failed, got: {event_names}"

    # Find the run_failed event
    run_failed_events = [e for e in events if e[0] == "run_failed"]
    assert len(run_failed_events) >= 1

    failed_data = run_failed_events[-1][1]
    assert failed_data["status"] == "failed"
    assert "timed out" in failed_data["error"]


@pytest.mark.asyncio
async def test_partial_results_preserved_on_timeout(monkeypatch) -> None:
    """Nodes that completed before timeout should have their events emitted."""
    monkeypatch.setattr(
        "fim_one.core.workflow.engine.get_executor",
        _patched_get_executor({"__default__": 30.0}),
    )
    blueprint = WorkflowBlueprint(
        nodes=[
            WorkflowNodeDef(id="start", type=NodeType.START, data={}),
            WorkflowNodeDef(id="slow", type=NodeType.LLM, data={}),
            WorkflowNodeDef(id="end", type=NodeType.END, data={}),
        ],
        edges=[
            WorkflowEdgeDef(id="e1", source="start", target="slow"),
            WorkflowEdgeDef(id="e2", source="slow", target="end"),
        ],
    )

    engine = WorkflowEngine(
        workflow_timeout_ms=2000,  # 2 second timeout
        run_id="test-partial",
        user_id="test-user",
        workflow_id="test-wf",
    )

    events: list[tuple[str, dict]] = []
    async for event_name, event_data in engine.execute_streaming(blueprint):
        events.append((event_name, event_data))

    event_names = [e[0] for e in events]

    # Start node should have completed
    start_completed = any(
        e[0] == "node_completed" and e[1].get("node_id") == "start"
        for e in events
    )
    assert start_completed, "Start node should have completed before timeout"

    # Should still end with run_failed
    assert "run_failed" in event_names


@pytest.mark.asyncio
async def test_custom_timeout_overrides_default(monkeypatch) -> None:
    """When a custom timeout is provided, it should override the default."""
    monkeypatch.setattr(
        "fim_one.core.workflow.engine.get_executor",
        _patched_get_executor({"__default__": 10.0}),
    )
    engine = WorkflowEngine(
        workflow_timeout_ms=1000,  # Custom 1s timeout
        run_id="test-custom-timeout",
        user_id="test-user",
        workflow_id="test-wf",
    )

    blueprint = _make_slow_blueprint(delay_seconds=10.0)

    events: list[tuple[str, dict]] = []
    async for event_name, event_data in engine.execute_streaming(blueprint):
        events.append((event_name, event_data))

    # Should have timed out
    run_failed = [e for e in events if e[0] == "run_failed"]
    assert len(run_failed) >= 1
    assert "timed out" in run_failed[-1][1]["error"]


@pytest.mark.asyncio
async def test_default_timeout() -> None:
    """When no custom timeout is set, the engine uses its default (600s = 600000ms)."""
    engine = WorkflowEngine(
        run_id="test-default",
        user_id="test-user",
        workflow_id="test-wf",
    )
    # Verify the internal default
    assert engine._workflow_timeout_ms == 600_000


@pytest.mark.asyncio
async def test_fast_workflow_completes_normally() -> None:
    """A workflow that finishes before timeout should complete normally."""
    engine = WorkflowEngine(
        workflow_timeout_ms=60_000,
        run_id="test-fast",
        user_id="test-user",
        workflow_id="test-wf",
    )

    blueprint = _make_fast_blueprint()

    events: list[tuple[str, dict]] = []
    async for event_name, event_data in engine.execute_streaming(blueprint):
        events.append((event_name, event_data))

    event_names = [e[0] for e in events]

    # Should complete successfully, no timeout
    assert "run_completed" in event_names
    assert "run_failed" not in event_names

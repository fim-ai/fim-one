"""The engine stops a run at a step that waits for a human, and goes on later (T10 P5).

A step that needs a decision returns ``NodeStatus.WAITING``. Nothing waits
in the process: once nothing else can run, the engine's last event is
``run_suspended`` with a state, and a later ``execute_streaming`` given
that state runs the waiting step again and carries on from there.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch

import pytest

from fim_one.core.workflow.engine import WorkflowEngine
from fim_one.core.workflow.parser import parse_blueprint
from fim_one.core.workflow.types import (
    ExecutionContext,
    NodeResult,
    NodeStatus,
    WorkflowBlueprint,
    WorkflowNodeDef,
)
from fim_one.core.workflow.variable_store import VariableStore


def _node(node_id: str, kind: str, **data: Any) -> dict[str, Any]:
    types = {"start": "START", "end": "END", "humanIntervention": "HUMAN_INTERVENTION",
             "connector": "CONNECTOR"}  # fmt: skip
    return {
        "id": node_id,
        "type": kind,
        "position": {"x": 0, "y": 0},
        "data": {"type": types[kind], **data},
    }


def _edge(source: str, target: str) -> dict[str, Any]:
    return {"id": f"{source}->{target}", "source": source, "target": target}


def _linear() -> WorkflowBlueprint:
    """start -> gate (asks a human) -> end"""
    return parse_blueprint(
        {
            "nodes": [
                _node("start_1", "start"),
                _node("gate", "humanIntervention"),
                _node("end_1", "end", output_mapping={"answer": "{{gate.output}}"}),
            ],
            "edges": [_edge("start_1", "gate"), _edge("gate", "end_1")],
        }
    )


class _Gate:
    """Stands in for a step that asks a human: waits until ``decision`` is set."""

    def __init__(self) -> None:
        self.decision: str | None = None
        self.asked = 0
        self.picked_up = 0

    async def execute(
        self, node: WorkflowNodeDef, store: VariableStore, context: ExecutionContext
    ) -> NodeResult:
        record = context.suspensions.get(node.id)
        if record is None:
            self.asked += 1
            record = {"confirmation_id": f"c-{node.id}-{self.asked}", "kind": "workflow_approval"}
        else:
            self.picked_up += 1
        if self.decision is None:
            return NodeResult(node_id=node.id, status=NodeStatus.WAITING, suspension=record)
        if self.decision == "reject":
            return NodeResult(node_id=node.id, status=NodeStatus.FAILED, error="rejected")
        await store.set(f"{node.id}.output", f"approved:{record['confirmation_id']}")
        return NodeResult(node_id=node.id, status=NodeStatus.COMPLETED, output="approved")


async def _run(
    engine: WorkflowEngine,
    bp: WorkflowBlueprint,
    *,
    inputs: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    return [event async for event in engine.execute_streaming(bp, inputs, resume_state=state)]


def _named(events: list[tuple[str, dict[str, Any]]], name: str) -> list[dict[str, Any]]:
    return [data for event, data in events if event == name]


def _stored(state: dict[str, Any]) -> dict[str, Any]:
    """The state as it comes back from a JSON column."""
    return dict(json.loads(json.dumps(state)))


@pytest.fixture()
def gate() -> Any:
    gate = _Gate()
    with patch(
        "fim_one.core.workflow.nodes.HumanInterventionExecutor.execute", side_effect=gate.execute
    ):
        yield gate


class TestSuspend:
    async def test_the_run_stops_at_the_waiting_step(self, gate: _Gate) -> None:
        events = await _run(WorkflowEngine(), _linear(), inputs={"q": "hello"})

        assert events[-1][0] == "run_suspended"
        assert _named(events, "run_completed") == [] and _named(events, "run_failed") == []
        (waiting,) = _named(events, "node_waiting")
        assert waiting["node_id"] == "gate"
        assert waiting["waiting_for"]["confirmation_id"] == "c-gate-1"
        suspended = events[-1][1]
        assert suspended["status"] == "waiting"
        assert suspended["waiting"] == [
            {"node_id": "gate", "confirmation_id": "c-gate-1", "kind": "workflow_approval"}
        ]
        # What follows the waiting step has not run.
        assert {d["node_id"] for d in _named(events, "node_completed")} == {"start_1"}

    async def test_the_state_is_json_and_holds_no_env_vars(self, gate: _Gate) -> None:
        engine = WorkflowEngine(env_vars={"TOKEN": "s3cret"})
        events = await _run(engine, _linear(), inputs={"q": "hello"})

        state = events[-1][1]["state"]
        assert _stored(state) == state
        assert "s3cret" not in json.dumps(state)
        assert state["variables"]["input.q"] == "hello"
        assert state["waiting"] == {
            "gate": {"confirmation_id": "c-gate-1", "kind": "workflow_approval"}
        }
        assert state["node_status"]["gate"] == "waiting"
        assert state["completed"] == ["start_1"]

    async def test_other_branches_finish_before_the_run_stops(self, gate: _Gate) -> None:
        ran: list[str] = []

        async def side_branch(node: Any, store: Any, ctx: Any) -> NodeResult:
            await asyncio.sleep(0.05)
            ran.append(node.id)
            return NodeResult(node_id=node.id, status=NodeStatus.COMPLETED, output="done")

        bp = parse_blueprint(
            {
                "nodes": [
                    _node("start_1", "start"),
                    _node("gate", "humanIntervention"),
                    _node("side", "connector"),
                    _node("end_1", "end"),
                ],
                "edges": [
                    _edge("start_1", "gate"),
                    _edge("start_1", "side"),
                    _edge("gate", "end_1"),
                    _edge("side", "end_1"),
                ],
            }
        )
        with patch("fim_one.core.workflow.nodes.ConnectorExecutor.execute", side_effect=side_branch):
            events = await _run(WorkflowEngine(), bp)

        assert events[-1][0] == "run_suspended"
        assert ran == ["side"]
        assert sorted(events[-1][1]["state"]["completed"]) == ["side", "start_1"]


class TestResume:
    async def test_an_approved_run_goes_on_after_the_step(self, gate: _Gate) -> None:
        bp = _linear()
        first = await _run(WorkflowEngine(), bp, inputs={"q": "hello"})
        state = _stored(first[-1][1]["state"])

        gate.decision = "approve"
        events = await _run(WorkflowEngine(), bp, state=state)

        assert events[0][0] == "run_resumed"
        assert _named(events, "run_started") == [] and _named(events, "node_started")[0][
            "node_id"
        ] == "end_1"
        assert [d["node_id"] for d in _named(events, "node_resumed")] == ["gate"]
        # The step picked up the request it made; it did not ask again, and
        # the step before it did not run a second time.
        assert (gate.asked, gate.picked_up) == (1, 1)
        assert [d["node_id"] for d in _named(events, "node_completed")] == ["gate", "end_1"]
        (done,) = _named(events, "run_completed")
        assert done["status"] == "completed"
        assert done["outputs"] == {"answer": "approved:c-gate-1"}

    async def test_a_rejected_run_fails_at_the_step(self, gate: _Gate) -> None:
        bp = _linear()
        first = await _run(WorkflowEngine(), bp)
        gate.decision = "reject"

        events = await _run(WorkflowEngine(), bp, state=_stored(first[-1][1]["state"]))

        (failed,) = _named(events, "node_failed")
        assert (failed["node_id"], failed["error"]) == ("gate", "rejected")
        assert _named(events, "run_failed")[0]["error_code"] == "workflow_step_failed"
        assert {d["node_id"] for d in _named(events, "node_skipped")} == {"end_1"}

    async def test_a_run_whose_decision_is_still_open_waits_again(self, gate: _Gate) -> None:
        bp = _linear()
        first = await _run(WorkflowEngine(), bp)

        again = await _run(WorkflowEngine(), bp, state=_stored(first[-1][1]["state"]))

        assert again[-1][0] == "run_suspended"
        assert again[-1][1]["state"]["waiting"] == first[-1][1]["state"]["waiting"]
        assert gate.asked == 1

    async def test_two_approvals_are_picked_up_one_at_a_time(self) -> None:
        """Parallel approvals: deciding one continues its branch, the other still waits."""
        decided: set[str] = set()

        async def execute(node: Any, store: Any, ctx: Any) -> NodeResult:
            record = ctx.suspensions.get(node.id) or {"confirmation_id": f"c-{node.id}"}
            if node.id not in decided:
                return NodeResult(node_id=node.id, status=NodeStatus.WAITING, suspension=record)
            return NodeResult(node_id=node.id, status=NodeStatus.COMPLETED, output="ok")

        bp = parse_blueprint(
            {
                "nodes": [
                    _node("start_1", "start"),
                    _node("gate_a", "humanIntervention"),
                    _node("gate_b", "humanIntervention"),
                    _node("end_1", "end"),
                ],
                "edges": [
                    _edge("start_1", "gate_a"),
                    _edge("start_1", "gate_b"),
                    _edge("gate_a", "end_1"),
                    _edge("gate_b", "end_1"),
                ],
            }
        )
        with patch(
            "fim_one.core.workflow.nodes.HumanInterventionExecutor.execute", side_effect=execute
        ):
            first = await _run(WorkflowEngine(), bp)
            assert sorted(first[-1][1]["state"]["waiting"]) == ["gate_a", "gate_b"]

            decided.add("gate_a")
            second = await _run(WorkflowEngine(), bp, state=_stored(first[-1][1]["state"]))
            assert second[-1][0] == "run_suspended"
            assert sorted(second[-1][1]["state"]["waiting"]) == ["gate_b"]

            decided.add("gate_b")
            third = await _run(WorkflowEngine(), bp, state=_stored(second[-1][1]["state"]))
        assert _named(third, "run_completed")[0]["status"] == "completed"

    async def test_time_spent_waiting_does_not_count_toward_the_run_limit(
        self, gate: _Gate
    ) -> None:
        """T10 W5: an approval may wait for hours; the run limit is minutes."""
        bp = _linear()
        first = await _run(WorkflowEngine(workflow_timeout_ms=300), bp)
        state = _stored(first[-1][1]["state"])
        assert state["active_ms"] < 300

        await asyncio.sleep(0.4)  # longer than the whole-run limit
        gate.decision = "approve"
        events = await _run(WorkflowEngine(workflow_timeout_ms=300), bp, state=state)

        assert _named(events, "run_failed") == []
        (done,) = _named(events, "run_completed")
        # The run's time is what it spent executing, before and after.
        assert state["active_ms"] <= done["duration_ms"] < 300

    async def test_time_already_spent_counts_toward_the_run_limit(self, gate: _Gate) -> None:
        bp = _linear()
        first = await _run(WorkflowEngine(workflow_timeout_ms=300), bp)
        state = {**_stored(first[-1][1]["state"]), "active_ms": 300}
        gate.decision = "approve"

        events = await _run(WorkflowEngine(workflow_timeout_ms=300), bp, state=state)

        assert _named(events, "run_failed")[0]["error_code"] == "workflow_run_timed_out"

    async def test_tokens_spent_before_waiting_stay_in_the_total(self, gate: _Gate) -> None:
        bp = _linear()
        first = await _run(WorkflowEngine(), bp)
        state = {**_stored(first[-1][1]["state"]), "total_tokens": 40}
        gate.decision = "approve"

        events = await _run(WorkflowEngine(), bp, state=state)

        assert _named(events, "run_completed")[0]["total_tokens"] == 40


class TestEndingWhileAStepWaits:
    async def test_a_stop_workflow_failure_ends_the_run(self, gate: _Gate) -> None:
        bp = parse_blueprint(
            {
                "nodes": [
                    _node("start_1", "start"),
                    _node("gate", "humanIntervention"),
                    _node("broken", "connector"),  # no connector chosen: fails
                    _node("end_1", "end"),
                ],
                "edges": [
                    _edge("start_1", "gate"),
                    _edge("start_1", "broken"),
                    _edge("gate", "end_1"),
                    _edge("broken", "end_1"),
                ],
            }
        )
        events = await _run(WorkflowEngine(), bp)

        assert _named(events, "run_suspended") == []
        assert events[-1][0] == "run_failed"
        assert "gate" in {d["node_id"] for d in _named(events, "node_skipped")}

    async def test_cancelling_is_recorded_as_cancelled(self) -> None:
        cancel = asyncio.Event()

        async def slow(node: Any, store: Any, ctx: Any) -> NodeResult:
            cancel.set()
            await asyncio.sleep(0.05)
            return NodeResult(node_id=node.id, status=NodeStatus.COMPLETED, output="ok")

        bp = parse_blueprint(
            {
                "nodes": [
                    _node("start_1", "start"),
                    _node("a", "connector"),
                    _node("b", "connector"),
                    _node("end_1", "end"),
                ],
                "edges": [_edge("start_1", "a"), _edge("a", "b"), _edge("b", "end_1")],
            }
        )
        with patch("fim_one.core.workflow.nodes.ConnectorExecutor.execute", side_effect=slow):
            events = await _run(WorkflowEngine(cancel_event=cancel), bp)

        assert events[-1][0] == "run_completed"
        assert events[-1][1]["status"] == "cancelled"
        assert events[-1][1]["error_code"] == "workflow_run_cancelled"
        assert {d["node_id"] for d in _named(events, "node_skipped")} == {"b", "end_1"}


class TestOwnershipCannotBeEstablished:
    """The database does not answer the question "is this run still ours"."""

    @staticmethod
    def _bp() -> WorkflowBlueprint:
        return parse_blueprint(
            {
                "nodes": [
                    _node("start_1", "start"),
                    _node("call", "connector"),
                    _node("end_1", "end"),
                ],
                "edges": [_edge("start_1", "call"), _edge("call", "end_1")],
            }
        )

    async def test_a_step_with_side_effects_is_not_run(self) -> None:
        asked = 0
        called: list[str] = []

        async def unknown() -> bool:
            nonlocal asked
            asked += 1
            raise OSError("database unreachable")

        async def call(node: Any, store: Any, ctx: Any) -> NodeResult:
            called.append(node.id)
            return NodeResult(node_id=node.id, status=NodeStatus.COMPLETED, output="sent")

        context = ExecutionContext(run_id="r", user_id="u", workflow_id="w", still_owned=unknown)
        with (
            patch("fim_one.core.workflow.nodes.ConnectorExecutor.execute", side_effect=call),
            patch("fim_one.core.workflow.engine._OWNERSHIP_RETRY_SECONDS", 0),
        ):
            events = [e async for e in WorkflowEngine().execute_streaming(self._bp(), {}, context)]

        assert called == []
        (failed,) = _named(events, "node_failed")
        assert failed["node_id"] == "call" and "could not confirm" in failed["error"]
        assert events[-1][0] == "run_failed"
        assert asked >= 3  # a bounded retry first; Start ran without the answer

    async def test_a_hiccup_that_passes_does_not_stop_anything(self) -> None:
        answers = iter([OSError("blip"), True, True, True, True, True])

        async def flaky() -> bool:
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer

        async def call(node: Any, store: Any, ctx: Any) -> NodeResult:
            return NodeResult(node_id=node.id, status=NodeStatus.COMPLETED, output="sent")

        context = ExecutionContext(run_id="r", user_id="u", workflow_id="w", still_owned=flaky)
        with (
            patch("fim_one.core.workflow.nodes.ConnectorExecutor.execute", side_effect=call),
            patch("fim_one.core.workflow.engine._OWNERSHIP_RETRY_SECONDS", 0),
        ):
            events = [e async for e in WorkflowEngine().execute_streaming(self._bp(), {}, context)]

        assert _named(events, "run_completed")[0]["status"] == "completed"

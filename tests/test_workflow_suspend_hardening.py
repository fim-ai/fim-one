"""Suspend and resume under attack (T10 P5, findings of the adversarial review).

Each class is one finding: what could go wrong with a run that waits for a
person, and the behaviour that closes it. Driven like
``test_workflow_suspend_resume.py``, whose rig these tests share.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import select, update

from fim_one.core.workflow import run_service as rs
from fim_one.core.workflow.rate_limiter import WorkflowRateLimiter
from fim_one.core.workflow.run_recovery import WorkflowRunSupervisor, install_resume_on_decision
from fim_one.db.models.channel import ConfirmationRequest
from fim_one.db.models.user import User
from fim_one.db.models.workflow import WorkflowRun
from tests.test_workflow_suspend_resume import (  # noqa: F401
    APPROVAL,
    BLUEPRINT,
    Rig,
    _Adapter,
    _CountingEngine,
    _edge,
    _new_service,
    _node,
    database,
    flagged,
    hooked,
    rig,
)

pytestmark = pytest.mark.usefixtures("human_intervention_enabled")


async def _requests(rig: Rig, run_id: str) -> list[ConfirmationRequest]:
    async with rig.factory() as s:
        return list(
            (
                await s.execute(
                    select(ConfirmationRequest)
                    .where(ConfirmationRequest.workflow_run_id == run_id)
                    .order_by(ConfirmationRequest.created_at)
                )
            ).scalars()
        )


async def _settled(
    rig: Rig, run_id: str, want: tuple[str, ...], timeout: float = 10.0
) -> WorkflowRun:
    """The run once it is in one of *want* and nothing of it executes here."""
    end = time.monotonic() + timeout
    while True:
        run = await rig.run(run_id)
        if run.status in want and not rig.service.active_run_ids():
            return run
        assert time.monotonic() < end, run.status
        await asyncio.sleep(0.02)


def _with_node_data(blueprint: dict[str, Any], node_id: str, **data: Any) -> dict[str, Any]:
    nodes = [
        {**n, "data": {**n["data"], **data}} if n["id"] == node_id else dict(n)
        for n in blueprint["nodes"]
    ]
    return {**blueprint, "nodes": nodes}


# ---------------------------------------------------------------------------
# L6. A person's "no" is not retried
# ---------------------------------------------------------------------------


class TestAnAnswerIsNotRetried:
    async def test_a_rejected_approval_fails_the_step_whatever_its_retry_count(
        self, rig: Rig, hooked: Any
    ) -> None:
        await rig.workflow(_with_node_data(BLUEPRINT, "human_1", retry_count=2, retry_delay_ms=0))
        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)

        await rig.decide(request.id, "reject", note="no")

        run = await rig.ended(outcome.run_id, timeout=5)  # asked again, it would wait on
        assert run.status == "failed"
        assert [r.status for r in await _requests(rig, run.id)] == ["rejected"]  # not asked again

    async def test_an_approval_nobody_answered_is_not_asked_again_either(self, rig: Rig) -> None:
        await rig.workflow(_with_node_data(BLUEPRINT, "human_1", retry_count=2, retry_delay_ms=0))
        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)
        await rig.flip(request.id, expires_at=datetime.now(UTC) - timedelta(seconds=1))

        await WorkflowRunSupervisor(rig.service).sweep()

        run = await rig.ended(outcome.run_id, timeout=5)  # asked again, it would wait on
        assert run.status == "failed"
        assert [r.status for r in await _requests(rig, run.id)] == ["expired"]

    async def test_a_rejected_connector_confirmation_is_not_asked_again(
        self, rig: Rig, flagged: _Adapter, hooked: Any
    ) -> None:
        wf = await rig.workflow()
        await rig.workflow(_with_node_data(wf.blueprint, "connector_1", retry_count=2, retry_delay_ms=0))
        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)

        await rig.decide(request.id, "reject")

        run = await rig.ended(outcome.run_id, timeout=5)  # asked again, it would wait on
        assert run.status == "failed" and flagged.calls == []
        assert [r.status for r in await _requests(rig, run.id)] == ["rejected"]


# ---------------------------------------------------------------------------
# L1, L2, L11. Closing a run on its record races nothing
# ---------------------------------------------------------------------------


class TestClosingARunOnItsRecord:
    async def test_a_cancel_is_not_lost_to_a_continuation_claiming_first(self, rig: Rig) -> None:
        """The cancel is on its way when another process claims the waiting
        run for continuation. The run is cancelled all the same, under the
        status it has by then."""
        from sqlalchemy.ext.asyncio import AsyncSession

        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)
        await rig.flip(request.id, status="approved")
        other = _new_service()
        real_execute = AsyncSession.execute
        fired: dict[str, Any] = {}

        async def execute(self: Any, statement: Any, *args: Any, **kwargs: Any) -> Any:
            text = str(statement)
            if text.startswith("UPDATE workflow_runs") and "completed_at" in text and not fired:
                fired["started"] = await other.resume_run(outcome.run_id)
                assert fired["started"] is not None
            return await real_execute(self, statement, *args, **kwargs)

        with patch.object(AsyncSession, "execute", execute):
            cancelled = await rig.service.cancel_stored_run(outcome.run_id)

        await fired["started"].wait()
        run = await rig.run(outcome.run_id)
        assert cancelled is True
        assert run.status == "cancelled"

    async def test_cancelling_a_run_that_has_ended_says_so(self, rig: Rig) -> None:
        from fim_one.web.api import workflows as wf_api
        from fim_one.web.exceptions import AppError

        outcome = await rig.start()
        assert await rig.service.cancel_stored_run(outcome.run_id) is True
        assert await rig.service.cancel_stored_run(outcome.run_id) is False

        async with rig.factory() as db:
            with pytest.raises(AppError) as refused:
                await wf_api.cancel_workflow_run(
                    rig.wf.id, outcome.run_id, current_user=rig.owner, db=db
                )
        assert (refused.value.status_code, refused.value.error_code) == (
            409,
            "workflow_run_already_finished",
        )
        assert refused.value.detail_args == {"status": "cancelled"}

    async def test_a_result_is_not_written_over_a_run_closed_elsewhere(self, rig: Rig) -> None:
        """Cancelled from another process while it ran here: the record
        stays cancelled, the caller is not told it completed, the owner's
        webhook is not called."""
        release = asyncio.Event()

        class _Finishes(_CountingEngine):
            async def execute_streaming(self, *args: Any, **kwargs: Any) -> Any:
                await release.wait()
                yield "run_completed", {"status": "completed", "outputs": {"done": True}}

        here = rs.WorkflowRunService(engine_factory=_Finishes)
        started = await here.start_run(
            await rig.workflow(), caller_id=rig.owner.id, inputs={}, mode=rs.RunMode.INTERACTIVE
        )
        assert await _new_service().cancel_stored_run(started.run_id) is True

        with patch.object(here, "_fire_webhook") as webhook:
            release.set()
            outcome = await started.wait()

        run = await rig.run(started.run_id)
        assert (run.status, run.error, run.outputs) == ("cancelled", "Execution cancelled", None)
        assert (outcome.status, outcome.outputs) == ("cancelled", {})
        assert outcome.error_code == "workflow_run_cancelled"
        webhook.assert_not_called()

    async def test_a_run_stamped_since_it_was_judged_dead_is_not_closed(self, rig: Rig) -> None:
        """The sweep picked the run as stale; before it closes it, the run's
        process stamps it. The close is conditional on the stamp still being old."""
        run_id = str(uuid.uuid4())
        async with rig.factory() as db:
            db.add(
                WorkflowRun(
                    id=run_id,
                    workflow_id=rig.wf.id,
                    user_id=rig.owner.id,
                    blueprint_snapshot=BLUEPRINT,
                    status="running",
                    worker_id="another-live-process",
                    started_at=datetime.now(UTC) - timedelta(hours=1),
                    heartbeat_at=datetime.now(UTC),
                )
            )
            await db.commit()

        closed = await rig.service._end_stored_run(
            run_id,
            ("pending", "running"),
            "failed",
            rs.RUN_INTERRUPTED_ERROR,
            stale_before=datetime.now(UTC) - timedelta(seconds=90),
        )

        assert closed is None
        assert (await rig.run(run_id)).status == "running"


# ---------------------------------------------------------------------------
# L4. What a waiting run keeps
# ---------------------------------------------------------------------------


class TestStoredState:
    async def test_it_is_encrypted_and_only_names_what_the_run_waits_on(self, rig: Rig) -> None:
        import json

        outcome = await rig.start(inputs={"q": "the launch code is 0000"})
        request = await rig.request_of(outcome.run_id)

        stored = (await rig.run(outcome.run_id)).suspend_state

        assert "launch code" not in json.dumps(stored)
        assert set(stored) == {"version", "waiting", "sealed"}
        assert stored["waiting"] == {
            "human_1": {
                "confirmation_id": request.id,
                "kind": "workflow_approval",
                "expires_at": stored["waiting"]["human_1"]["expires_at"],
            }
        }
        assert rs.unseal_state(stored)["variables"]["start_1.q"] == "the launch code is 0000"

    async def test_a_step_output_is_kept_once(self, rig: Rig) -> None:
        import json

        marker = "only-once-" + uuid.uuid4().hex
        outcome = await rig.start(inputs={"q": marker})

        state = rs.unseal_state((await rig.run(outcome.run_id)).suspend_state)

        # In the variables (start_1.q, start_1.output, input.q), not again
        # under the Start step's stored result.
        assert state["node_results"]["start_1"]["output"] is None
        assert json.dumps(state["node_results"]).count(marker) == 0
        assert json.dumps(state["variables"]).count(marker) == 3

    async def test_a_run_whose_state_is_over_the_limit_fails_instead_of_waiting(
        self, rig: Rig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WORKFLOW_SUSPEND_STATE_MAX_BYTES", "2000")

        outcome = await rig.start(inputs={"q": "x" * 5000})

        assert outcome.status == "failed"
        assert outcome.error_code == "workflow_suspend_state_too_large"
        assert "over the limit of 2,000 bytes" in (outcome.error or "")
        run = await rig.run(outcome.run_id)
        assert (run.status, run.suspend_state) == ("failed", None)
        # Nobody is left with a request for a run that is not waiting.
        assert [r.status for r in await _requests(rig, run.id)] == ["expired"]

    async def test_a_state_this_instance_cannot_decrypt_closes_the_run(self, rig: Rig) -> None:
        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)
        async with rig.factory() as db:
            run = await db.get(WorkflowRun, outcome.run_id)
            assert run is not None
            run.suspend_state = {**run.suspend_state, "sealed": "gAAAAA-not-ours"}
            await db.commit()
        await rig.flip(request.id, status="approved")

        assert await rig.service.resume_run(outcome.run_id) is None

        run = await rig.run(outcome.run_id)
        assert (run.status, run.error) == ("failed", rs.RUN_NOT_RESUMABLE_ERROR)

    async def test_sealing_does_not_run_on_the_event_loop(self, rig: Rig) -> None:
        import threading

        threads: list[str] = []
        seal = rs.seal_state

        def sealing(state: dict[str, Any]) -> dict[str, Any]:
            threads.append(threading.current_thread().name)
            return seal(state)

        with patch.object(rs, "seal_state", sealing):
            assert (await rig.start()).status == "waiting"
        assert threads and threading.current_thread().name not in threads


# ---------------------------------------------------------------------------
# L5. The sweep reaches every waiting run
# ---------------------------------------------------------------------------


class TestSweepReachesEveryWaitingRun:
    async def test_runs_beyond_one_batch_are_continued_oldest_first(self, rig: Rig) -> None:
        """More waiting runs than one batch, the decided ones among the
        newest: a sweep that looked at "the first N in no order" could
        leave them out for good."""
        from fim_one.core.workflow import run_recovery

        ids = [(await rig.start()).run_id for _ in range(7)]
        async with rig.factory() as db:  # creation order, a second apart
            for age, run_id in enumerate(reversed(ids)):
                await db.execute(
                    update(WorkflowRun)
                    .where(WorkflowRun.id == run_id)
                    .values(created_at=datetime.now(UTC) - timedelta(seconds=age))
                )
            await db.commit()
        decided = [ids[1], ids[5], ids[6]]
        for run_id in decided:
            await rig.flip((await rig.request_of(run_id)).id, status="approved")

        with patch.object(run_recovery, "_SWEEP_BATCH", 2):
            report = await WorkflowRunSupervisor(rig.service).sweep()

        assert report.resumed == decided  # all of them, oldest first
        for run_id in decided:
            assert (await rig.ended(run_id)).status == "completed"
        assert {(await rig.run(i)).status for i in ids if i not in decided} == {"waiting"}

    async def test_a_batch_costs_a_fixed_number_of_queries(self, rig: Rig) -> None:
        from sqlalchemy import event

        for _ in range(6):
            await rig.start()
        engine = rig.factory.kw["bind"].sync_engine
        statements: list[str] = []

        def count(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", count)
        try:
            await WorkflowRunSupervisor(rig.service)._resume_settled_runs(
                __import__("fim_one.core.workflow.run_recovery", fromlist=["x"]).SweepReport()
            )
        finally:
            event.remove(engine, "before_cursor_execute", count)
        selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
        # ids, the batch's states, the batch's requests: not one per run.
        assert len(selects) == 3, selects


# ---------------------------------------------------------------------------
# M1. What was approved is what runs
# ---------------------------------------------------------------------------


class TestWhatWasApprovedIsWhatRuns:
    async def test_an_env_var_edited_while_the_request_waits_does_not_change_the_call(
        self, rig: Rig, flagged: _Adapter, hooked: Any
    ) -> None:
        wf = await rig.workflow()
        await rig.workflow(
            _with_node_data(wf.blueprint, "connector_1", parameters={"target": "{{env.TARGET}}"})
        )
        env = {"TARGET": "staging-db"}
        with patch.object(rs, "decrypt_workflow_env", lambda _wf: dict(env)):
            outcome = await rig.start()
            assert outcome.status == "waiting"
            request = await rig.request_of(outcome.run_id)
            shown = request.payload["tool_args"]
            assert shown == {"target": "staging-db"}

            env["TARGET"] = "PROD-db"  # the author edits the env vars meanwhile
            await rig.decide(request.id, "approve")
            run = await rig.ended(outcome.run_id)

        assert run.status == "completed"
        assert flagged.calls == [shown]

    async def test_a_variable_another_branch_wrote_meanwhile_does_not_change_the_call(
        self, rig: Rig, flagged: _Adapter, hooked: Any
    ) -> None:
        wf = await rig.workflow()
        connector = next(n for n in wf.blueprint["nodes"] if n["id"] == "connector_1")
        connector = {
            **connector,
            "data": {
                **connector["data"],
                "parameters": {"target": "{{approval_result.decision_note}}"},
            },
        }
        await rig.workflow(
            {
                "schema_version": 2,
                "nodes": [
                    _node("start_1", "start", "START"),
                    APPROVAL,
                    connector,
                    _node("end_1", "end", "END"),
                ],
                "edges": [
                    _edge("start_1", "human_1"),
                    _edge("start_1", "connector_1"),
                    _edge("human_1", "end_1"),
                    _edge("connector_1", "end_1"),
                ],
                "viewport": {"x": 0, "y": 0, "zoom": 1},
            }
        )
        outcome = await rig.start()
        assert outcome.status == "waiting"
        requests = {r.kind: r for r in await _requests(rig, outcome.run_id)}
        shown = requests["confirmation"].payload["tool_args"]

        # The approval step is decided first and writes the variable the
        # connector's parameter refers to; the run waits again on the other.
        await rig.decide(requests["workflow_approval"].id, "approve", note="prod-ledger")
        for _ in range(500):
            run = await rig.run(outcome.run_id)
            if run.status == "waiting" and "human_1" not in run.suspend_state["waiting"]:
                break
            await asyncio.sleep(0.02)
        await rig.decide(requests["confirmation"].id, "approve")
        run = await rig.ended(outcome.run_id)

        assert run.status == "completed"
        assert flagged.calls == [shown]

    @pytest.mark.parametrize("what", ["action", "connector"])
    async def test_an_action_or_connector_edited_meanwhile_is_not_run(
        self, rig: Rig, flagged: _Adapter, hooked: Any, what: str
    ) -> None:
        from fim_one.db.models.connector import Connector, ConnectorAction

        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)
        async with rig.factory() as db:
            if what == "action":
                await db.execute(
                    update(ConnectorAction)
                    .where(ConnectorAction.id == "wipe-1")
                    .values(path="/wipe-everything")
                )
            else:
                await db.execute(
                    update(Connector)
                    .where(Connector.id == request.payload["connector_id"])
                    .values(base_url="https://elsewhere.example.test")
                )
            await db.commit()

        await rig.decide(request.id, "approve")

        run = await rig.ended(outcome.run_id)
        assert run.status == "failed"
        assert "changed after the confirmation was requested" in (
            run.node_results["connector_1"]["error"]
        )
        assert flagged.calls == []

    async def test_the_inbox_shows_the_arguments_that_will_be_sent(
        self, rig: Rig, flagged: _Adapter
    ) -> None:
        from fim_one.web.api import workflows as wf_api

        outcome = await rig.start(inputs={"q": "ledger-9"})
        request = await rig.request_of(outcome.run_id)
        async with rig.factory() as db:
            (item,) = (await wf_api.approval_inbox(current_user=rig.owner, db=db)).data["items"]
        assert item["arguments"] == request.payload["tool_args"] == {"target": "ledger-9"}


# ---------------------------------------------------------------------------
# M2. A continuation is admitted again, takes a slot, and waiting is capped
# ---------------------------------------------------------------------------


class TestAContinuationIsAdmittedAgain:
    async def test_a_user_disabled_while_the_run_waited_does_not_get_it_continued(
        self, rig: Rig, hooked: Any
    ) -> None:
        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)
        async with rig.factory() as s:
            await s.execute(update(User).where(User.id == rig.owner.id).values(is_active=False))
            await s.commit()

        await rig.decide(request.id, "approve", by=rig.admin)

        run = await rig.ended(outcome.run_id)
        assert run.status == "failed"
        assert "could not continue after its approval" in (run.error or "")
        assert run.node_results.get("end_1", {}).get("status") != "completed"

    async def test_what_the_gate_refuses_now_ends_the_run_with_that_refusal(
        self, rig: Rig, hooked: Any
    ) -> None:
        """Quota used up while the run waited."""
        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)
        asked: list[tuple[str, str | None]] = []

        class Gate:
            async def check(self, principal: Any, binding: Any = None) -> None:
                asked.append((principal.user_id, getattr(binding, "id", None)))
                raise rs.RunRefusedError("token_quota_exceeded", 429, "Token quota exceeded")

        with patch.object(rig.service, "gate", Gate()):
            await rig.decide(request.id, "approve")
            run = await rig.ended(outcome.run_id)

        assert asked == [(rig.owner.id, rig.wf.id)]
        assert run.status == "failed"
        assert run.error == (
            "The run could not continue after its approval: Token quota exceeded "
            "(token_quota_exceeded)"
        )
        assert run.suspend_state is None and _CountingEngine.continued == []

    async def test_a_trigger_run_of_a_workflow_switched_off_meanwhile_is_not_continued(
        self, rig: Rig, hooked: Any
    ) -> None:
        from fim_one.db.models.workflow import Workflow

        outcome = await rig.start(caller_id=None, mode=rs.RunMode.TRIGGER)
        request = await rig.request_of(outcome.run_id)
        async with rig.factory() as s:
            await s.execute(update(Workflow).where(Workflow.id == rig.wf.id).values(is_active=False))
            await s.commit()

        await rig.decide(request.id, "approve")

        run = await rig.ended(outcome.run_id)
        assert run.status == "failed" and "(workflow_inactive)" in (run.error or "")

    async def test_the_human_intervention_switch_turned_off_meanwhile_stops_it(
        self, rig: Rig, hooked: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)
        monkeypatch.setenv("WORKFLOW_HUMAN_INTERVENTION_ENABLED", "false")

        await rig.decide(request.id, "approve")

        run = await rig.ended(outcome.run_id)
        assert run.status == "failed" and "(human_intervention_disabled)" in (run.error or "")


class TestAContinuationTakesASlot:
    @staticmethod
    def _capped(cap: int) -> rs.WorkflowRunService:
        return rs.WorkflowRunService(
            engine_factory=_CountingEngine,
            rate_limiter=WorkflowRateLimiter(max_runs_per_minute=1000, max_concurrent_runs=cap),
        )

    async def test_parked_runs_decided_together_do_not_exceed_the_concurrent_limit(
        self, rig: Rig
    ) -> None:
        from fim_one.core.workflow.types import NodeResult, NodeStatus

        service = self._capped(1)
        remove = install_resume_on_decision(service)
        running = peak = 0
        release = asyncio.Event()

        async def held_end(node: Any, store: Any, ctx: Any) -> Any:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await release.wait()
            running -= 1
            return NodeResult(node_id=node.id, status=NodeStatus.COMPLETED, output={})

        try:
            with patch("fim_one.core.workflow.nodes.EndExecutor.execute", side_effect=held_end):
                ids = []
                for _ in range(5):
                    outcome = await rig.start(service=service)
                    assert outcome.status == "waiting"
                    ids.append(outcome.run_id)
                for run_id in ids:
                    await rig.decide((await rig.request_of(run_id)).id, "approve")
                await asyncio.sleep(0.6)

                assert peak == 1
                # None was dropped: the rest are queued, shown as running,
                # registered here (so stamped, and not taken for dead).
                assert sorted(service.active_run_ids()) == sorted(ids)
                assert {(await rig.run(i)).status for i in ids} == {"running"}
                assert (
                    await WorkflowRunSupervisor(service, stale_seconds=90).sweep()
                ).closed == []

                release.set()
                for run_id in ids:
                    assert (await rig.ended(run_id)).status == "completed"
            assert peak == 1
        finally:
            remove()

    async def test_a_queued_continuation_can_be_cancelled(self, rig: Rig) -> None:
        service = self._capped(1)
        outcome = await rig.start(service=service)
        await rig.flip((await rig.request_of(outcome.run_id)).id, status="approved")
        # Something else of this user's holds the only slot.
        await service.rate_limiter.acquire(rig.owner.id, "another-run")

        started = await service.resume_run(outcome.run_id)
        assert started is not None
        await asyncio.sleep(0.1)
        assert not started.done and _CountingEngine.continued == []  # queued

        assert service.cancel_run(outcome.run_id) is True
        ended = await asyncio.wait_for(started.wait(), timeout=5)

        assert ended.status == "cancelled"
        assert (await rig.run(outcome.run_id)).status == "cancelled"


class TestWaitingRunsAreCapped:
    async def test_a_run_over_the_cap_fails_at_the_step_instead_of_waiting(
        self, rig: Rig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WORKFLOW_MAX_WAITING_RUNS_PER_USER", "2")
        first, second = (await rig.start()), (await rig.start())
        assert (first.status, second.status) == ("waiting", "waiting")

        third = await rig.start()

        assert third.status == "failed"
        assert third.error_code == "workflow_waiting_limit_reached"
        assert "2 of this user's runs are already waiting" in (third.error or "")
        assert [r.status for r in await _requests(rig, third.run_id)] == ["expired"]
        # The ones already waiting are untouched, and once one is decided
        # there is room again.
        assert {(await rig.run(o.run_id)).status for o in (first, second)} == {"waiting"}
        assert await rig.service.cancel_stored_run(first.run_id) is True
        assert (await rig.start()).status == "waiting"

    async def test_another_users_waiting_runs_do_not_count(
        self, rig: Rig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WORKFLOW_MAX_WAITING_RUNS_PER_USER", "1")
        assert (await rig.start()).status == "waiting"
        # A trigger run is the owner's too; the administrator's is not.
        async with rig.factory() as s:
            s.add(
                WorkflowRun(
                    workflow_id=rig.wf.id,
                    user_id=rig.admin.id,
                    blueprint_snapshot=BLUEPRINT,
                    status="waiting",
                )
            )
            await s.commit()
        assert await rig.service._waiting_limit_reached(rig.admin.id) is True
        monkeypatch.setenv("WORKFLOW_MAX_WAITING_RUNS_PER_USER", "0")  # no limit
        assert await rig.service._waiting_limit_reached(rig.owner.id) is False

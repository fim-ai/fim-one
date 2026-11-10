"""Suspend and resume under attack (T10 P5, findings of the adversarial review).

Each class is one finding: what could go wrong with a run that waits for a
person, and the behaviour that closes it. Driven like
``test_workflow_suspend_resume.py``, whose rig these tests share.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import select, update

from fim_one.core.workflow import run_service as rs
from fim_one.core.assembly.confirmed_call import open_call
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

        from fim_one.core.workflow.types import NodeResult, NodeStatus

        release = asyncio.Event()

        async def held_end(node: Any, store: Any, ctx: Any) -> Any:
            # The continuation is still at work when the cancel lands.
            await release.wait()
            return NodeResult(node_id=node.id, status=NodeStatus.COMPLETED, output={})

        with (
            patch("fim_one.core.workflow.nodes.EndExecutor.execute", side_effect=held_end),
            patch.object(AsyncSession, "execute", execute),
        ):
            cancelled = await rig.service.cancel_stored_run(outcome.run_id)
            release.set()
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
            stale_seconds=90,
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

        run = await rig.run(outcome.run_id)
        stored = run.suspend_state

        assert "launch code" not in json.dumps(stored)
        assert set(stored) == {"version", "sealed"}
        # What the sweep and the API need is beside it, in a column of its own.
        assert run.waiting_on == {
            "human_1": {
                "confirmation_id": request.id,
                "kind": "workflow_approval",
                "expires_at": run.waiting_on["human_1"]["expires_at"],
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
            sent, shown = open_call(request.payload)
            # The approver sees that an env var goes in, not its value.
            assert shown == {"target": "{{env.TARGET}}"}
            assert sent == {"target": "staging-db"}
            assert "staging-db" not in json.dumps(request.payload)

            env["TARGET"] = "PROD-db"  # the author edits the env vars meanwhile
            await rig.decide(request.id, "approve")
            run = await rig.ended(outcome.run_id)

        assert run.status == "completed"
        assert flagged.calls == [{"target": "staging-db"}]

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
        shown, _ = open_call(requests["confirmation"].payload)

        # The approval step is decided first and writes the variable the
        # connector's parameter refers to; the run waits again on the other.
        await rig.decide(requests["workflow_approval"].id, "approve", note="prod-ledger")
        for _ in range(500):
            run = await rig.run(outcome.run_id)
            if run.status == "waiting" and "human_1" not in run.waiting_on:
                break
            await asyncio.sleep(0.02)
        await rig.decide(requests["confirmation"].id, "approve")
        run = await rig.ended(outcome.run_id)

        assert run.status == "completed"
        assert flagged.calls == [shown]

    @pytest.mark.parametrize("what", ["auth", "fallback", "credential"])
    async def test_a_change_of_who_the_call_is_made_as_is_not_run(
        self, rig: Rig, flagged: _Adapter, hooked: Any, what: str
    ) -> None:
        """Approved as one identity, it is not sent as another."""
        from fim_one.core.security.encryption import encrypt_credential
        from fim_one.db.models.connector import Connector
        from fim_one.db.models.connector_credential import ConnectorCredential

        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)
        connector_id = request.payload["connector_id"]
        async with rig.factory() as db:
            if what == "auth":
                await db.execute(
                    update(Connector)
                    .where(Connector.id == connector_id)
                    .values(auth_config={"token_header": "X-Other"})
                )
            elif what == "fallback":
                connector = await db.get(Connector, connector_id)
                assert connector is not None
                connector.allow_fallback = not connector.allow_fallback
            else:  # the runner saves a credential of their own meanwhile
                db.add(
                    ConnectorCredential(
                        connector_id=connector_id,
                        user_id=rig.owner.id,
                        credentials_blob=encrypt_credential({"token": "mine"}),
                    )
                )
            await db.commit()

        await rig.decide(request.id, "approve")

        run = await rig.ended(outcome.run_id)
        assert run.status == "failed" and flagged.calls == []
        assert "changed after the confirmation was requested" in (
            run.node_results["connector_1"]["error"]
        )

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
        assert item["arguments"] == open_call(request.payload)[1] == {"target": "ledger-9"}

    async def test_an_env_value_inside_a_longer_argument_is_masked_too(self) -> None:
        from fim_one.core.assembly.confirmed_call import seal_call

        sealed = seal_call(
            {"url": "https://api.example.test/?key=s3cr3t&n=1", "n": 5, "nested": ["s3cr3t", True]},
            {"API_KEY": "s3cr3t", "EMPTY": ""},
        )
        sent, shown = open_call({"sealed": sealed})
        assert sent == {
            "url": "https://api.example.test/?key=s3cr3t&n=1",
            "n": 5,
            "nested": ["s3cr3t", True],
        }
        assert shown == {
            "url": "https://api.example.test/?key={{env.API_KEY}}&n=1",
            "n": 5,
            "nested": ["{{env.API_KEY}}", True],
        }
        assert "s3cr3t" not in sealed and open_call({"sealed": "not-ours"}) == (None, None)


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
    async def test_at_the_cap_a_workflow_that_waits_is_refused_before_any_step(
        self, rig: Rig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WORKFLOW_MAX_WAITING_RUNS_PER_USER", "2")
        first, second = (await rig.start()), (await rig.start())
        assert (first.status, second.status) == ("waiting", "waiting")
        ran: list[str] = []

        async def start_step(node: Any, store: Any, ctx: Any) -> Any:
            from fim_one.core.workflow.types import NodeResult, NodeStatus

            ran.append(node.id)
            return NodeResult(node_id=node.id, status=NodeStatus.COMPLETED, output={})

        with patch("fim_one.core.workflow.nodes.StartExecutor.execute", side_effect=start_step):
            with pytest.raises(rs.RunRefusedError) as refused:
                await rig.start()

        assert (refused.value.error_code, refused.value.status_code) == (
            "workflow_waiting_limit_reached",
            429,
        )
        assert "2 of this user's runs are already waiting" in refused.value.detail
        assert ran == []  # no step before the approval ran for nothing
        async with rig.factory() as s:
            assert len((await s.execute(select(WorkflowRun))).scalars().all()) == 2
        # Once one is decided or cancelled there is room again.
        assert await rig.service.cancel_stored_run(first.run_id) is True
        assert (await rig.start()).status == "waiting"

    async def test_a_workflow_that_cannot_wait_is_not_refused(
        self, rig: Rig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WORKFLOW_MAX_WAITING_RUNS_PER_USER", "1")
        assert (await rig.start()).status == "waiting"
        await rig.workflow(
            {
                "schema_version": 2,
                "nodes": [_node("start_1", "start", "START"), _node("end_1", "end", "END")],
                "edges": [_edge("start_1", "end_1")],
                "viewport": {"x": 0, "y": 0, "zoom": 1},
            }
        )
        assert (await rig.start()).status == "completed"

    async def test_a_connector_step_that_asks_counts_as_a_step_that_waits(
        self, rig: Rig, flagged: _Adapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WORKFLOW_MAX_WAITING_RUNS_PER_USER", "1")
        assert (await rig.start()).status == "waiting"
        with pytest.raises(rs.RunRefusedError) as refused:
            await rig.start()
        assert refused.value.error_code == "workflow_waiting_limit_reached"

    async def test_runs_reaching_their_approvals_together_cannot_overshoot(
        self, rig: Rig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Six runs start while nothing waits, so all pass the check at the
        start; they reach the approval together. Two get a place."""
        monkeypatch.setenv("WORKFLOW_MAX_WAITING_RUNS_PER_USER", "2")
        workers = [_new_service() for _ in range(6)]
        # No run goes on to its steps before all six have passed the check
        # at the start. Left to timing, a run that reached that check a few
        # tens of milliseconds late found both places taken and was
        # refused there, which is the other test's case, not this one's.
        check = rs.WorkflowRunService.refuse_if_cannot_wait
        passed, all_passed = 0, asyncio.Event()

        async def check_then_wait_for_the_rest(self: Any, *args: Any, **kwargs: Any) -> None:
            nonlocal passed
            await check(self, *args, **kwargs)
            passed += 1
            if passed == len(workers):
                all_passed.set()
            await all_passed.wait()

        monkeypatch.setattr(
            rs.WorkflowRunService, "refuse_if_cannot_wait", check_then_wait_for_the_rest
        )

        outcomes = await asyncio.wait_for(
            asyncio.gather(*(rig.start(service=worker) for worker in workers)), timeout=30
        )

        assert sorted(o.status for o in outcomes) == ["failed"] * 4 + ["waiting"] * 2
        failed = [o for o in outcomes if o.status == "failed"]
        assert {o.error_code for o in failed} == {"workflow_waiting_limit_reached"}
        for outcome in failed:
            assert [r.status for r in await _requests(rig, outcome.run_id)] == ["expired"]
            assert (await rig.run(outcome.run_id)).waiting_on is None
        async with rig.factory() as s:
            waiting = (
                await s.execute(select(WorkflowRun).where(WorkflowRun.status == "waiting"))
            ).scalars().all()
        assert len(waiting) == 2

    async def test_a_run_that_starts_once_the_places_are_taken_is_refused_at_the_start(
        self, rig: Rig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The same six runs, but four of them reach the check at the start
        after the first two took their places: they are refused there
        (no run record, no step run), and still only two wait."""
        monkeypatch.setenv("WORKFLOW_MAX_WAITING_RUNS_PER_USER", "2")
        workers = [_new_service() for _ in range(6)]
        check = rs.WorkflowRunService.refuse_if_cannot_wait
        early, early_done = set(map(id, workers[:2])), asyncio.Event()

        async def late_for_four(self: Any, *args: Any, **kwargs: Any) -> None:
            if id(self) not in early:
                await early_done.wait()
            await check(self, *args, **kwargs)

        monkeypatch.setattr(rs.WorkflowRunService, "refuse_if_cannot_wait", late_for_four)

        async def start(worker: rs.WorkflowRunService) -> Any:
            try:
                return await rig.start(service=worker)
            finally:
                if id(worker) in early:
                    early.discard(id(worker))
                    if not early:
                        early_done.set()

        outcomes = await asyncio.wait_for(
            asyncio.gather(*(start(w) for w in workers), return_exceptions=True), timeout=30
        )

        assert [getattr(o, "status", o) for o in outcomes[:2]] == ["waiting", "waiting"]
        refusals = [o for o in outcomes[2:] if isinstance(o, rs.RunRefusedError)]
        assert len(refusals) == 4
        assert {o.error_code for o in refusals} == {"workflow_waiting_limit_reached"}
        async with rig.factory() as s:
            runs = (await s.execute(select(WorkflowRun))).scalars().all()
        assert [r.status for r in runs] == ["waiting", "waiting"]

    @pytest.mark.parametrize(
        "ending",
        [
            pytest.param(rs.SuspendStateTooLargeError(2, 1), id="state-too-large"),
            pytest.param(RuntimeError("could not seal"), id="failed"),
            pytest.param(asyncio.CancelledError(), id="cancelled"),
        ],
    )
    async def test_a_place_taken_is_given_back_when_the_run_does_not_wait_after_all(
        self, rig: Rig, monkeypatch: pytest.MonkeyPatch, ending: BaseException
    ) -> None:
        """The run took its place (``waiting_on`` written while running)
        and then ended instead of waiting. Its place is free again."""
        monkeypatch.setenv("WORKFLOW_MAX_WAITING_RUNS_PER_USER", "1")
        reserved: list[Any] = []

        def seal(state: dict[str, Any]) -> dict[str, Any]:
            raise ending

        async def reserve_and_look(self: Any, run_id: str, *args: Any, **kwargs: Any) -> bool:
            got: bool = await take(self, run_id, *args, **kwargs)
            reserved.append((got, (await rig.run(run_id)).waiting_on))
            return got

        take = rs.WorkflowRunService._reserve_wait
        monkeypatch.setattr(rs.WorkflowRunService, "_reserve_wait", reserve_and_look)
        with patch.object(rs, "seal_state", seal):
            outcome = await rig.start()

        assert outcome.status in ("failed", "cancelled")
        assert reserved and reserved[0][0] is True and reserved[0][1]  # it had the place
        run = await rig.run(outcome.run_id)
        assert (run.status, run.waiting_on) == (outcome.status, None)
        async with rig.factory() as s:
            assert await rig.service._waiting_count(s, rig.owner.id) == 0
        assert (await rig.start()).status == "waiting"  # the one place is there to take

    @pytest.mark.parametrize("closed_by", ["sweep", "cancel"])
    async def test_a_place_held_by_a_run_whose_process_died_is_given_back(
        self, rig: Rig, monkeypatch: pytest.MonkeyPatch, closed_by: str
    ) -> None:
        """A process died between taking the place and storing the wait:
        the record stays ``running`` with ``waiting_on`` set. It counts
        until the record is closed, by the sweep or by a cancel, not after."""
        monkeypatch.setenv("WORKFLOW_MAX_WAITING_RUNS_PER_USER", "1")
        async with rig.factory() as s:
            dead = WorkflowRun(
                workflow_id=rig.wf.id,
                user_id=rig.owner.id,
                blueprint_snapshot=BLUEPRINT,
                status="running",
                worker_id="a-process-that-died",
                waiting_on={"human_1": {"confirmation_id": "x"}},
            )
            s.add(dead)
            await s.commit()
        await _age_stamp(rig, dead.id, 3600)
        with pytest.raises(rs.RunRefusedError):
            await rig.start()

        if closed_by == "sweep":
            report = await WorkflowRunSupervisor(rig.service, stale_seconds=90).sweep()
            assert report.closed == [dead.id]
        else:
            assert await rig.service.cancel_stored_run(dead.id) is True

        assert (await rig.run(dead.id)).waiting_on is None
        assert (await rig.start()).status == "waiting"

    async def test_a_batch_starts_only_as_many_items_as_may_wait_and_says_so(
        self, rig: Rig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fim_one.web.api import workflows as wf_api
        from fim_one.web.schemas.workflow import WorkflowBatchRunRequest

        monkeypatch.setenv("WORKFLOW_MAX_WAITING_RUNS_PER_USER", "3")
        assert (await rig.start()).status == "waiting"

        async with rig.factory() as db:
            response = await wf_api.batch_run_workflow(
                rig.wf.id,
                WorkflowBatchRunRequest(inputs=[{"q": str(i)} for i in range(5)]),
                current_user=rig.owner,
                db=db,
            )

        results = response.data["results"]
        assert response.data["not_started"] == 3
        assert [r["status"] for r in results] == ["waiting", "waiting", "failed", "failed", "failed"]
        assert all(r["run_id"] == "" for r in results[2:])
        assert "3 of the 5 items were left out" in results[2]["error"]
        async with rig.factory() as s:
            assert len((await s.execute(select(WorkflowRun))).scalars().all()) == 3

    async def test_another_users_waiting_runs_do_not_count(
        self, rig: Rig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WORKFLOW_MAX_WAITING_RUNS_PER_USER", "1")
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
        assert (await rig.start()).status == "waiting"  # the owner's first
        monkeypatch.setenv("WORKFLOW_MAX_WAITING_RUNS_PER_USER", "0")  # no limit
        assert (await rig.start()).status == "waiting"


# ---------------------------------------------------------------------------
# M3. A live run is not taken for dead, and a run closed under its process
#     does nothing more
# ---------------------------------------------------------------------------

# A server process of its own. It runs start -> end, where the End step
# blocks the event loop for four seconds (time.sleep): no task on that loop
# can stamp the run meanwhile. With "thread" the supervisor stamps from its
# heartbeat thread, as a server does; with "loop" from the loop, as before.
_BLOCKED_SERVER = """
import asyncio, sys, time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.workflow import run_service as rs
from fim_one.core.workflow.run_recovery import WorkflowRunSupervisor, sync_engine_for
from fim_one.core.workflow.types import NodeResult, NodeStatus
from fim_one.db.models.workflow import Workflow
from fim_one.web.services.assembly_ports import register_assembly_ports

db_file, workflow_id, user_id, heartbeat, marker = sys.argv[1:6]
register_assembly_ports()
url = f"sqlite+aiosqlite:///{db_file}"


async def blocking_end(node, store, ctx):
    time.sleep(4)  # the whole event loop stands still
    open(marker, "w").write("the step ran to its end")
    return NodeResult(node_id=node.id, status=NodeStatus.COMPLETED, output={})


async def main():
    engine = create_async_engine(url, connect_args={"timeout": 30})
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def create_session():
        async with factory() as session:
            yield session

    with (
        patch("fim_one.db.create_session", create_session),
        patch("fim_one.web.services.quota_backend.usage_and_cap", new=AsyncMock(return_value=(0, 0))),
        patch("fim_one.web.services.quota_enforcer.is_user_unentitled", new=AsyncMock(return_value=False)),
        patch("fim_one.core.workflow.nodes.EndExecutor.execute", side_effect=blocking_end),
    ):
        service = rs.WorkflowRunService()
        supervisor = WorkflowRunSupervisor(
            service,
            heartbeat_seconds=0.2,
            sync_engine=sync_engine_for(url) if heartbeat == "thread" else None,
        )
        watching = asyncio.create_task(supervisor.run_loop())
        await asyncio.sleep(0.5)
        async with factory() as session:
            workflow = await session.get(Workflow, workflow_id)
        outcome = await service.run_to_completion(
            workflow, caller_id=user_id, inputs={}, mode=rs.RunMode.INTERACTIVE
        )
        print(outcome.run_id, outcome.status, flush=True)
        watching.cancel()


asyncio.run(main())
"""

_START_END = {
    "schema_version": 2,
    "nodes": [_node("start_1", "start", "START"), _node("end_1", "end", "END")],
    "edges": [_edge("start_1", "end_1")],
    "viewport": {"x": 0, "y": 0, "zoom": 1},
}


async def _blocked_server(rig: Rig, tmp_path: Any, heartbeat: str) -> tuple[Any, Any]:
    import os
    import sys

    await rig.workflow(_START_END)
    marker = tmp_path / "side-effect.txt"
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _BLOCKED_SERVER,
        str(tmp_path / "suspend.db"),
        rig.wf.id,
        rig.owner.id,
        heartbeat,
        str(marker),
        env=dict(os.environ),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    return child, marker


async def _the_run(rig: Rig, timeout: float = 30.0) -> WorkflowRun:
    end = time.monotonic() + timeout
    while True:
        async with rig.factory() as s:
            run = (await s.execute(select(WorkflowRun))).scalars().first()
        if run is not None:
            return run
        assert time.monotonic() < end, "the other process started no run"
        await asyncio.sleep(0.05)


class TestALiveRunIsNotTakenForDead:
    async def test_a_process_whose_event_loop_is_blocked_keeps_its_run_alive(
        self, rig: Rig, tmp_path: Any
    ) -> None:
        """Two real processes. One executes a step that blocks its event
        loop for 4 s; the other sweeps meanwhile with a 0.6 s staleness
        limit. The heartbeat thread keeps stamping, so nothing is closed."""
        if rig.backend != "sqlite":
            pytest.skip("the child process opens the SQLite file")
        child, marker = await _blocked_server(rig, tmp_path, "thread")
        run = await _the_run(rig)
        other = WorkflowRunSupervisor(_new_service(), heartbeat_seconds=0.2, stale_seconds=0.6)

        closed: list[str] = []
        for _ in range(4):  # 2 s into the 4 s the loop is blocked
            await asyncio.sleep(0.5)
            closed += (await other.sweep()).closed

        out, err = await asyncio.wait_for(child.communicate(), timeout=60)
        assert child.returncode == 0, err.decode()[-2000:]
        assert closed == []
        assert out.decode().split() == [run.id, "completed"]
        assert (await rig.run(run.id)).status == "completed"

    async def test_without_the_thread_the_same_run_is_closed_and_never_reported_as_done(
        self, rig: Rig, tmp_path: Any
    ) -> None:
        """The control: stamping from the blocked loop itself, the run goes
        stale and the other process closes it. The step that was in flight
        finishes, but the run is not recorded or reported as completed."""
        if rig.backend != "sqlite":
            pytest.skip("the child process opens the SQLite file")
        child, marker = await _blocked_server(rig, tmp_path, "loop")
        run = await _the_run(rig)
        other = WorkflowRunSupervisor(_new_service(), heartbeat_seconds=0.2, stale_seconds=0.6)

        # Swept until it is closed, well inside the 3 s the child's loop is
        # blocked (it goes stale 0.6 s after its last stamp): no fixed
        # schedule of sweeps that a slow machine could miss.
        closed: list[str] = []
        deadline = time.monotonic() + 2.5
        while not closed and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
            closed += (await other.sweep()).closed

        out, err = await asyncio.wait_for(child.communicate(), timeout=60)
        assert child.returncode == 0, err.decode()[-2000:]
        assert closed == [run.id]
        assert marker.exists()  # the step in flight could not be recalled
        assert out.decode().split() == [run.id, "failed"]
        stored = await rig.run(run.id)
        assert (stored.status, stored.error) == ("failed", rs.RUN_INTERRUPTED_ERROR)

    async def test_the_heartbeat_thread_stamps_and_notices_a_run_closed_elsewhere(
        self, rig: Rig, tmp_path: Any
    ) -> None:
        from fim_one.core.workflow.run_recovery import HeartbeatThread, sync_engine_for

        if rig.backend != "sqlite":
            pytest.skip("uses the SQLite file")
        release = asyncio.Event()

        class _Held(_CountingEngine):
            async def execute_streaming(self, *args: Any, **kwargs: Any) -> Any:
                await release.wait()
                yield "run_completed", {"status": "completed", "outputs": {}}

        service = rs.WorkflowRunService(engine_factory=_Held)
        started = await service.start_run(
            await rig.workflow(), caller_id=rig.owner.id, inputs={}, mode=rs.RunMode.INTERACTIVE
        )
        async with rig.factory() as s:
            await s.execute(
                update(WorkflowRun)
                .where(WorkflowRun.id == started.run_id)
                .values(heartbeat_at=datetime.now(UTC) - timedelta(hours=1))
            )
            await s.commit()
        thread = HeartbeatThread(
            sync_engine_for(f"sqlite+aiosqlite:///{tmp_path / 'suspend.db'}"),
            service,
            asyncio.get_running_loop(),
            0.05,
        )

        await asyncio.to_thread(thread.beat)
        stamped = (await rig.run(started.run_id)).heartbeat_at
        assert stamped is not None
        assert stamped.replace(tzinfo=UTC) > datetime.now(UTC) - timedelta(minutes=1)
        assert await service.owns(started.run_id) is True

        assert await _new_service().cancel_stored_run(started.run_id) is True
        await asyncio.to_thread(thread.beat)
        await asyncio.sleep(0)  # the cancel handed to the loop runs
        assert started.cancel_event.is_set()
        assert await service.owns(started.run_id) is False
        release.set()
        assert (await started.wait()).status == "cancelled"

    async def test_staleness_is_judged_by_the_databases_clock(self, rig: Rig) -> None:
        """This host's clock is a day ahead of the database's: a run stamped
        a moment ago is not stale for that, and one not stamped for 200 s is."""
        import datetime as real

        async def run_stamped(seconds_ago: float) -> str:
            run_id = str(uuid.uuid4())
            async with rig.factory() as s:
                s.add(
                    WorkflowRun(
                        id=run_id,
                        workflow_id=rig.wf.id,
                        user_id=rig.owner.id,
                        blueprint_snapshot=BLUEPRINT,
                        status="running",
                        worker_id="another-process",
                        heartbeat_at=rs.db_seconds_ago(rig.backend, seconds_ago),
                    )
                )
                await s.commit()
            return run_id

        fresh, stale = await run_stamped(1), await run_stamped(200)

        class _AheadByADay(real.datetime):
            @classmethod
            def now(cls, tz: Any = None) -> Any:
                return real.datetime.now(tz) + real.timedelta(days=1)

        with patch.object(rs, "datetime", _AheadByADay):
            report = await WorkflowRunSupervisor(rig.service, stale_seconds=90).sweep()

        assert report.closed == [stale]
        assert (await rig.run(fresh)).status == "running"


class TestARunClosedUnderItsProcessDoesNothingMore:
    async def test_the_next_step_is_not_run_and_nothing_reports_success(self, rig: Rig) -> None:
        """Process A is alive but its run was declared dead (it could not
        stamp for longer than the limit). The step in flight cannot be
        recalled; the step after it is not started, the record stays as the
        other process left it, A reports that and calls no webhook, and the
        run's files are still there for the moment."""
        from fim_one.core.workflow.types import NodeResult, NodeStatus

        a, b = _new_service(), _new_service()
        release = asyncio.Event()
        ran: list[str] = []

        async def in_flight(node: Any, store: Any, ctx: Any) -> Any:
            await release.wait()
            ran.append("first step")
            return NodeResult(node_id=node.id, status=NodeStatus.COMPLETED, output="ok")

        async def side_effect(node: Any, store: Any, ctx: Any) -> Any:
            ran.append("second step")
            return NodeResult(node_id=node.id, status=NodeStatus.COMPLETED, output={"done": True})

        await rig.workflow(
            {
                "schema_version": 2,
                "nodes": [
                    _node("start_1", "start", "START"),
                    _node("first", "connector", "CONNECTOR"),
                    _node("end_1", "end", "END"),
                ],
                "edges": [_edge("start_1", "first"), _edge("first", "end_1")],
                "viewport": {"x": 0, "y": 0, "zoom": 1},
            }
        )
        with (
            patch("fim_one.core.workflow.nodes.ConnectorExecutor.execute", side_effect=in_flight),
            patch("fim_one.core.workflow.nodes.EndExecutor.execute", side_effect=side_effect),
            patch.object(a, "_fire_webhook") as webhook,
        ):
            started = await a.start_run(
                await rig.workflow(), caller_id=rig.owner.id, inputs={}, mode=rs.RunMode.INTERACTIVE
            )
            await asyncio.sleep(0.2)
            sandbox = rig.sandboxes / f"wfrun-{started.run_id}"
            sandbox.mkdir(exist_ok=True)
            (sandbox / "work.txt").write_text("x")
            async with rig.factory() as s:
                await s.execute(
                    update(WorkflowRun)
                    .where(WorkflowRun.id == started.run_id)
                    .values(heartbeat_at=rs.db_seconds_ago(rig.backend, 200))
                )
                await s.commit()

            report = await WorkflowRunSupervisor(b, stale_seconds=90).sweep()
            assert report.closed == [started.run_id]
            assert sandbox.exists()  # not pulled from under a process that may be alive

            release.set()
            outcome = await started.wait()

        assert ran == ["first step"]  # the second step was never started
        run = await rig.run(started.run_id)
        assert (run.status, run.error) == ("failed", rs.RUN_INTERRUPTED_ERROR)
        assert (outcome.status, outcome.error, outcome.outputs) == (
            "failed",
            rs.RUN_INTERRUPTED_ERROR,
            {},
        )
        webhook.assert_not_called()

    async def test_a_run_cancelled_from_another_process_stops_before_its_next_step(
        self, rig: Rig
    ) -> None:
        """L3: no waiting for the next heartbeat for a step not yet started."""
        from fim_one.core.workflow.types import NodeResult, NodeStatus

        here, elsewhere = _new_service(), _new_service()
        release = asyncio.Event()
        ran: list[str] = []

        async def first(node: Any, store: Any, ctx: Any) -> Any:
            await release.wait()
            return NodeResult(node_id=node.id, status=NodeStatus.COMPLETED, output="ok")

        async def second(node: Any, store: Any, ctx: Any) -> Any:
            ran.append("second step")
            return NodeResult(node_id=node.id, status=NodeStatus.COMPLETED, output={})

        await rig.workflow(
            {
                "schema_version": 2,
                "nodes": [
                    _node("start_1", "start", "START"),
                    _node("first", "connector", "CONNECTOR"),
                    _node("end_1", "end", "END"),
                ],
                "edges": [_edge("start_1", "first"), _edge("first", "end_1")],
                "viewport": {"x": 0, "y": 0, "zoom": 1},
            }
        )
        with (
            patch("fim_one.core.workflow.nodes.ConnectorExecutor.execute", side_effect=first),
            patch("fim_one.core.workflow.nodes.EndExecutor.execute", side_effect=second),
        ):
            started = await here.start_run(
                await rig.workflow(), caller_id=rig.owner.id, inputs={}, mode=rs.RunMode.INTERACTIVE
            )
            await asyncio.sleep(0.2)
            assert await elsewhere.cancel_stored_run(started.run_id) is True
            release.set()  # no heartbeat has run in between
            outcome = await started.wait()

        assert ran == []
        assert outcome.status == "cancelled"
        assert (await rig.run(started.run_id)).status == "cancelled"


# ---------------------------------------------------------------------------
# Round 2, A. An answer counts even if nobody would be asked today
# ---------------------------------------------------------------------------


class TestTheFlagRemovedWhileARequestWaits:
    @staticmethod
    async def _unflag(rig: Rig) -> None:
        from fim_one.db.models.connector import ConnectorAction

        async with rig.factory() as db:
            await db.execute(
                update(ConnectorAction)
                .where(ConnectorAction.id == "wipe-1")
                .values(requires_confirmation=False)
            )
            await db.commit()

    async def test_a_rejection_still_stops_the_action(
        self, rig: Rig, flagged: _Adapter, hooked: Any
    ) -> None:
        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)
        await self._unflag(rig)

        await rig.decide(request.id, "reject", note="no")

        run = await rig.ended(outcome.run_id)
        assert run.status == "failed" and flagged.calls == []
        assert "the confirmation was rejected (no)" in run.node_results["connector_1"]["error"]

    async def test_an_expiry_still_stops_the_action(self, rig: Rig, flagged: _Adapter) -> None:
        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)
        await self._unflag(rig)
        await rig.flip(request.id, expires_at=datetime.now(UTC) - timedelta(seconds=1))

        await WorkflowRunSupervisor(rig.service).sweep()

        run = await rig.ended(outcome.run_id)
        assert run.status == "failed" and flagged.calls == []

    async def test_an_approval_of_the_flagged_action_does_not_cover_the_unflagged_one(
        self, rig: Rig, flagged: _Adapter, hooked: Any
    ) -> None:
        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)
        await self._unflag(rig)

        await rig.decide(request.id, "approve")

        run = await rig.ended(outcome.run_id)
        assert run.status == "failed" and flagged.calls == []
        assert "changed after the confirmation was requested" in (
            run.node_results["connector_1"]["error"]
        )


# ---------------------------------------------------------------------------
# Round 2, B. What a run spent is recorded, however it ended
# ---------------------------------------------------------------------------


def _spending(tokens: int, release: asyncio.Event | None = None) -> Any:
    from fim_one.core.workflow.types import NodeResult, NodeStatus

    async def execute(node: Any, store: Any, ctx: Any) -> Any:
        await ctx.usage_tracker.record({"total_tokens": tokens})
        if release is not None:
            await release.wait()
        return NodeResult(node_id=node.id, status=NodeStatus.COMPLETED, output="ok")

    return execute


class TestTokensAreRecorded:
    async def test_of_a_run_cancelled_from_another_process(self, rig: Rig) -> None:
        here, elsewhere = _new_service(), _new_service()
        release = asyncio.Event()
        with patch(
            "fim_one.core.workflow.nodes.StartExecutor.execute",
            side_effect=_spending(50_000, release),
        ):
            started = await here.start_run(
                await rig.workflow(), caller_id=rig.owner.id, inputs={}, mode=rs.RunMode.INTERACTIVE
            )
            await asyncio.sleep(0.2)
            assert await elsewhere.cancel_stored_run(started.run_id) is True
            release.set()
            await started.wait()

        run = await rig.run(started.run_id)
        assert (run.status, run.total_tokens) == ("cancelled", 50_000)
        assert run.outputs is None and run.duration_ms

    async def test_of_the_part_before_a_wait_and_of_every_continuation_once(
        self, rig: Rig
    ) -> None:
        """start(100) -> approval -> connector step(20) that also waits -> end:
        the run is stored twice as waiting and continued twice."""
        from fim_one.core.workflow.types import NodeResult, NodeStatus

        async def second_gate(node: Any, store: Any, ctx: Any) -> Any:
            await ctx.usage_tracker.record({"total_tokens": 20})
            if node.id not in ctx.suspensions:
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.WAITING,
                    suspension={"confirmation_id": "gone", "kind": "confirmation"},
                )
            return NodeResult(node_id=node.id, status=NodeStatus.COMPLETED, output="ok")

        await rig.workflow(
            {
                "schema_version": 2,
                "nodes": [
                    _node("start_1", "start", "START"),
                    APPROVAL,
                    _node("second", "connector", "CONNECTOR"),
                    _node("end_1", "end", "END"),
                ],
                "edges": [
                    _edge("start_1", "human_1"),
                    _edge("human_1", "second"),
                    _edge("second", "end_1"),
                ],
                "viewport": {"x": 0, "y": 0, "zoom": 1},
            }
        )
        with (
            patch("fim_one.core.workflow.nodes.StartExecutor.execute", side_effect=_spending(100)),
            patch("fim_one.core.workflow.nodes.ConnectorExecutor.execute", side_effect=second_gate),
        ):
            outcome = await rig.start()
            assert (await rig.run(outcome.run_id)).total_tokens == 100  # at the first wait

            await rig.flip((await rig.request_of(outcome.run_id)).id, status="approved")
            first = await rig.service.resume_run(outcome.run_id)
            assert first is not None and (await first.wait()).status == "waiting"
            # The second step's request does not exist, so the run is
            # continued again right away, by itself.
            run = await rig.ended(outcome.run_id)

        assert run.status == "completed"
        assert _CountingEngine.continued == [run.id, run.id]
        assert run.total_tokens == 140  # 100 + 20 + 20, each part once

    async def test_quota_used_up_while_the_run_waited_stops_the_continuation(
        self, rig: Rig, hooked: Any
    ) -> None:
        """Readmission through the real gate, by its effect."""
        from unittest.mock import AsyncMock

        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)

        with patch(
            "fim_one.web.services.quota_backend.usage_and_cap",
            new=AsyncMock(return_value=(1_000, 1_000)),
        ):
            await rig.decide(request.id, "approve")
            run = await rig.ended(outcome.run_id)

        assert run.status == "failed"
        assert "(token_quota_exceeded)" in (run.error or "")
        assert _CountingEngine.continued == []
        assert run.node_results.get("end_1", {}).get("status") != "completed"


# ---------------------------------------------------------------------------
# Round 2, C. The heartbeat thread cannot fail silently, hang, or write late
# ---------------------------------------------------------------------------


class _HeldEngine(_CountingEngine):
    release: asyncio.Event

    async def execute_streaming(self, *args: Any, **kwargs: Any) -> Any:
        await _HeldEngine.release.wait()
        yield "run_completed", {"status": "completed", "outputs": {}}


async def _held_run(rig: Rig) -> tuple[rs.WorkflowRunService, Any]:
    _HeldEngine.release = asyncio.Event()
    service = rs.WorkflowRunService(engine_factory=_HeldEngine)
    started = await service.start_run(
        await rig.workflow(), caller_id=rig.owner.id, inputs={}, mode=rs.RunMode.INTERACTIVE
    )
    return service, started


async def _age_stamp(rig: Rig, run_id: str, seconds: float) -> None:
    async with rig.factory() as s:
        await s.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == run_id)
            .values(heartbeat_at=rs.db_seconds_ago(rig.backend, seconds))
        )
        await s.commit()


async def _stamp_age(rig: Rig, run_id: str) -> float:
    """How old the run's heartbeat is, by the database's clock (seconds)."""
    from sqlalchemy import func

    async with rig.factory() as s:
        fresh = (
            await s.execute(
                select(func.count()).where(
                    WorkflowRun.id == run_id,
                    WorkflowRun.heartbeat_at > rs.db_seconds_ago(rig.backend, 5),
                )
            )
        ).scalar_one()
    return 0.0 if fresh else 999.0


class TestTheHeartbeatThread:
    def test_its_connection_has_short_connect_lock_and_statement_timeouts(
        self, tmp_path: Any
    ) -> None:
        from fim_one.core.workflow.run_recovery import sync_connect_args, sync_engine_for

        pg = sync_connect_args("postgresql")
        assert pg["connect_timeout"] == 5
        assert "lock_timeout=2000" in pg["options"] and "statement_timeout=5000" in pg["options"]
        assert sync_connect_args("sqlite") == {"timeout": 5}
        engine = sync_engine_for("postgresql+asyncpg://u:p@127.0.0.1:1/db")
        assert engine.dialect.driver == "psycopg2"
        assert sync_engine_for(f"sqlite+aiosqlite:///{tmp_path / 'x.db'}").dialect.name == "sqlite"
        assert sync_engine_for("sqlite+aiosqlite:///:memory:") is None

    async def test_a_thread_that_cannot_reach_the_database_hands_stamping_to_the_loop(
        self, rig: Rig, tmp_path: Any
    ) -> None:
        """Alive but failing every beat: before, the loop saw "alive" and
        never stamped, and a peer closed the healthy run."""
        from sqlalchemy import create_engine

        service, started = await _held_run(rig)
        await _age_stamp(rig, started.run_id, 3600)
        broken = create_engine(f"sqlite:///{tmp_path}/no/such/dir/x.db")
        supervisor = WorkflowRunSupervisor(
            service, heartbeat_seconds=0.05, sweep_seconds=3600, sync_engine=broken
        )

        watching = asyncio.create_task(supervisor.run_loop())
        try:
            await asyncio.sleep(0.6)
            thread = supervisor._heartbeat
            assert thread is not None and thread.is_alive()
            assert thread.failures > 0 and thread.stamping() is False
            assert await _stamp_age(rig, started.run_id) < 5  # the loop stamped it
            peer = WorkflowRunSupervisor(_new_service(), heartbeat_seconds=1, stale_seconds=90)
            assert (await peer.sweep()).closed == []
        finally:
            watching.cancel()
            _HeldEngine.release.set()
            await started.wait()

    async def test_a_thread_that_works_again_takes_stamping_back(
        self, rig: Rig, tmp_path: Any
    ) -> None:
        from fim_one.core.workflow.run_recovery import HeartbeatThread, sync_engine_for

        if rig.backend != "sqlite":
            pytest.skip("uses the SQLite file")
        service, started = await _held_run(rig)
        engine = sync_engine_for(f"sqlite+aiosqlite:///{tmp_path / 'suspend.db'}")
        thread = HeartbeatThread(engine, service, asyncio.get_running_loop(), 0.05)
        beat, failing = thread.beat, {"on": True}

        def flaky() -> None:
            if failing["on"]:
                raise OSError("connection lost")
            beat()

        thread.beat = flaky  # type: ignore[method-assign]
        thread.start()
        try:
            await asyncio.sleep(0.3)
            assert thread.stamping() is False and thread.failures > 0
            failing["on"] = False
            for _ in range(100):  # the pause had grown; it comes back
                await asyncio.sleep(0.1)
                if thread.stamping():
                    break
            assert thread.stamping() is True and thread.failures == 0
        finally:
            thread.stop()
            _HeldEngine.release.set()
            await started.wait()

    async def test_a_locked_row_does_not_delay_the_others_or_get_an_old_time(
        self, rig: Rig
    ) -> None:
        """PostgreSQL: another transaction holds one run's row (a progress
        save not yet committed). The other run is stamped at once."""
        import os

        from sqlalchemy import create_engine, text
        from sqlalchemy.engine import make_url

        from fim_one.core.workflow.run_recovery import HeartbeatThread, sync_connect_args

        if rig.backend != "postgresql":
            pytest.skip("row locks are PostgreSQL's")
        async with rig.factory() as s:
            schema = (await s.execute(text("select current_schema()"))).scalar_one()
        url = make_url(os.environ["FIM_TEST_PG_URL"]).set(drivername="postgresql+psycopg2")
        args = sync_connect_args("postgresql")
        args["options"] += f" -csearch_path={schema}"
        engine = create_engine(url, connect_args=args, isolation_level="AUTOCOMMIT")
        holder = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})

        service, locked = await _held_run(rig)
        free = await service.start_run(
            await rig.workflow(), caller_id=rig.owner.id, inputs={}, mode=rs.RunMode.INTERACTIVE
        )
        for run in (locked, free):
            await _age_stamp(rig, run.run_id, 3600)
        thread = HeartbeatThread(engine, service, asyncio.get_running_loop(), 0.05)
        try:
            with holder.connect() as conn:
                conn.execute(
                    text("select id from workflow_runs where id = :id for update"),
                    {"id": locked.run_id},
                )
                began = time.monotonic()
                await asyncio.to_thread(thread.beat)
                took = time.monotonic() - began
                assert took < 1.5, f"the beat waited {took:.1f}s behind the lock"
                assert await _stamp_age(rig, free.run_id) < 5
                assert await _stamp_age(rig, locked.run_id) > 5  # skipped, not stamped late
                conn.rollback()
            await asyncio.to_thread(thread.beat)
            assert await _stamp_age(rig, locked.run_id) < 5
        finally:
            engine.dispose()
            holder.dispose()
            _HeldEngine.release.set()
            await locked.wait()
            await free.wait()


class TestATriggerCallerLearnsWhyARunCouldNotWait:
    async def test_the_waiting_limit_has_its_own_code(
        self, rig: Rig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fim_one.web.api import workflows as wf_api
        from fim_one.web.schemas.workflow import WorkflowTriggerRequest

        # A step before the approval, so the limit is met at the step.
        monkeypatch.setenv("WORKFLOW_MAX_WAITING_RUNS_PER_USER", "1")
        assert (await rig.start()).status == "waiting"
        from unittest.mock import AsyncMock

        # Past the check at the start, as when two runs start together.
        with (
            patch.object(rig.service, "_refuse_if_running", new=AsyncMock()),
            patch.object(rig.service, "refuse_if_cannot_wait", new=AsyncMock()),
        ):
            response = await wf_api._execute_trigger(
                await rig.workflow(), WorkflowTriggerRequest(inputs={})
            )
        assert response.data["status"] == "failed"
        assert response.data["error_code"] == "workflow_waiting_limit_reached"
        assert "Too many runs are already waiting" in response.data["error"]


class TestARunClosedOnItsRecordIsAnnounced:
    """Nobody's driver ends these runs, so the owner's run-failed webhook
    is sent by whoever closed the record: once."""

    async def test_a_run_whose_process_died(self, rig: Rig) -> None:
        async with rig.factory() as s:
            dead = WorkflowRun(
                workflow_id=rig.wf.id,
                user_id=rig.owner.id,
                blueprint_snapshot=BLUEPRINT,
                status="running",
                mode="trigger",
                worker_id="a-process-that-died",
                heartbeat_at=rs.db_seconds_ago(rig.backend, 600),
            )
            s.add(dead)
            await s.commit()
            run_id = dead.id
        sent: list[tuple[str, str | None]] = []
        supervisors = [WorkflowRunSupervisor(_new_service()) for _ in range(4)]
        for supervisor in supervisors:
            supervisor.service._fire_webhook = (  # type: ignore[method-assign]
                lambda wf, out, mode, ctx: sent.append((out.run_id, out.error))
            )

        await asyncio.gather(*(supervisor.sweep() for supervisor in supervisors))

        assert sent == [(run_id, rs.RUN_INTERRUPTED_ERROR)]

    async def test_a_run_refused_at_its_continuation(self, rig: Rig, hooked: Any) -> None:
        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)
        async with rig.factory() as s:
            await s.execute(update(User).where(User.id == rig.owner.id).values(is_active=False))
            await s.commit()

        with patch.object(rig.service, "_fire_webhook") as webhook:
            await rig.decide(request.id, "approve", by=rig.admin)
            await rig.ended(outcome.run_id)

        (call,) = webhook.call_args_list
        assert call.args[1].status == "failed"
        assert "could not continue after its approval" in call.args[1].error

    async def test_a_cancelled_run_is_not_announced_as_failed(self, rig: Rig) -> None:
        outcome = await rig.start()
        with patch.object(rig.service, "_fire_webhook") as webhook:
            assert await rig.service.cancel_stored_run(outcome.run_id) is True
        webhook.assert_not_called()


class TestAQueuedContinuation:
    @staticmethod
    def _capped(cap: int) -> rs.WorkflowRunService:
        return rs.WorkflowRunService(
            engine_factory=_CountingEngine,
            rate_limiter=WorkflowRateLimiter(max_runs_per_minute=1000, max_concurrent_runs=cap),
        )

    async def _queued(self, rig: Rig, service: rs.WorkflowRunService) -> Any:
        outcome = await rig.start(service=service)
        await rig.flip((await rig.request_of(outcome.run_id)).id, status="approved")
        await service.rate_limiter.acquire(rig.owner.id, "another-run")  # the only slot
        started = await service.resume_run(outcome.run_id)
        assert started is not None
        await asyncio.sleep(0.1)
        return started

    async def test_it_is_shown_as_queued_and_goes_before_the_users_new_runs(
        self, rig: Rig
    ) -> None:
        from fim_one.web.api import workflows as wf_api

        service = self._capped(1)
        started = await self._queued(rig, service)

        async with rig.factory() as db:
            shown = (
                await wf_api.get_workflow_run(
                    rig.wf.id, started.run_id, current_user=rig.owner, db=db
                )
            ).data
        assert (shown["status"], shown["queued"]) == ("running", True)

        # The slot comes free: a new run asking at that moment does not get it.
        await service.rate_limiter.record_run_end(rig.owner.id, "another-run")
        allowed, why = await service.rate_limiter.acquire(rig.owner.id, "a-new-run")
        assert allowed is False and "next in line" in (why or "")

        assert (await asyncio.wait_for(started.wait(), timeout=5)).status == "completed"
        async with rig.factory() as db:
            shown = (
                await wf_api.get_workflow_run(
                    rig.wf.id, started.run_id, current_user=rig.owner, db=db
                )
            ).data
        assert (shown["status"], shown["queued"]) == ("completed", False)
        # And nothing is held back once it has run.
        assert (await service.rate_limiter.acquire(rig.owner.id, "a-new-run"))[0] is True

    async def test_it_does_not_queue_for_ever(
        self, rig: Rig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WORKFLOW_RESUME_SLOT_WAIT_SECONDS", "0.3")
        service = self._capped(1)
        started = await self._queued(rig, service)

        outcome = await asyncio.wait_for(started.wait(), timeout=5)

        assert outcome.status == "failed"
        assert outcome.error_code == "workflow_resume_slot_timeout"
        run = await rig.run(started.run_id)
        assert run.status == "failed" and "none came free" in (run.error or "")
        assert run.queued_at is None and _CountingEngine.continued == []
        # The user's new runs are no longer held back for it.
        await service.rate_limiter.record_run_end(rig.owner.id, "another-run")
        assert (await service.rate_limiter.acquire(rig.owner.id, "a-new-run"))[0] is True

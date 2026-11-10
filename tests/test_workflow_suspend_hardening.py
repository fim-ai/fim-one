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

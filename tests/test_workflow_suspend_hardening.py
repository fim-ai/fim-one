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

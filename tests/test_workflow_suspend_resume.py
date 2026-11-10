"""A workflow run that waits for a decision is suspended, and continued later (T10 P5).

Driven for real: the real engine, the real confirmation service, a database
per test (a SQLite file, and PostgreSQL too with ``FIM_TEST_PG_URL`` set).
Several :class:`WorkflowRunService` objects stand side by side where the
point is what separate server processes do to one run: they share nothing
but the database, which is all separate processes share.

Covered here: a decision continues the run; a restart in between does not
lose it; however many processes or surfaces act at once, the run is
continued once; a run that is cancelled or fails closes its requests; a
run left behind by a dead process is closed and cleaned up; a run is
visible while it runs and is not tied to the connection that started it.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fim_one.core.assembly.confirmation import ConfirmationService
from fim_one.core.assembly.errors import AssemblyRefusal
from fim_one.core.assembly.principal import AuthMethod, resolve_principal
from fim_one.core.workflow import run_service as rs
from fim_one.core.workflow.engine import WorkflowEngine
from fim_one.core.workflow.rate_limiter import WorkflowRateLimiter
from fim_one.core.workflow.run_recovery import WorkflowRunSupervisor, install_resume_on_decision
from fim_one.db.base import Base
from fim_one.db.models.channel import ConfirmationRequest
from fim_one.db.models.user import User
from fim_one.db.models.workflow import Workflow, WorkflowRun
from fim_one.web.api import workflows as wf_api

ROUNDS = 30
WORKERS = 6

pytestmark = pytest.mark.usefixtures("human_intervention_enabled")


def _node(node_id: str, kind: str, type_name: str, **data: Any) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": kind,
        "position": {"x": 0, "y": 0},
        "data": {"type": type_name, **data},
    }


def _edge(source: str, target: str) -> dict[str, Any]:
    return {"id": f"{source}->{target}", "source": source, "target": target}


APPROVAL = _node(
    "human_1",
    "humanIntervention",
    "HUMAN_INTERVENTION",
    label="Manager sign-off",
    title="Sign off",
    description="Check the figures first.",
    timeout_hours=1,
)
# start -> approval -> end; the end reads something set before the wait and
# something set by the decision.
BLUEPRINT: dict[str, Any] = {
    "schema_version": 2,
    "nodes": [
        _node("start_1", "start", "START"),
        APPROVAL,
        _node(
            "end_1",
            "end",
            "END",
            output_mapping={
                "asked": "{{start_1.q}}",
                "decision": "{{human_1.output}}",
            },
        ),
    ],
    "edges": [_edge("start_1", "human_1"), _edge("human_1", "end_1")],
    "viewport": {"x": 0, "y": 0, "zoom": 1},
}
# The approval beside a step that fails (a connector step with nothing chosen).
FAILING_BESIDE_APPROVAL: dict[str, Any] = {
    "schema_version": 2,
    "nodes": [
        _node("start_1", "start", "START"),
        APPROVAL,
        _node("broken_1", "connector", "CONNECTOR"),
        _node("end_1", "end", "END"),
    ],
    "edges": [
        _edge("start_1", "human_1"),
        _edge("start_1", "broken_1"),
        _edge("human_1", "end_1"),
        _edge("broken_1", "end_1"),
    ],
    "viewport": {"x": 0, "y": 0, "zoom": 1},
}


def _decision(run: WorkflowRun) -> dict[str, Any]:
    """The approval step's output as the End step handed it out."""
    value = run.outputs["decision"]
    return dict(value) if isinstance(value, dict) else dict(json.loads(value))


# A server process of its own: starts a run of the workflow in the SQLite
# file it is given, prints the run id and its worker id once the run waits,
# and dies without any shutdown.
_CHILD_SERVER = """
import asyncio, os, sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.workflow import run_service as rs
from fim_one.db.models.workflow import Workflow
from fim_one.web.services.assembly_ports import register_assembly_ports

db_file, workflow_id, user_id = sys.argv[1:4]
register_assembly_ports()  # what the web layer does at start-up


async def main():
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", connect_args={"timeout": 30})
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def create_session():
        async with factory() as session:
            yield session

    with (
        patch("fim_one.db.create_session", create_session),
        patch("fim_one.web.services.quota_backend.usage_and_cap", new=AsyncMock(return_value=(0, 0))),
        patch(
            "fim_one.web.services.quota_enforcer.is_user_unentitled",
            new=AsyncMock(return_value=False),
        ),
    ):
        async with factory() as session:
            workflow = await session.get(Workflow, workflow_id)
        outcome = await rs.WorkflowRunService().run_to_completion(
            workflow,
            caller_id=user_id,
            inputs={"q": "from the other process"},
            mode=rs.RunMode.INTERACTIVE,
        )
        assert outcome.status == "waiting", outcome
        print(outcome.run_id, rs.WORKER_ID, flush=True)
    os.kill(os.getpid(), 9)


asyncio.run(main())
"""


class _CountingEngine(WorkflowEngine):
    """The real engine; counts how many times a suspended run was continued."""

    continued: ClassVar[list[str]] = []

    async def execute_streaming(
        self,
        blueprint: Any,
        inputs: dict[str, Any] | None = None,
        context: Any = None,
        resume_state: dict[str, Any] | None = None,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        if resume_state is not None:
            _CountingEngine.continued.append(self._run_id)
        async for event in super().execute_streaming(
            blueprint, inputs, context=context, resume_state=resume_state
        ):
            yield event


def _new_service() -> rs.WorkflowRunService:
    """A run service as one server process has it; limits out of the way."""
    return rs.WorkflowRunService(
        engine_factory=_CountingEngine,
        rate_limiter=WorkflowRateLimiter(max_runs_per_minute=100_000, max_concurrent_runs=1000),
    )


def _backends() -> list[Any]:
    return [
        pytest.param("sqlite", id="sqlite"),
        pytest.param(
            "postgresql",
            id="postgresql",
            marks=pytest.mark.skipif(
                not os.environ.get("FIM_TEST_PG_URL"), reason="FIM_TEST_PG_URL not set"
            ),
        ),
    ]


@pytest.fixture(params=_backends())
async def database(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[tuple[str, AsyncEngine]]:
    import fim_one.db.models  # noqa: F401

    backend: str = request.param
    if backend == "sqlite":
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'suspend.db'}", connect_args={"timeout": 30}
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield backend, engine
        await engine.dispose()
        return

    url = make_url(os.environ["FIM_TEST_PG_URL"]).set(drivername="postgresql+asyncpg")
    schema = f"suspend_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        url,
        pool_size=WORKERS * 4,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield backend, engine
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@dataclass
class Rig:
    backend: str
    factory: async_sessionmaker[AsyncSession]
    service: rs.WorkflowRunService
    owner: User
    admin: User
    wf: Workflow
    sandboxes: Path

    def confirmations(self) -> ConfirmationService:
        return ConfirmationService(self.factory)

    async def workflow(self, blueprint: dict[str, Any] | None = None) -> Workflow:
        async with self.factory() as s:
            wf = await s.get(Workflow, self.wf.id)
            assert wf is not None
            if blueprint is not None:
                wf.blueprint = blueprint
                await s.commit()
            return wf

    async def start(
        self, *, service: rs.WorkflowRunService | None = None, **kwargs: Any
    ) -> rs.RunOutcome:
        """Start a run of the workflow and let it go as far as it can."""
        kwargs.setdefault("caller_id", self.owner.id)
        kwargs.setdefault("inputs", {"q": "ship it?"})
        kwargs.setdefault("mode", rs.RunMode.INTERACTIVE)
        return await (service or self.service).run_to_completion(await self.workflow(), **kwargs)

    async def run(self, run_id: str) -> WorkflowRun:
        async with self.factory() as s:
            run = await s.get(WorkflowRun, run_id)
            assert run is not None
            return run

    async def request_of(self, run_id: str) -> ConfirmationRequest:
        async with self.factory() as s:
            return (
                await s.execute(
                    select(ConfirmationRequest).where(ConfirmationRequest.workflow_run_id == run_id)
                )
            ).scalar_one()

    async def decide(
        self, request_id: str, decision: str, *, by: User | None = None, note: str | None = None
    ) -> ConfirmationRequest:
        """Decide through the service, as the portal and a channel callback do."""
        async with self.factory() as db:
            actor = await resolve_principal(
                db, user_id=(by or self.owner).id, auth=AuthMethod.SESSION
            )
            decided: ConfirmationRequest = await self.confirmations().decide(
                db,
                confirmation_id=request_id,
                actor=actor,
                decision=decision,  # type: ignore[arg-type]
                note=note,
            )
            return decided

    async def flip(self, request_id: str, **values: Any) -> None:
        """Change the request row directly: no listener is told."""
        async with self.factory() as db:
            row = await db.get(ConfirmationRequest, request_id)
            assert row is not None
            for key, value in values.items():
                setattr(row, key, value)
            await db.commit()

    async def ended(self, run_id: str, timeout: float = 10.0) -> WorkflowRun:
        """The run once it is neither running nor waiting."""
        deadline = time.monotonic() + timeout
        while True:
            run = await self.run(run_id)
            if run.status not in ("running", "waiting", "pending"):
                return run
            assert time.monotonic() < deadline, f"run {run_id} is still {run.status}"
            await asyncio.sleep(0.02)


@pytest.fixture()
async def rig(database: tuple[str, AsyncEngine], tmp_path: Path) -> AsyncIterator[Rig]:
    backend, engine = database
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _cs() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    async with factory() as s:
        owner = User(id=str(uuid.uuid4()), email="owner@x.io")
        admin = User(id=str(uuid.uuid4()), email="admin@x.io", is_admin=True)
        s.add_all([owner, admin])
        await s.flush()
        wf = Workflow(
            user_id=owner.id, name="sign-off", blueprint=BLUEPRINT, status="active", is_active=True
        )
        s.add(wf)
        await s.commit()

    sandboxes = tmp_path / "sandbox"
    sandboxes.mkdir()
    _CountingEngine.continued = []
    service = _new_service()
    with (
        patch("fim_one.db.create_session", _cs),
        patch.object(rs, "_default_service", service),
        patch("fim_one.core.assembly.sandbox._BASE", sandboxes),
        patch("fim_one.web.services.quota_backend.usage_and_cap", new=AsyncMock(return_value=(0, 0))),
        patch(
            "fim_one.web.services.quota_enforcer.is_user_unentitled",
            new=AsyncMock(return_value=False),
        ),
    ):
        yield Rig(backend, factory, service, owner, admin, wf, sandboxes)
        # Let background continuations finish before the database goes.
        for _ in range(200):
            if not service.active_run_ids():
                break
            await asyncio.sleep(0.02)


@pytest.fixture()
def hooked(rig: Rig) -> Any:
    """The process's run service continues runs when their requests are decided."""
    remove = install_resume_on_decision(rig.service)
    yield rig.service
    remove()


# ---------------------------------------------------------------------------
# A decision continues the run
# ---------------------------------------------------------------------------


class TestADecisionContinuesTheRun:
    async def test_the_run_waits_without_staying_in_the_process(self, rig: Rig) -> None:
        outcome = await rig.start()

        assert outcome.status == "waiting"
        request = await rig.request_of(outcome.run_id)
        assert outcome.waiting == [
            {
                "node_id": "human_1",
                "confirmation_id": request.id,
                "kind": "workflow_approval",
                "expires_at": outcome.waiting[0]["expires_at"],
            }
        ]
        run = await rig.run(outcome.run_id)
        assert (run.status, run.completed_at, run.outputs) == ("waiting", None, None)
        assert run.suspend_state["variables"]["start_1.q"] == "ship it?"
        assert run.node_results["human_1"]["status"] == "waiting"
        assert run.node_results["start_1"]["status"] == "completed"
        assert rig.service.active_run_ids() == []

    async def test_approved_the_run_goes_on_after_the_step(
        self, rig: Rig, hooked: rs.WorkflowRunService
    ) -> None:
        outcome = await rig.start()
        waiting = await rig.run(outcome.run_id)
        request = await rig.request_of(outcome.run_id)

        await rig.decide(request.id, "approve", note="go ahead")

        run = await rig.ended(outcome.run_id)
        assert run.status == "completed"
        # A variable from before the wait and the decision itself.
        assert run.outputs["asked"] == "ship it?"
        assert _decision(run)["status"] == "approved"
        assert _decision(run)["decision_note"] == "go ahead"
        assert run.suspend_state is None and run.completed_at is not None
        assert run.started_at == waiting.started_at
        assert {nid: r["status"] for nid, r in run.node_results.items()} == {
            "start_1": "completed",
            "human_1": "completed",
            "end_1": "completed",
        }
        assert _CountingEngine.continued == [run.id]

    async def test_rejected_the_run_fails_at_the_step(
        self, rig: Rig, hooked: rs.WorkflowRunService
    ) -> None:
        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)

        await rig.decide(request.id, "reject", note="not like this")

        run = await rig.ended(outcome.run_id)
        assert run.status == "failed"
        assert "Approval rejected: not like this" in run.node_results["human_1"]["error"]
        assert {nid: r["status"] for nid, r in run.node_results.items()} == {
            "start_1": "completed",
            "human_1": "failed",  # not left as "waiting"
            "end_1": "skipped",
        }
        assert "end_1" not in {n for n, r in run.node_results.items() if r.get("status") == "completed"}

    async def test_expired_the_run_fails_as_timed_out(self, rig: Rig) -> None:
        """Nobody decides: the sweep finds the request past its deadline."""
        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)
        supervisor = WorkflowRunSupervisor(rig.service)

        assert (await supervisor.sweep()).resumed == []  # still open: left alone
        await rig.flip(request.id, expires_at=datetime.now(UTC) - timedelta(seconds=1))
        assert (await supervisor.sweep()).resumed == [outcome.run_id]

        run = await rig.ended(outcome.run_id)
        assert run.status == "failed"
        assert "Approval timed out" in run.node_results["human_1"]["error"]
        assert (await rig.request_of(run.id)).status == "expired"

    async def test_a_trigger_caller_is_told_the_run_waits(self, rig: Rig) -> None:
        from fim_one.web.schemas.workflow import WorkflowTriggerRequest

        response = await wf_api._execute_trigger(
            await rig.workflow(), WorkflowTriggerRequest(inputs={"q": "x"})
        )

        assert response.data["status"] == "waiting"
        assert response.data["error"] is None and response.data["error_code"] is None
        # Still in flight: a second trigger is refused until it is decided.
        with pytest.raises(rs.RunRefusedError) as refused:
            await rig.service._refuse_if_running(rig.wf.id)
        assert refused.value.error_code == "workflow_already_running"


# ---------------------------------------------------------------------------
# A restart in between
# ---------------------------------------------------------------------------


class TestRestart:
    async def test_approved_after_a_restart_the_run_completes(self, rig: Rig) -> None:
        """Acceptance: restart the service while a run waits, approve, the run completes."""
        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)

        # The process that started the run is gone; a new one is up.
        after_restart = _new_service()
        remove = install_resume_on_decision(after_restart)
        try:
            await rig.decide(request.id, "approve")
            run = await rig.ended(outcome.run_id)
        finally:
            remove()

        assert run.status == "completed"
        assert run.outputs["asked"] == "ship it?"
        assert _decision(run)["status"] == "approved"
        assert run.worker_id == rs.WORKER_ID

    async def test_the_process_that_started_the_run_has_really_exited(
        self, rig: Rig, hooked: rs.WorkflowRunService, tmp_path: Path
    ) -> None:
        """A second server process, for real: it starts the run, reaches the
        approval and is killed. This process, which never saw the run, takes
        the decision and finishes it from the record alone."""
        if rig.backend != "sqlite":
            pytest.skip("the child process opens the SQLite file")
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            _CHILD_SERVER,
            str(tmp_path / "suspend.db"),
            rig.wf.id,
            rig.owner.id,
            env={**os.environ, "WORKFLOW_HUMAN_INTERVENTION_ENABLED": "true"},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(child.communicate(), timeout=60)
        assert child.returncode == -9, err.decode()[-2000:]  # killed, no shutdown
        run_id, child_worker = out.decode().split()
        waiting = await rig.run(run_id)
        assert waiting.status == "waiting"
        assert waiting.worker_id == child_worker != rs.WORKER_ID

        request = await rig.request_of(run_id)
        await rig.decide(request.id, "approve")

        run = await rig.ended(run_id)
        assert run.status == "completed"
        assert run.outputs["asked"] == "from the other process"
        assert run.worker_id == rs.WORKER_ID

    async def test_a_decision_made_while_nothing_listened_is_picked_up_at_startup(
        self, rig: Rig
    ) -> None:
        """The deciding process died right after the decision (or never had the hook)."""
        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)
        await rig.decide(request.id, "approve")  # no listener registered
        assert (await rig.run(outcome.run_id)).status == "waiting"

        after_restart = _new_service()
        report = await WorkflowRunSupervisor(after_restart).sweep()

        assert report.resumed == [outcome.run_id]
        run = await rig.ended(outcome.run_id)
        assert run.status == "completed"

    async def test_a_waiting_run_with_no_readable_state_is_closed(self, rig: Rig) -> None:
        outcome = await rig.start()
        async with rig.factory() as db:
            run = await db.get(WorkflowRun, outcome.run_id)
            assert run is not None
            run.suspend_state = None
            await db.commit()

        await WorkflowRunSupervisor(rig.service).sweep()

        run = await rig.ended(outcome.run_id)
        assert (run.status, run.error) == ("failed", rs.RUN_NOT_RESUMABLE_ERROR)
        assert (await rig.request_of(run.id)).status == "expired"


# ---------------------------------------------------------------------------
# Exactly one continuation
# ---------------------------------------------------------------------------


class TestOneContinuation:
    async def test_many_processes_continue_a_decided_run_once(self, rig: Rig) -> None:
        """Every process hears of the decision at once (callback, sweep, retry)."""
        for _ in range(ROUNDS):
            outcome = await rig.start()
            request = await rig.request_of(outcome.run_id)
            await rig.flip(request.id, status="approved")
            _CountingEngine.continued = []
            workers = [_new_service() for _ in range(WORKERS)]

            claimed = await asyncio.gather(
                *(worker.resume_run(outcome.run_id) for worker in workers),
                *(worker.resume_run(outcome.run_id) for worker in workers),
            )

            winners = [started for started in claimed if started is not None]
            assert len(winners) == 1
            await winners[0].wait()
            run = await rig.ended(outcome.run_id)
            assert run.status == "completed"
            assert _CountingEngine.continued == [outcome.run_id]
            # Later tries find nothing to do.
            assert await workers[0].resume_run(outcome.run_id) is None

    async def test_the_same_approval_decided_on_two_surfaces_at_once(self, rig: Rig) -> None:
        """Acceptance: two surfaces, two processes, one decision, one continuation."""
        portal, channel = _new_service(), _new_service()
        removers = [install_resume_on_decision(portal), install_resume_on_decision(channel)]
        try:
            for round_no in range(ROUNDS):
                outcome = await rig.start()
                request = await rig.request_of(outcome.run_id)
                _CountingEngine.continued = []

                results = await asyncio.gather(
                    rig.decide(request.id, "approve", by=rig.owner),
                    rig.decide(request.id, "reject", by=rig.admin, note="no"),
                    return_exceptions=True,
                )

                decided = [r for r in results if not isinstance(r, BaseException)]
                refused = [r for r in results if isinstance(r, BaseException)]
                assert len(decided) == 1 and len(refused) == 1, (round_no, results)
                assert isinstance(refused[0], AssemblyRefusal)
                assert refused[0].error_code == "confirmation_already_decided"
                run = await rig.ended(outcome.run_id)
                assert run.status == ("completed" if decided[0].status == "approved" else "failed")
                assert _CountingEngine.continued == [outcome.run_id]
        finally:
            for remove in removers:
                remove()
            for service in (portal, channel):
                for _ in range(200):
                    if not service.active_run_ids():
                        break
                    await asyncio.sleep(0.02)

    async def test_a_decision_made_while_another_branch_still_runs_is_not_lost(
        self, rig: Rig, hooked: rs.WorkflowRunService
    ) -> None:
        """The run is busy when the decision comes in: nobody can continue it
        then, so the run looks at its requests once more when it stops."""
        release = asyncio.Event()
        asked = asyncio.Event()

        async def slow_branch(node: Any, store: Any, ctx: Any) -> Any:
            from fim_one.core.workflow.types import NodeResult, NodeStatus

            await release.wait()
            return NodeResult(node_id=node.id, status=NodeStatus.COMPLETED, output="ok")

        await rig.workflow(FAILING_BESIDE_APPROVAL)
        with patch("fim_one.core.workflow.nodes.ConnectorExecutor.execute", side_effect=slow_branch):
            started = await rig.service.start_run(
                await rig.workflow(), caller_id=rig.owner.id, inputs={}, mode=rs.RunMode.INTERACTIVE
            )
            for _ in range(200):
                async with rig.factory() as s:
                    request = (await s.execute(select(ConfirmationRequest))).scalar_one_or_none()
                if request is not None:
                    asked.set()
                    break
                await asyncio.sleep(0.02)
            assert asked.is_set()
            await rig.decide(request.id, "approve")  # the run is still running
            assert (await rig.run(started.run_id)).status == "running"
            release.set()
            run = await rig.ended(started.run_id)

        assert run.status == "completed"
        assert _CountingEngine.continued == [run.id]


    async def test_a_run_continued_the_moment_it_is_stored_stays_registered(
        self, rig: Rig
    ) -> None:
        """The first part of the run is still tidying up when the continuation
        starts under the same id: the tidying must not unregister it, or the
        process would stop stamping its heartbeat and could not cancel it."""
        from fim_one.core.workflow.types import NodeResult, NodeStatus

        release = asyncio.Event()
        store_run = rig.service._persist
        continued: list[Any] = []

        async def store_then_continue(out: rs.RunOutcome, **kwargs: Any) -> None:
            await store_run(out, **kwargs)
            if out.status == "waiting":
                request = await rig.request_of(out.run_id)
                await rig.flip(request.id, status="approved")
                continued.append(await rig.service.resume_run(out.run_id))

        async def held_end(node: Any, store: Any, ctx: Any) -> Any:
            await release.wait()
            return NodeResult(node_id=node.id, status=NodeStatus.COMPLETED, output={})

        with (
            patch.object(rig.service, "_persist", store_then_continue),
            patch("fim_one.core.workflow.nodes.EndExecutor.execute", side_effect=held_end),
        ):
            outcome = await rig.start()
            assert outcome.status == "waiting" and continued[0] is not None

            assert rig.service.active_run_ids() == [outcome.run_id]
            assert rig.service.cancel_run(outcome.run_id) is True  # reachable
            release.set()
            await continued[0].wait()
        assert rig.service.active_run_ids() == []


# ---------------------------------------------------------------------------
# Cancelling or failing closes the run's requests
# ---------------------------------------------------------------------------


class TestEndingClosesRequests:
    async def test_cancelling_a_waiting_run(self, rig: Rig, hooked: rs.WorkflowRunService) -> None:
        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)
        (rig.sandboxes / f"wfrun-{outcome.run_id}").mkdir()

        async with rig.factory() as db:
            response = await wf_api.cancel_workflow_run(
                rig.wf.id, outcome.run_id, current_user=rig.owner, db=db
            )

        assert response.data["cancelled"] is True
        run = await rig.run(outcome.run_id)
        assert (run.status, run.suspend_state) == ("cancelled", None)
        assert run.completed_at is not None
        assert (await rig.request_of(run.id)).status == "expired"
        assert not (rig.sandboxes / f"wfrun-{run.id}").exists()
        # Nothing left to approve, and nothing to continue.
        with pytest.raises(AssemblyRefusal) as late:
            await rig.decide(request.id, "approve")
        assert late.value.error_code == "confirmation_already_decided"
        assert await rig.service.resume_run(run.id) is None
        assert (await rig.run(run.id)).status == "cancelled"

    async def test_a_run_that_fails_while_a_step_waits(self, rig: Rig) -> None:
        await rig.workflow(FAILING_BESIDE_APPROVAL)

        outcome = await rig.start()

        assert outcome.status == "failed"
        request = await rig.request_of(outcome.run_id)
        assert request.status == "expired"
        async with rig.factory() as db:
            actor = await resolve_principal(db, user_id=rig.owner.id, auth=AuthMethod.SESSION)
            assert await rig.confirmations().pending_for(db, actor) == []

    async def test_a_waiting_run_cannot_be_deleted_but_a_cancelled_one_can(self, rig: Rig) -> None:
        from fim_one.web.exceptions import AppError

        outcome = await rig.start()
        async with rig.factory() as db:
            with pytest.raises(AppError) as refused:
                await wf_api.delete_workflow_run(
                    rig.wf.id, outcome.run_id, current_user=rig.owner, db=db
                )
            assert refused.value.error_code == "cannot_delete_active_run"
            await wf_api.cancel_workflow_run(
                rig.wf.id, outcome.run_id, current_user=rig.owner, db=db
            )
        async with rig.factory() as db:
            await wf_api.delete_workflow_run(
                rig.wf.id, outcome.run_id, current_user=rig.owner, db=db
            )
        async with rig.factory() as db:
            assert await db.get(WorkflowRun, outcome.run_id) is None


# ---------------------------------------------------------------------------
# A run left behind by a dead process
# ---------------------------------------------------------------------------


async def _left_running(
    rig: Rig, *, heartbeat_age: float | None, started_age: float = 3600.0
) -> str:
    """A run record as a process that died mid-run leaves it."""
    now = datetime.now(UTC)
    run_id = str(uuid.uuid4())
    async with rig.factory() as db:
        db.add(
            WorkflowRun(
                id=run_id,
                workflow_id=rig.wf.id,
                user_id=rig.owner.id,
                blueprint_snapshot=BLUEPRINT,
                inputs={},
                status="running",
                mode="trigger",
                started_at=now - timedelta(seconds=started_age),
                worker_id="host-that-died-1234-abcd" if heartbeat_age is not None else None,
                heartbeat_at=(
                    now - timedelta(seconds=heartbeat_age) if heartbeat_age is not None else None
                ),
            )
        )
        await db.commit()
    return run_id


def _old(path: Path, age_seconds: float) -> Path:
    path.mkdir()
    (path / "work.txt").write_text("x")
    moment = time.time() - age_seconds
    os.utime(path / "work.txt", (moment, moment))
    os.utime(path, (moment, moment))
    return path


class TestCrashRecovery:
    async def test_a_run_whose_process_died_is_closed_as_failed(self, rig: Rig) -> None:
        dead = await _left_running(rig, heartbeat_age=600)
        sandbox = _old(rig.sandboxes / f"wfrun-{dead}", 5)
        # It was waiting on nothing, but had asked for a confirmation earlier.
        async with rig.factory() as db:
            actor = await resolve_principal(db, user_id=rig.owner.id, auth=AuthMethod.SESSION)
        from fim_one.core.assembly.confirmation import ConfirmationSpec

        request = await rig.confirmations().request(
            ConfirmationSpec(
                kind="workflow_approval",
                initiator=actor,
                binding=None,
                org_id=None,
                title="t",
                description=None,
                payload={},
                eligible_scope="initiator",
                eligible_user_id=rig.owner.id,
                delivery="inline_only",
                channel_id=None,
                timeout_seconds=3600,
                workflow_run_id=dead,
                node_id="human_1",
            )
        )
        with pytest.raises(rs.RunRefusedError):
            await rig.service._refuse_if_running(rig.wf.id)  # the trigger key is stuck

        report = await WorkflowRunSupervisor(rig.service, stale_seconds=90).sweep()

        assert report.closed == [dead]
        run = await rig.run(dead)
        assert (run.status, run.error) == ("failed", rs.RUN_INTERRUPTED_ERROR)
        assert run.completed_at is not None
        async with rig.factory() as db:
            assert (await db.get(ConfirmationRequest, request.id)).status == "expired"  # type: ignore[union-attr]
        assert not sandbox.exists()
        await rig.service._refuse_if_running(rig.wf.id)  # the trigger key is free again

    async def test_a_run_from_before_heartbeats_existed_is_closed_too(self, rig: Rig) -> None:
        legacy = await _left_running(rig, heartbeat_age=None, started_age=7200)

        report = await WorkflowRunSupervisor(rig.service).sweep()

        assert report.closed == [legacy]

    async def test_a_run_another_live_process_is_executing_is_left_alone(self, rig: Rig) -> None:
        alive = await _left_running(rig, heartbeat_age=20, started_age=7200)

        report = await WorkflowRunSupervisor(rig.service, stale_seconds=90).sweep()

        assert report.closed == []
        assert (await rig.run(alive)).status == "running"

    async def test_a_heartbeat_keeps_a_long_run_alive(self, rig: Rig) -> None:
        """This process's own run: stamped by ``beat``, never closed by a sweep."""
        release = asyncio.Event()

        class _Held(_CountingEngine):
            async def execute_streaming(self, *args: Any, **kwargs: Any) -> Any:
                await release.wait()
                yield "run_completed", {"status": "completed", "outputs": {}}

        service = rs.WorkflowRunService(engine_factory=_Held)
        started = await service.start_run(
            await rig.workflow(), caller_id=rig.owner.id, inputs={}, mode=rs.RunMode.INTERACTIVE
        )
        long_ago = datetime.now(UTC) - timedelta(hours=1)
        async with rig.factory() as db:
            run = await db.get(WorkflowRun, started.run_id)
            assert run is not None and run.worker_id == rs.WORKER_ID
            run.heartbeat_at = long_ago
            await db.commit()
        mine = WorkflowRunSupervisor(service, stale_seconds=90)
        other = WorkflowRunSupervisor(_new_service(), stale_seconds=90)

        assert (await mine.sweep()).closed == []  # executing here
        await mine.beat()
        stamped = (await rig.run(started.run_id)).heartbeat_at
        assert stamped is not None and stamped.replace(tzinfo=UTC) > long_ago
        assert (await other.sweep()).closed == []  # another process sees it alive

        release.set()
        await started.wait()
        assert (await rig.run(started.run_id)).status == "completed"

    async def test_several_processes_starting_at_once_close_it_once(self, rig: Rig) -> None:
        dead = [await _left_running(rig, heartbeat_age=600) for _ in range(5)]
        supervisors = [WorkflowRunSupervisor(_new_service()) for _ in range(WORKERS)]

        reports = await asyncio.gather(*(s.sweep() for s in supervisors))

        closed = [run_id for report in reports for run_id in report.closed]
        assert sorted(closed) == sorted(dead)  # each exactly once
        for run_id in dead:
            assert (await rig.run(run_id)).status == "failed"

    async def test_a_run_cancelled_from_another_process_stops_at_the_next_heartbeat(
        self, rig: Rig
    ) -> None:
        class _UntilCancelled(_CountingEngine):
            async def execute_streaming(self, *args: Any, **kwargs: Any) -> Any:
                assert self._cancel_event is not None
                await self._cancel_event.wait()
                yield "run_completed", {"status": "cancelled", "error": "Execution cancelled"}

        here = rs.WorkflowRunService(engine_factory=_UntilCancelled)
        elsewhere = _new_service()
        started = await here.start_run(
            await rig.workflow(), caller_id=rig.owner.id, inputs={}, mode=rs.RunMode.INTERACTIVE
        )

        assert await elsewhere.cancel_stored_run(started.run_id) is True
        assert not started.done
        await WorkflowRunSupervisor(here).beat()
        await asyncio.wait_for(started.wait(), timeout=5)

        run = await rig.run(started.run_id)
        assert (run.status, run.error) == ("cancelled", "Execution cancelled")

    async def test_leftover_run_directories_are_removed(self, rig: Rig) -> None:
        outcome = await rig.start()  # a waiting run: its files are still needed
        ended = await _left_running(rig, heartbeat_age=600)
        async with rig.factory() as db:
            run = await db.get(WorkflowRun, ended)
            assert run is not None
            run.status = "completed"
            await db.commit()
        hour, two_days = 3600.0, 2 * 86400.0
        kept = [
            _old(rig.sandboxes / f"wfrun-{outcome.run_id}", two_days),  # run waits
            _old(rig.sandboxes / f"wfrun-{uuid.uuid4()}", 5),  # no record, but just made
            _old(rig.sandboxes / f"subagent-{uuid.uuid4()}", hour),  # may be in use
            _old(rig.sandboxes / f"evalrun-{uuid.uuid4()}", hour),
            _old(rig.sandboxes / f"wfrun-test-{uuid.uuid4()}", hour),
            _old(rig.sandboxes / str(uuid.uuid4()), two_days),  # a conversation's
        ]
        removed = [
            _old(rig.sandboxes / f"wfrun-{ended}", hour),  # its run is over
            _old(rig.sandboxes / f"wfrun-{uuid.uuid4()}", hour),  # no such run
            _old(rig.sandboxes / f"subagent-{uuid.uuid4()}", two_days),
            _old(rig.sandboxes / f"evalrun-{uuid.uuid4()}", two_days),
            _old(rig.sandboxes / f"wfrun-test-{uuid.uuid4()}", two_days),
        ]

        report = await WorkflowRunSupervisor(rig.service).sweep()

        assert sorted(report.removed_sandboxes) == sorted(p.name for p in removed)
        assert all(p.exists() for p in kept)
        assert not any(p.exists() for p in removed)


# ---------------------------------------------------------------------------
# A run is visible while it runs, whoever is connected
# ---------------------------------------------------------------------------


class TestLiveState:
    async def test_the_run_record_shows_progress_while_the_run_is_going(self, rig: Rig) -> None:
        release = asyncio.Event()

        class _TwoSteps(_CountingEngine):
            async def execute_streaming(self, *args: Any, **kwargs: Any) -> Any:
                yield "node_started", {"node_id": "start_1"}
                yield "node_completed", {"node_id": "start_1", "status": "completed"}
                await release.wait()
                yield "run_completed", {"status": "completed", "outputs": {"ok": 1}}

        service = rs.WorkflowRunService(engine_factory=_TwoSteps)
        with patch.object(rs, "PROGRESS_SAVE_INTERVAL", 0.0):
            started = await service.start_run(
                await rig.workflow(), caller_id=rig.owner.id, inputs={}, mode=rs.RunMode.INTERACTIVE
            )
            await asyncio.sleep(0.2)

            # What someone opening the run in another tab is given.
            async with rig.factory() as db:
                shown = (
                    await wf_api.get_workflow_run(
                        rig.wf.id, started.run_id, current_user=rig.owner, db=db
                    )
                ).data
            assert shown["status"] == "running"
            assert shown["node_results"]["start_1"]["status"] == "completed"

            release.set()
            await started.wait()

    async def test_a_waiting_run_says_what_it_waits_on(self, rig: Rig) -> None:
        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)

        async with rig.factory() as db:
            shown = (
                await wf_api.get_workflow_run(
                    rig.wf.id, outcome.run_id, current_user=rig.owner, db=db
                )
            ).data

        assert shown["status"] == "waiting"
        assert [(w["node_id"], w["confirmation_id"], w["kind"]) for w in shown["waiting_for"]] == [
            ("human_1", request.id, "workflow_approval")
        ]
        assert "suspend_state" not in shown



# ---------------------------------------------------------------------------
# A connector action that requires confirmation
# ---------------------------------------------------------------------------


class _Adapter:
    """Stands in for the connector action's tool: records what it is run with."""

    category = "connector"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, **params: Any) -> str:
        self.calls.append(params)
        return "wiped"


@pytest.fixture()
async def flagged(rig: Rig) -> AsyncIterator[_Adapter]:
    """The workflow becomes start -> a flagged connector action -> end."""
    from fim_one.db.models.connector import Connector, ConnectorAction

    async with rig.factory() as s:
        connector = Connector(
            id=str(uuid.uuid4()),
            user_id=rig.owner.id,
            name="Ledger",
            type="api",
            base_url="https://ledger.example.test",
            auth_type="none",
            status="published",
            is_active=True,
        )
        s.add(connector)
        await s.flush()
        s.add(
            ConnectorAction(
                id="wipe-1",
                connector_id=connector.id,
                name="wipe",
                method="POST",
                path="/wipe",
                requires_confirmation=True,
            )
        )
        await s.commit()
    await rig.workflow(
        {
            "schema_version": 2,
            "nodes": [
                _node("start_1", "start", "START"),
                _node(
                    "connector_1",
                    "connector",
                    "CONNECTOR",
                    connector_id=connector.id,
                    action_id="wipe-1",
                    parameters={"target": "{{start_1.q}}"},
                ),
                _node("end_1", "end", "END", output_mapping={"result": "{{connector_1.output}}"}),
            ],
            "edges": [_edge("start_1", "connector_1"), _edge("connector_1", "end_1")],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        }
    )
    adapter = _Adapter()
    with patch(
        "fim_one.core.assembly.toolset.connector_tools", new=AsyncMock(return_value=[adapter])
    ):
        yield adapter


class TestConnectorActionConfirmation:
    async def test_the_run_waits_and_the_action_runs_once_approved(
        self, rig: Rig, flagged: _Adapter, hooked: rs.WorkflowRunService
    ) -> None:
        outcome = await rig.start(inputs={"q": "ledger-7"})

        assert outcome.status == "waiting"
        assert flagged.calls == []  # not run before anyone said yes
        request = await rig.request_of(outcome.run_id)
        assert (request.kind, request.status, request.node_id) == (
            "confirmation",
            "pending",
            "connector_1",
        )
        # Decided by whoever the run belongs to.
        assert (request.eligible_scope, request.eligible_user_id) == ("initiator", rig.owner.id)
        assert request.payload["tool_name"] == "Ledger.wipe"
        assert request.payload["tool_args"] == {"target": "ledger-7"}
        assert request.title == "Confirm: Ledger / wipe"

        await rig.decide(request.id, "approve")

        run = await rig.ended(outcome.run_id)
        assert run.status == "completed"
        assert flagged.calls == [{"target": "ledger-7"}]
        assert run.outputs == {"result": "wiped"}

    async def test_rejected_the_step_fails_and_the_action_never_runs(
        self, rig: Rig, flagged: _Adapter, hooked: rs.WorkflowRunService
    ) -> None:
        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)

        await rig.decide(request.id, "reject", note="wrong ledger")

        run = await rig.ended(outcome.run_id)
        assert run.status == "failed"
        assert run.node_results["connector_1"]["error"] == (
            "Action 'wipe' was not run: the confirmation was rejected (wrong ledger)."
        )
        assert flagged.calls == []

    async def test_unanswered_the_step_fails_when_the_request_runs_out(
        self, rig: Rig, flagged: _Adapter
    ) -> None:
        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)
        await rig.flip(request.id, expires_at=datetime.now(UTC) - timedelta(seconds=1))

        await WorkflowRunSupervisor(rig.service).sweep()

        run = await rig.ended(outcome.run_id)
        assert run.status == "failed"
        assert run.node_results["connector_1"]["error"] == (
            "Action 'wipe' was not run: nobody confirmed it within 24 hours."
        )
        assert flagged.calls == []

    async def test_a_trigger_run_is_confirmed_by_the_workflows_owner(
        self, rig: Rig, flagged: _Adapter, hooked: rs.WorkflowRunService
    ) -> None:
        async with rig.factory() as s:
            stranger = User(id=str(uuid.uuid4()), email="stranger@x.io")
            s.add(stranger)
            await s.commit()

        outcome = await rig.start(caller_id=None, mode=rs.RunMode.TRIGGER)

        request = await rig.request_of(outcome.run_id)
        assert (request.eligible_scope, request.eligible_user_id) == ("initiator", rig.wf.user_id)
        with pytest.raises(AssemblyRefusal) as refused:
            await rig.decide(request.id, "approve", by=stranger)
        assert refused.value.error_code == "confirmation_forbidden"
        await rig.decide(request.id, "approve", by=rig.admin)  # an administrator may
        assert (await rig.ended(outcome.run_id)).status == "completed"
        assert len(flagged.calls) == 1

    async def test_a_workflow_run_as_a_tool_cannot_ask_and_fails_the_step(
        self, rig: Rig, flagged: _Adapter
    ) -> None:
        outcome = await rig.start(mode=rs.RunMode.TOOL)

        assert outcome.status == "failed"
        error = outcome.node_results["connector_1"]["error"]
        assert "requires confirmation, and there is nobody to ask here" in error
        assert flagged.calls == []
        async with rig.factory() as s:
            assert (await s.execute(select(ConfirmationRequest))).scalars().all() == []


# ---------------------------------------------------------------------------
# The approval inbox
# ---------------------------------------------------------------------------


async def _inbox(rig: Rig, user: User) -> dict[str, Any]:
    async with rig.factory() as db:
        inbox = (await wf_api.approval_inbox(current_user=user, db=db)).data
        count = (await wf_api.approval_inbox_count(current_user=user, db=db)).data
    assert count == {"count": inbox["count"]} and inbox["count"] == len(inbox["items"])
    return dict(inbox)


async def _respond(rig: Rig, request_id: str, decision: str, user: User, reason: str = "") -> Any:
    from fim_one.web.api.confirmations import ConfirmationRespondRequest, respond_to_confirmation

    async with rig.factory() as db:
        return await respond_to_confirmation(
            request_id,
            ConfirmationRespondRequest(decision=decision, reason=reason or None),  # type: ignore[arg-type]
            current_user=user,
            db=db,
        )


class TestApprovalInbox:
    async def test_an_approval_is_listed_with_where_it_comes_from(self, rig: Rig) -> None:
        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)

        (item,) = (await _inbox(rig, rig.owner))["items"]

        assert item["id"] == request.id
        assert (item["kind"], item["status"]) == ("workflow_approval", "pending")
        assert (item["title"], item["description"]) == ("Sign off", "Check the figures first.")
        assert (item["workflow_id"], item["workflow_name"]) == (rig.wf.id, "sign-off")
        assert (item["run_id"], item["run_mode"]) == (outcome.run_id, "interactive")
        assert (item["node_id"], item["step_label"]) == ("human_1", "Manager sign-off")
        assert item["started_by"] == {"id": rig.owner.id, "name": "owner@x.io"}
        assert item["created_at"] and item["expires_at"]
        assert (item["action"], item["arguments"]) == (None, None)
        # An administrator may decide any request but is not asked to.
        assert (await _inbox(rig, rig.admin))["items"] == []

    async def test_a_connector_confirmation_says_what_is_about_to_be_called(
        self, rig: Rig, flagged: _Adapter
    ) -> None:
        outcome = await rig.start(inputs={"q": "ledger-7"})

        (item,) = (await _inbox(rig, rig.owner))["items"]

        assert item["kind"] == "confirmation"
        assert item["title"] == "Confirm: Ledger / wipe"
        assert (item["action"], item["arguments"]) == ("Ledger.wipe", {"target": "ledger-7"})
        assert (item["run_id"], item["node_id"]) == (outcome.run_id, "connector_1")

    async def test_deciding_from_the_inbox_continues_the_run_and_empties_the_list(
        self, rig: Rig, hooked: rs.WorkflowRunService
    ) -> None:
        from fim_one.web.exceptions import AppError

        outcome = await rig.start()
        (item,) = (await _inbox(rig, rig.owner))["items"]

        answer = await _respond(rig, item["id"], "approve", rig.owner, "looks right")

        assert answer.status == "approved"
        run = await rig.ended(outcome.run_id)
        assert run.status == "completed"
        assert _decision(run)["decision_note"] == "looks right"
        assert (await _inbox(rig, rig.owner))["count"] == 0
        # Another surface that still shows the request and clicks: told how
        # it was decided, not that something broke.
        with pytest.raises(AppError) as late:
            await _respond(rig, item["id"], "reject", rig.admin)
        assert (late.value.status_code, late.value.error_code) == (
            409,
            "confirmation_already_decided",
        )
        assert late.value.detail_args == {"status": "approved"}

    async def test_a_request_past_its_deadline_is_not_listed(self, rig: Rig) -> None:
        outcome = await rig.start()
        request = await rig.request_of(outcome.run_id)
        await rig.flip(request.id, expires_at=datetime.now(UTC) - timedelta(seconds=1))

        assert (await _inbox(rig, rig.owner))["count"] == 0

    async def test_the_badge_count_is_a_count_not_a_load_of_the_rows(self, rig: Rig) -> None:
        """Asked for every 30 s by every open tab: no request row, with its
        payload, is read to answer it."""
        for _ in range(3):
            await rig.start()
        expired = await rig.request_of((await rig.start()).run_id)
        await rig.flip(expired.id, expires_at=datetime.now(UTC) - timedelta(seconds=1))
        assert (await _inbox(rig, rig.owner))["count"] == 3

        loaded: list[Any] = []
        from sqlalchemy import event

        from fim_one.db.models.channel import ConfirmationRequest as Row

        def on_load(target: Any, _context: Any) -> None:
            loaded.append(target)

        event.listen(Row, "load", on_load)
        try:
            async with rig.factory() as db:
                count = (await wf_api.approval_inbox_count(current_user=rig.owner, db=db)).data
                nobody = (await wf_api.approval_inbox_count(current_user=rig.admin, db=db)).data
        finally:
            event.remove(Row, "load", on_load)
        assert count == {"count": 3} and nobody == {"count": 0}
        assert loaded == []

    async def test_requests_of_chat_tool_calls_are_not_in_it(self, rig: Rig) -> None:
        from fim_one.core.assembly.confirmation import ConfirmationSpec

        async with rig.factory() as db:
            actor = await resolve_principal(db, user_id=rig.owner.id, auth=AuthMethod.SESSION)
        await rig.confirmations().request(
            ConfirmationSpec(
                kind="confirmation",
                initiator=actor,
                binding=None,
                org_id=None,
                title="chat tool call",
                description=None,
                payload={"tool_name": "x", "tool_args": {}},
                eligible_scope="initiator",
                eligible_user_id=rig.owner.id,
                delivery="inline_only",
                channel_id=None,
                timeout_seconds=600,
                conversation_id="conv-1",
            )
        )

        assert (await _inbox(rig, rig.owner))["count"] == 0

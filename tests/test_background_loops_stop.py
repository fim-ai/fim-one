"""Background loops stop when they are cancelled, even mid database call.

A cancellation that arrives while a database call is awaited can come out
of the driver's clean-up as an ordinary error: SQLAlchemy on aiosqlite
cancels the connection's termination too, and the rollback that follows
raises ``ValueError: no active connection``. Each loop here logs ordinary
errors and goes on, so it went on: the cancellation was lost, and the
shutdown that awaits the loop (``web/app.py`` lifespan) waited for ever.
Seen as the run supervisor of a child process in
``tests/test_workflow_suspend_hardening.py`` never exiting.

Each case replaces the loop's database pass with one that turns its
cancellation into that error, starts the loop, cancels it during the pass,
and expects it to end.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, ClassVar

import pytest


class _PassLosingCancellation:
    """A database pass whose cancellation comes out as an ordinary error."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.lost = 0

    async def __call__(self, *_args: Any, **_kwargs: Any) -> Any:
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if self.lost:
                # Once is enough to show it; a second cancel ends a loop
                # that went on, so the test can fail instead of hanging.
                raise
            self.lost += 1
            raise ValueError("no active connection") from None


async def _noop(*_args: Any, **_kwargs: Any) -> Any:
    return None


@asynccontextmanager
async def _no_session() -> AsyncIterator[None]:
    yield None


@pytest.fixture()
def no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fim_one.db.create_session", _no_session)


def _supervisor(part: str) -> Callable[[pytest.MonkeyPatch, Any], Awaitable[None]]:
    def start(monkeypatch: pytest.MonkeyPatch, fake: Any) -> Awaitable[None]:
        from fim_one.core.workflow.run_recovery import WorkflowRunSupervisor
        from fim_one.core.workflow.run_service import WorkflowRunService

        supervisor = WorkflowRunSupervisor(
            WorkflowRunService(), heartbeat_seconds=0.01, sweep_seconds=0.01
        )
        for name in ("beat", "_close_dead_runs", "_resume_settled_runs", "_remove_sandboxes"):
            monkeypatch.setattr(supervisor, name, fake if name == part else _noop)
        if part == "sweep":
            monkeypatch.setattr(supervisor, "sweep", fake)
        return supervisor.run_loop()

    return start


def _cleaner(monkeypatch: pytest.MonkeyPatch, fake: Any) -> Awaitable[None]:
    from fim_one.core.workflow.run_cleanup import WorkflowRunCleaner

    cleaner = WorkflowRunCleaner(cleanup_interval_hours=0)
    monkeypatch.setattr(cleaner, "cleanup", fake)
    return cleaner.run_loop()


def _audit_retention(monkeypatch: pytest.MonkeyPatch, fake: Any) -> Awaitable[None]:
    from fim_one.core.audit import retention

    monkeypatch.setattr(retention.random, "uniform", lambda _a, _b: 0)
    sweeper = retention.AuditRetentionSweeper(
        retention.Retention(audit_days=365), check_interval_seconds=0
    )
    monkeypatch.setattr(sweeper, "sweep", fake)
    return sweeper.run_loop()


def _confirmation_expirer(monkeypatch: pytest.MonkeyPatch, fake: Any) -> Awaitable[None]:
    from fim_one.core.channels.expiry import ConfirmationRequestExpirer

    expirer = ConfirmationRequestExpirer(sweep_interval_seconds=0)
    monkeypatch.setattr(expirer, "sweep", fake)
    return expirer.run_loop()


def _subscription_lifecycle(monkeypatch: pytest.MonkeyPatch, fake: Any) -> Awaitable[None]:
    from fim_one.web.services import subscription_lifecycle

    monkeypatch.setattr(subscription_lifecycle, "downgrade_expired_canceled_subscriptions", fake)
    return subscription_lifecycle._run_loop(0)


def _scheduler(monkeypatch: pytest.MonkeyPatch, fake: Any) -> Awaitable[None]:
    from fim_one.core.workflow.scheduler import WorkflowScheduler

    scheduler = WorkflowScheduler(poll_interval=1)
    monkeypatch.setattr(scheduler, "_tick", fake)
    return scheduler.run()


LOOPS = {
    "run supervisor, heartbeat": _supervisor("beat"),
    "run supervisor, startup sweep": _supervisor("sweep"),
    "run supervisor, a part of a sweep": _supervisor("_close_dead_runs"),
    "workflow run cleaner": _cleaner,
    "audit retention sweeper": _audit_retention,
    "confirmation request expirer": _confirmation_expirer,
    "subscription lifecycle": _subscription_lifecycle,
    "workflow scheduler": _scheduler,
}


@pytest.mark.usefixtures("no_database")
@pytest.mark.parametrize("start", list(LOOPS.values()), ids=list(LOOPS))
async def test_a_loop_cancelled_during_its_database_pass_stops(
    start: Callable[[pytest.MonkeyPatch, Any], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _PassLosingCancellation()
    task = asyncio.ensure_future(start(monkeypatch, fake))
    await asyncio.wait_for(fake.entered.wait(), timeout=5)

    task.cancel()

    done, _ = await asyncio.wait({task}, timeout=2)
    if not done:
        task.cancel()
        await asyncio.wait({task}, timeout=2)
        pytest.fail("the loop went on after it was cancelled")
    # Ended as a cancelled loop ends: no error for whoever awaits it.
    assert task.cancelled() or task.exception() is None


class TestTheRunSupervisorStopsWithoutBeingCancelled:
    """Shutdown asks the supervisor to stop instead of cancelling it.

    A cancellation that interrupts an aiosqlite call can also leave a task
    behind that never ends: SQLAlchemy closes the connection gracefully in
    a shielded task, falls back to stopping aiosqlite's worker thread when
    that is cancelled too, and the graceful close then waits for an answer
    from the thread that has stopped (SQLAlchemy 2.0.47, aiosqlite 0.22.1).
    ``asyncio.run`` waits for that task at exit, and the process never
    ends. Asked to stop, the supervisor finishes the pass it is in.
    """

    async def test_asked_to_stop_during_a_pass_it_finishes_the_pass_and_ends(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fim_one.core.workflow.run_recovery import WorkflowRunSupervisor
        from fim_one.core.workflow.run_service import WorkflowRunService

        supervisor = WorkflowRunSupervisor(
            WorkflowRunService(), heartbeat_seconds=0.01, sweep_seconds=3600
        )
        in_pass, cancelled, finished = asyncio.Event(), [], []

        async def beat() -> None:
            in_pass.set()
            try:
                await asyncio.sleep(0.2)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
            finished.append(True)

        monkeypatch.setattr(supervisor, "sweep", _noop)
        monkeypatch.setattr(supervisor, "beat", beat)
        task = asyncio.ensure_future(supervisor.run_loop())
        await asyncio.wait_for(in_pass.wait(), timeout=5)

        supervisor.stop()

        await asyncio.wait_for(task, timeout=2)
        assert (cancelled, finished[:1]) == ([], [True])

    async def test_asked_to_stop_while_it_waits_it_ends_at_once(self) -> None:
        from fim_one.core.workflow.run_recovery import WorkflowRunSupervisor
        from fim_one.core.workflow.run_service import WorkflowRunService

        supervisor = WorkflowRunSupervisor(
            WorkflowRunService(), heartbeat_seconds=3600, sweep_seconds=3600
        )
        supervisor.sweep = _noop  # type: ignore[method-assign]
        task = asyncio.ensure_future(supervisor.run_loop())
        await asyncio.sleep(0.05)

        supervisor.stop()

        await asyncio.wait_for(task, timeout=2)

    async def test_the_application_stops_it_by_asking(self) -> None:
        import inspect

        from fim_one.web import app

        source = "".join(inspect.getsource(app.lifespan).split())
        after = source.split("run_supervisor.stop()", 1)[1]
        # Asked first; then waited for without a cancel; cancelled only after.
        assert after.startswith("ifnotawaitstop_background(run_supervisor_task,timeout=15,"), after[
            :120
        ]
        assert "cancel=False" in after.split("awaitstop_background(run_supervisor_task", 2)[1]


class _Session:
    """A stand-in session: no database, no rows."""

    bind = None

    async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        class _Result:
            def scalars(self) -> Any:
                return self

            def all(self) -> list[Any]:
                return list(_Session.rows)

        return _Result()

    rows: ClassVar[list[Any]] = []


@asynccontextmanager
async def _stand_in_session() -> AsyncIterator[_Session]:
    yield _Session()


async def _ends(task: asyncio.Future[Any], seconds: float = 2) -> bool:
    done, _ = await asyncio.wait({task}, timeout=seconds)
    if not done:
        task.cancel()  # the fake ends on a second cancel
        await asyncio.wait({task}, timeout=2)
    return bool(done)


class TestAPassThatCatchesTheErrorItselfStillStops:
    """Recheck R2. A pass that catches errors per item (a table of the
    retention sweep, a workflow of the scheduler's tick) swallowed the
    converted cancellation and returned normally; the loop then slept its
    whole interval, and the shutdown that awaits it waited as long."""

    async def test_the_retention_sweep_cancelled_inside_a_tables_delete(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fim_one.core.audit import retention

        monkeypatch.setattr("fim_one.db.create_session", _stand_in_session)
        monkeypatch.setattr(retention.random, "uniform", lambda _a, _b: 0)
        sweeper = retention.AuditRetentionSweeper(
            retention.Retention(audit_days=365), check_interval_seconds=3600
        )
        fake = _PassLosingCancellation()

        async def due(_moment: Any) -> bool:
            return True

        monkeypatch.setattr(sweeper, "_due", due)
        monkeypatch.setattr(sweeper, "_leave_a_record", _noop)
        monkeypatch.setattr(sweeper, "_clear_payloads", _noop)
        monkeypatch.setattr(sweeper, "_delete_older", fake)
        task = asyncio.ensure_future(sweeper.run_loop())
        await asyncio.wait_for(fake.entered.wait(), timeout=5)

        task.cancel()

        assert await _ends(task), "the sweeper slept its interval after it was cancelled"

    async def test_the_scheduler_cancelled_inside_one_workflows_dispatch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        from fim_one.core.workflow.scheduler import WorkflowScheduler

        monkeypatch.setattr("fim_one.db.create_session", _stand_in_session)
        monkeypatch.setattr(_Session, "rows", [SimpleNamespace(id="wf-1", name="n")])
        scheduler = WorkflowScheduler(poll_interval=3600)
        fake = _PassLosingCancellation()
        monkeypatch.setattr(scheduler, "_is_due", lambda _wf, _now: True)

        async def enabled(*_args: Any) -> bool:
            return True

        monkeypatch.setattr("fim_one.web.services.feature_flags.is_feature_enabled", enabled)
        monkeypatch.setattr(scheduler, "_dispatch_run", fake)
        task = asyncio.ensure_future(scheduler.run())
        await asyncio.wait_for(fake.entered.wait(), timeout=5)

        task.cancel()

        assert await _ends(task), "the scheduler waited its interval after it was cancelled"


class TestTheShutdownWaitsABoundedTime:
    async def test_a_task_that_does_not_end_on_cancel_is_left_after_the_timeout(self) -> None:
        from fim_one.core.utils import stop_background

        release = asyncio.Event()

        async def stubborn() -> None:
            while not release.is_set():
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    continue

        task = asyncio.ensure_future(stubborn())
        await asyncio.sleep(0)

        started = asyncio.get_running_loop().time()
        assert await stop_background(task, timeout=0.3, name="stubborn") is False
        assert asyncio.get_running_loop().time() - started < 1.5
        release.set()
        task.cancel()
        await asyncio.wait({task}, timeout=2)

    async def test_the_application_bounds_every_background_task(self) -> None:
        import inspect

        from fim_one.web import app

        source = "".join(inspect.getsource(app.lifespan).split())
        shutdown = source.split("yield", 1)[1]
        for task in (
            "subscription_lifecycle_task",
            "confirmation_expirer_task",
            "run_supervisor_task",
            "cleanup_task",
            "audit_retention_task",
            "scheduler_task",
        ):
            assert f"stop_background({task}," in shutdown, task
            assert f"await{task}" not in shutdown, task

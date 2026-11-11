"""Keeping workflow runs alive across decisions, restarts and dead processes.

Three jobs, all on top of :class:`~.run_service.WorkflowRunService`:

- **Continue on decision.** :func:`install_resume_on_decision` registers
  with ``ConfirmationService.on_decided``: when a request that belongs to a
  workflow run is decided, the run is continued in a background task of the
  deciding process.
- **Heartbeat.** Every run executing in this process has its record
  stamped every ``heartbeat_seconds``, with the database's clock. In a
  server the stamping is done by a thread of its own with its own
  database connection (:class:`HeartbeatThread`): the event loop that runs
  the steps can be blocked for a long time by one of them, and a run must
  not look dead because of that. A record closed by another process
  (cancelled, or taken for dead) is noticed on the same pass and the run
  is stopped here; the engine also asks before every step.
- **Sweep**, at startup and every ``sweep_seconds``:

  * a ``waiting`` run whose request is decided, expired or gone is
    continued (the deciding process died before it could, a request ran
    out of time, the service was down when the decision came in);
  * a ``running`` run whose heartbeat is older than ``stale_seconds``, by
    the database's clock, was being executed by a process that no longer
    exists: it is closed as failed and its open requests are closed. That
    also frees the trigger key's single-flight, which looks at unfinished
    runs. Its sandbox is left for the pass over unused directories, a
    grace period later;
  * a run sandbox directory (``wfrun-*``, ``subagent-*``, ``evalrun-*``)
    nobody is using is removed.

Every step is safe with several server processes doing it at once: closing
and continuing a run are each one conditional update of its record, so one
process wins and the rest do nothing; a live process's runs have a fresh
heartbeat and are left alone.

The background task plus this sweep stand in for a task queue. The seam is
``WorkflowRunService.schedule_resume``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fim_one.core.assembly.sandbox import is_run_sandbox, sandbox_base
from fim_one.core.utils import cancel_requested

from .run_service import (
    RUN_INTERRUPTED_ERROR,
    UNFINISHED_STATUSES,
    WorkflowRunService,
    get_run_service,
    request_states,
    settled_given,
    waited_request_ids,
)

logger = logging.getLogger(__name__)

__all__ = [
    "HeartbeatThread",
    "SweepReport",
    "WorkflowRunSupervisor",
    "install_resume_on_decision",
    "sync_connect_args",
    "sync_engine_for",
]

_WORKFLOW_RUN_DIR = re.compile(
    r"wfrun-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)
# ``system_settings`` key: when a process with heartbeats first found runs
# without one (the start of their grace period).
LEGACY_FIRST_SEEN_KEY = "workflow.runs_without_heartbeat_first_seen_at"
# Rows looked at per sweep; the rest wait for the next one.
_SWEEP_BATCH = 500


def install_resume_on_decision(
    service: WorkflowRunService | None = None,
) -> Callable[[], None]:
    """Continue a workflow run when a request it waits on is decided.

    Returns a function that removes the registration.
    """
    from fim_one.core.assembly.confirmation import ConfirmationService

    def continue_run(row: Any) -> None:
        run_id = getattr(row, "workflow_run_id", None)
        if run_id:
            (service or get_run_service()).schedule_resume(str(run_id))

    return ConfirmationService.on_decided(continue_run)


# The heartbeat's connection must never hang: a stamp that cannot be
# written within these is given up, and the next one tried.
HEARTBEAT_CONNECT_TIMEOUT_SECONDS = 5
HEARTBEAT_LOCK_TIMEOUT_MS = 2000
HEARTBEAT_STATEMENT_TIMEOUT_MS = 5000


def sync_connect_args(backend: str) -> dict[str, Any]:
    """Connection settings of the heartbeat thread's own connection."""
    if backend == "sqlite":
        return {"timeout": HEARTBEAT_CONNECT_TIMEOUT_SECONDS}
    if backend == "postgresql":
        return {
            "connect_timeout": HEARTBEAT_CONNECT_TIMEOUT_SECONDS,
            "options": (
                f"-c lock_timeout={HEARTBEAT_LOCK_TIMEOUT_MS} "
                f"-c statement_timeout={HEARTBEAT_STATEMENT_TIMEOUT_MS}"
            ),
        }
    return {}


def sync_engine_for(database_url: str) -> Any:
    """A synchronous engine on the application's database, for the heartbeat thread.

    One connection, used by that thread alone, in autocommit, with short
    connect, lock and statement timeouts (:func:`sync_connect_args`).
    ``None`` for an in-memory SQLite database, which a second connection
    cannot see.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    url = make_url(database_url)
    backend = url.get_backend_name()
    if backend == "sqlite":
        if not url.database or url.database == ":memory:":
            return None
        url = url.set(drivername="sqlite")
    elif backend == "postgresql":
        url = url.set(drivername="postgresql+psycopg2")
    return create_engine(
        url,
        connect_args=sync_connect_args(backend),
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=True,
        isolation_level="AUTOCOMMIT",
    )


class HeartbeatThread(threading.Thread):
    """Stamps this process's running runs from outside the event loop.

    A daemon thread with a synchronous connection of its own. Every
    *interval* seconds it sets ``heartbeat_at`` of the runs the service is
    executing to the database's clock, then looks for those whose record
    is no longer ``running``: someone else closed them. Those are noted on
    the service at once (the engine reads that before its next step) and
    their cancel is handed to the event loop.

    Each run is stamped by a statement of its own, in autocommit, with the
    clock read as the statement runs, and a row someone holds a lock on is
    skipped (whoever holds it is writing the run, heartbeat included): one
    busy row neither delays the others nor gets an old time written.

    A step that blocks the event loop for minutes does not stop this
    thread, so the run it belongs to keeps its heartbeat. A beat that
    fails (connection lost, timeout) is retried with a fresh connection
    after a growing pause; :meth:`stamping` then says no, and the
    supervisor's loop stamps in its place until this thread succeeds again.
    """

    def __init__(
        self,
        engine: Any,
        service: WorkflowRunService,
        loop: asyncio.AbstractEventLoop,
        interval: float,
    ) -> None:
        super().__init__(name="workflow-run-heartbeat", daemon=True)
        self._engine = engine
        self._service = service
        self._loop = loop
        self._interval = interval
        self._halt = threading.Event()
        self._last_success = time.monotonic()
        self.failures = 0

    def stop(self) -> None:
        self._halt.set()

    def stamping(self) -> bool:
        """Whether this thread is doing its job: alive, and its last
        successful beat no more than two intervals ago."""
        return self.is_alive() and time.monotonic() - self._last_success <= 2 * self._interval

    def run(self) -> None:
        pause = self._interval
        while not self._halt.wait(pause):
            try:
                self.beat()
            except Exception:
                self.failures += 1
                if self.failures in (1, 10) or self.failures % 100 == 0:
                    logger.exception(
                        "Workflow run heartbeat failed (%d in a row); the event loop "
                        "stamps meanwhile",
                        self.failures,
                    )
                # A new connection next time, after a pause that grows.
                try:
                    self._engine.dispose()
                except Exception:
                    pass
                pause = min(self._interval * 2 ** min(self.failures, 5), 30.0)
            else:
                self.failures = 0
                self._last_success = time.monotonic()
                pause = self._interval
        self._engine.dispose()

    def beat(self) -> None:
        from sqlalchemy import select, update

        from fim_one.db.models import WorkflowRun

        from .run_service import db_now

        ids = self._service.active_run_ids()
        if not ids:
            return
        with self._engine.connect() as conn:
            now = db_now(conn.dialect.name)
            for run_id in ids:
                free = (
                    select(WorkflowRun.id)
                    .where(WorkflowRun.id == run_id, WorkflowRun.status == "running")
                    .with_for_update(skip_locked=True)
                )
                conn.execute(
                    update(WorkflowRun)
                    .where(WorkflowRun.id.in_(free))
                    .values(heartbeat_at=now)
                    .execution_options(synchronize_session=False)
                )
            closed_elsewhere = list(
                conn.execute(
                    select(WorkflowRun.id).where(
                        WorkflowRun.id.in_(ids), WorkflowRun.status != "running"
                    )
                ).scalars()
            )
        for run_id in closed_elsewhere:
            self._service.note_closed_elsewhere(run_id)
            try:
                self._loop.call_soon_threadsafe(self._service.cancel_run, run_id)
            except RuntimeError:
                return  # the loop is closed: the process is going down


@dataclass
class SweepReport:
    """What one sweep did."""

    closed: list[str] = field(default_factory=list)
    resumed: list[str] = field(default_factory=list)
    removed_sandboxes: list[str] = field(default_factory=list)


class WorkflowRunSupervisor:
    """Heartbeat and recovery sweep of one server process. See the module docstring.

    Parameters
    ----------
    heartbeat_seconds:
        How often the records of this process's runs are stamped.
    stale_seconds:
        A ``running`` run with no heartbeat for this long is taken for
        dead. Must be several heartbeats, so that a busy process is not
        mistaken for a dead one.
    sweep_seconds:
        How often the sweep runs after the one at startup.
    sandbox_grace_seconds:
        A workflow run's directory is removed only once nothing in it has
        changed for this long, so a run that is just starting or just
        ending is not raced.
    legacy_grace_seconds:
        A ``running`` run with no ``worker_id`` was started by a process
        from before heartbeats existed, which may still be executing it
        (a rolling deploy). Such runs are left alone for this long after
        a process of this version first found any (remembered in
        ``system_settings``, so restarts do not start the wait again), then
        judged by their start time.
    sandbox_orphan_seconds:
        A directory with no run record to ask (a single-step test, a
        delegation, an evaluation run) is removed once nothing in it has
        changed for this long.
    """

    def __init__(
        self,
        service: WorkflowRunService | None = None,
        *,
        heartbeat_seconds: float = 10.0,
        stale_seconds: float = 90.0,
        sweep_seconds: float = 30.0,
        sandbox_grace_seconds: float = 600.0,
        sandbox_orphan_seconds: float = 24 * 3600.0,
        legacy_grace_seconds: float = 1800.0,
        sync_engine: Any = None,
    ) -> None:
        self._service = service
        # With one, heartbeats come from a thread (see HeartbeatThread);
        # without, from this supervisor's loop task.
        self._sync_engine = sync_engine
        self._heartbeat: HeartbeatThread | None = None
        # Set by stop(): the loop ends after the pass it is in.
        self._stopping = asyncio.Event()
        self.legacy_grace_seconds = legacy_grace_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.stale_seconds = max(stale_seconds, heartbeat_seconds * 3)
        self.sweep_seconds = sweep_seconds
        self.sandbox_grace_seconds = sandbox_grace_seconds
        self.sandbox_orphan_seconds = sandbox_orphan_seconds

    @classmethod
    def from_env(cls) -> WorkflowRunSupervisor:
        from fim_one.db.engine import _get_database_url

        try:
            sync_engine = sync_engine_for(_get_database_url())
        except Exception:
            logger.exception(
                "No synchronous database connection for the heartbeat thread; "
                "heartbeats will come from the event loop"
            )
            sync_engine = None
        return cls(
            sync_engine=sync_engine,
            heartbeat_seconds=float(os.getenv("WORKFLOW_RUN_HEARTBEAT_SECONDS", "10")),
            stale_seconds=float(os.getenv("WORKFLOW_RUN_STALE_SECONDS", "90")),
            sweep_seconds=float(os.getenv("WORKFLOW_RUN_SWEEP_SECONDS", "30")),
            sandbox_orphan_seconds=float(os.getenv("SANDBOX_ORPHAN_MAX_AGE_HOURS", "24")) * 3600,
            legacy_grace_seconds=float(os.getenv("WORKFLOW_RUN_LEGACY_GRACE_SECONDS", "1800")),
        )

    @property
    def service(self) -> WorkflowRunService:
        return self._service or get_run_service()

    # -- heartbeat ------------------------------------------------------------

    async def beat(self) -> None:
        """Stamp the runs executing here; stop those cancelled from elsewhere."""
        from sqlalchemy import select, update

        from fim_one.db import create_session
        from fim_one.db.models import WorkflowRun

        from .run_service import _dialect_of, db_now

        ids = self.service.active_run_ids()
        if not ids:
            return
        async with create_session() as db:
            await db.execute(
                update(WorkflowRun)
                .where(WorkflowRun.id.in_(ids), WorkflowRun.status == "running")
                .values(heartbeat_at=db_now(_dialect_of(db)))
                .execution_options(synchronize_session=False)
            )
            await db.commit()
            closed_elsewhere = (
                (
                    await db.execute(
                        select(WorkflowRun.id).where(
                            WorkflowRun.id.in_(ids), WorkflowRun.status != "running"
                        )
                    )
                )
                .scalars()
                .all()
            )
        for run_id in closed_elsewhere:
            # Its record already says how it ended; the engine just stops.
            self.service.note_closed_elsewhere(run_id)
            if self.service.cancel_run(run_id):
                logger.info("Workflow run %s was closed from elsewhere; stopping it", run_id)

    # -- sweep ----------------------------------------------------------------

    async def sweep(self) -> SweepReport:
        """One recovery pass. Each part is attempted even if another fails."""
        report = SweepReport()
        for part in (self._close_dead_runs, self._resume_settled_runs, self._remove_sandboxes):
            try:
                await part(report)
            except Exception:
                if cancel_requested():
                    # Not a failure of the part: the sweep was cancelled
                    # (see cancel_requested). The caller decides.
                    raise
                logger.exception("Workflow run sweep: %s failed", part.__name__)
        if report.closed or report.resumed or report.removed_sandboxes:
            logger.info(
                "Workflow run sweep: closed %d dead run(s), continued %d waiting run(s), "
                "removed %d sandbox director(ies)",
                len(report.closed),
                len(report.resumed),
                len(report.removed_sandboxes),
            )
        return report

    async def _close_dead_runs(self, report: SweepReport) -> None:
        from sqlalchemy import func, select

        from fim_one.db import create_session
        from fim_one.db.models import WorkflowRun

        from .run_service import _dialect_of, db_seconds_ago

        mine = set(self.service.active_run_ids())
        async with create_session() as db:
            # The database's clock on both sides: the stamp was taken from
            # it, and so is "how long ago is too long".
            cutoff = db_seconds_ago(_dialect_of(db), self.stale_seconds)
            candidates = (
                (
                    await db.execute(
                        select(WorkflowRun.id, WorkflowRun.worker_id)
                        .where(
                            WorkflowRun.status.in_(("pending", "running")),
                            func.coalesce(
                                WorkflowRun.heartbeat_at,
                                WorkflowRun.started_at,
                                WorkflowRun.created_at,
                            )
                            < cutoff,
                        )
                        .limit(_SWEEP_BATCH)
                    )
                )
                .all()
            )
        # Runs no process has put its name on come from before this
        # release. During a rolling deploy the old process may still be
        # executing them, and it does not stamp: they are given time.
        legacy_due = any(worker_id is None for _, worker_id in candidates) and (
            await self._legacy_grace_over()
        )
        for run_id, worker_id in candidates:
            if run_id in mine:
                continue
            if worker_id is None and not legacy_due:
                continue
            # Conditional on the heartbeat still being old: a process that
            # stamps the run in between keeps it.
            closed = await self.service._end_stored_run(
                run_id,
                ("pending", "running"),
                "failed",
                RUN_INTERRUPTED_ERROR,
                stale_seconds=self.stale_seconds,
                closed_because="stale_heartbeat",
            )
            if closed is not None:
                report.closed.append(run_id)

    async def _legacy_grace_over(self) -> bool:
        """Whether runs from before heartbeats have had their grace.

        Counted from the first time any process of this version looked,
        which is written to ``system_settings`` once: a process restarted
        during the grace period does not start it again.
        """
        from datetime import UTC, datetime

        from sqlalchemy import select
        from sqlalchemy.exc import IntegrityError

        from fim_one.db import create_session
        from fim_one.db.models.system_setting import SystemSetting

        now = datetime.now(UTC)
        async with create_session() as db:
            first_seen = (
                await db.execute(
                    select(SystemSetting.value).where(SystemSetting.key == LEGACY_FIRST_SEEN_KEY)
                )
            ).scalar_one_or_none()
            if first_seen is None:
                db.add(SystemSetting(key=LEGACY_FIRST_SEEN_KEY, value=now.isoformat()))
                try:
                    await db.commit()
                except IntegrityError:
                    await db.rollback()  # another process wrote it this instant
                return self.legacy_grace_seconds <= 0
        try:
            since = datetime.fromisoformat(first_seen)
        except ValueError:
            return True
        return (now - since).total_seconds() >= self.legacy_grace_seconds

    async def _resume_settled_runs(self, report: SweepReport) -> None:
        """Continue every waiting run that has something to go on with.

        All of them, oldest first, however many there are: their ids are
        read in one light query, then taken a batch at a time, each batch
        with one query for its runs' states and one for their requests.
        """
        from sqlalchemy import Text, cast, func, select

        from fim_one.db import create_session
        from fim_one.db.models import WorkflowRun

        async with create_session() as db:
            waiting_ids = list(
                (
                    await db.execute(
                        select(WorkflowRun.id)
                        .where(WorkflowRun.status == "waiting")
                        .order_by(WorkflowRun.created_at, WorkflowRun.id)
                    )
                )
                .scalars()
                .all()
            )
        for start in range(0, len(waiting_ids), _SWEEP_BATCH):
            batch = waiting_ids[start : start + _SWEEP_BATCH]
            async with create_session() as db:
                states = {
                    str(row[0]): (
                        {"waiting": row[1]} if isinstance(row[1], dict) and not row[2] else None
                    )
                    for row in (
                        await db.execute(
                            # Not the sealed state: only what the run waits on.
                            select(
                                WorkflowRun.id,
                                WorkflowRun.waiting_on,
                                # "Has no state", without loading it. Its
                                # length, because an emptied JSON column
                                # holds SQL NULL or JSON null.
                                func.coalesce(
                                    func.length(cast(WorkflowRun.suspend_state, Text)), 0
                                )
                                < 10,
                            ).where(
                                WorkflowRun.id.in_(batch), WorkflowRun.status == "waiting"
                            )
                        )
                    ).all()
                }
            requests = await request_states(
                [rid for state in states.values() for rid in waited_request_ids(state)]
            )
            for run_id in batch:
                if run_id not in states:
                    continue  # no longer waiting
                state = states[run_id]
                # A run with no readable state cannot wait for anything: it
                # is "continued" too, which closes it as failed.
                if isinstance(state, dict) and not settled_given(state, requests):
                    continue
                if await self.service.resume_run(run_id) is not None or not isinstance(
                    state, dict
                ):
                    report.resumed.append(run_id)

    async def _remove_sandboxes(self, report: SweepReport) -> None:
        from sqlalchemy import select

        from fim_one.db import create_session
        from fim_one.db.models import WorkflowRun

        found = await asyncio.to_thread(_run_sandboxes)
        if not found:
            return
        by_run = {
            match.group(1): path
            for path in found
            if (match := _WORKFLOW_RUN_DIR.fullmatch(path.name))
        }
        unfinished: set[str] = set()
        if by_run:
            async with create_session() as db:
                unfinished = set(
                    (
                        await db.execute(
                            select(WorkflowRun.id).where(
                                WorkflowRun.id.in_(list(by_run)),
                                WorkflowRun.status.in_(UNFINISHED_STATUSES),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
        mine = set(self.service.active_run_ids())
        of_runs = set(by_run.values())
        now = time.time()
        for path in found:
            if path in of_runs:
                run_id = next(rid for rid, p in by_run.items() if p == path)
                if run_id in unfinished or run_id in mine:
                    continue
                limit = self.sandbox_grace_seconds
            else:
                limit = self.sandbox_orphan_seconds
            if now - await asyncio.to_thread(_last_change, path) < limit:
                continue
            await asyncio.to_thread(shutil.rmtree, path, True)
            report.removed_sandboxes.append(path.name)

    # -- loop -----------------------------------------------------------------

    def stop(self) -> None:
        """Ask :meth:`run_loop` to end once the pass it is in is over.

        How the application stops it. Cancelling it instead can interrupt
        a database call, and on SQLite (aiosqlite) a cancelled call can
        leave a connection-closing task behind that never ends, so the
        process cannot exit (see ``tests/test_background_loops_stop.py``).
        """
        self._stopping.set()

    async def run_loop(self) -> None:
        """Sweep once at startup, then heartbeat and sweep until stopped or cancelled."""
        logger.info(
            "Workflow run supervisor started (heartbeat=%.0fs, stale=%.0fs, sweep=%.0fs)",
            self.heartbeat_seconds,
            self.stale_seconds,
            self.sweep_seconds,
        )
        try:
            await self.sweep()
        except asyncio.CancelledError:
            raise
        except Exception:
            if cancel_requested():
                logger.info("Workflow run supervisor stopped")
                return
            logger.exception("Workflow run startup sweep failed")
        if self._sync_engine is not None:
            self._heartbeat = HeartbeatThread(
                self._sync_engine,
                self.service,
                asyncio.get_running_loop(),
                self.heartbeat_seconds,
            )
            self._heartbeat.start()
        last_sweep = time.monotonic()
        stopping = asyncio.ensure_future(self._stopping.wait())
        while True:
            try:
                # The interval, or less when asked to stop.
                await asyncio.wait({stopping}, timeout=self.heartbeat_seconds)
                if stopping.done():
                    break
                # The thread stamps; this loop does when there is none, or
                # when the thread has not managed to for two intervals.
                if self._heartbeat is None or not self._heartbeat.stamping():
                    await self.beat()
                if time.monotonic() - last_sweep >= self.sweep_seconds:
                    last_sweep = time.monotonic()
                    await self.sweep()
                if cancel_requested():
                    # A pass that caught the converted cancellation itself.
                    break
            except asyncio.CancelledError:
                break
            except Exception:
                if cancel_requested():
                    # Cancelled during a database call, and the driver's
                    # clean-up turned that into an error: stop all the same.
                    break
                logger.exception("Workflow run supervisor pass failed")
        stopping.cancel()
        if self._heartbeat is not None:
            self._heartbeat.stop()
        logger.info("Workflow run supervisor stopped")


def _run_sandboxes() -> list[Path]:
    base = sandbox_base()
    if not base.is_dir():
        return []
    return [p for p in base.iterdir() if p.is_dir() and is_run_sandbox(p.name)]


def _last_change(path: Path) -> float:
    """When *path* or anything directly in it last changed (epoch seconds)."""
    latest = 0.0
    try:
        latest = path.stat().st_mtime
        for child in path.iterdir():
            latest = max(latest, child.stat().st_mtime)
    except OSError:
        pass
    return latest

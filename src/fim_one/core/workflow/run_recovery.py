"""Keeping workflow runs alive across decisions, restarts and dead processes.

Three jobs, all on top of :class:`~.run_service.WorkflowRunService`:

- **Continue on decision.** :func:`install_resume_on_decision` registers
  with ``ConfirmationService.on_decided``: when a request that belongs to a
  workflow run is decided, the run is continued in a background task of the
  deciding process.
- **Heartbeat.** Every run executing in this process has its record
  stamped every ``heartbeat_seconds``. A record marked cancelled by another
  process is noticed on the same pass and the run is stopped here.
- **Sweep**, at startup and every ``sweep_seconds``:

  * a ``waiting`` run whose request is decided, expired or gone is
    continued (the deciding process died before it could, a request ran
    out of time, the service was down when the decision came in);
  * a ``running`` run whose heartbeat is older than ``stale_seconds`` was
    being executed by a process that no longer exists: it is closed as
    failed, its open requests are closed and its sandbox removed. That
    also frees the trigger key's single-flight, which looks at unfinished
    runs;
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
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fim_one.core.assembly.sandbox import is_run_sandbox, sandbox_base

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

__all__ = ["SweepReport", "WorkflowRunSupervisor", "install_resume_on_decision"]

_WORKFLOW_RUN_DIR = re.compile(
    r"wfrun-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)
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
    ) -> None:
        self._service = service
        self.heartbeat_seconds = heartbeat_seconds
        self.stale_seconds = max(stale_seconds, heartbeat_seconds * 3)
        self.sweep_seconds = sweep_seconds
        self.sandbox_grace_seconds = sandbox_grace_seconds
        self.sandbox_orphan_seconds = sandbox_orphan_seconds

    @classmethod
    def from_env(cls) -> WorkflowRunSupervisor:
        return cls(
            heartbeat_seconds=float(os.getenv("WORKFLOW_RUN_HEARTBEAT_SECONDS", "10")),
            stale_seconds=float(os.getenv("WORKFLOW_RUN_STALE_SECONDS", "90")),
            sweep_seconds=float(os.getenv("WORKFLOW_RUN_SWEEP_SECONDS", "30")),
            sandbox_orphan_seconds=float(os.getenv("SANDBOX_ORPHAN_MAX_AGE_HOURS", "24")) * 3600,
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

        ids = self.service.active_run_ids()
        if not ids:
            return
        async with create_session() as db:
            await db.execute(
                update(WorkflowRun)
                .where(WorkflowRun.id.in_(ids), WorkflowRun.status == "running")
                .values(heartbeat_at=datetime.now(UTC))
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

        cutoff = datetime.now(UTC) - timedelta(seconds=self.stale_seconds)
        mine = set(self.service.active_run_ids())
        async with create_session() as db:
            candidates = (
                (
                    await db.execute(
                        select(WorkflowRun.id)
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
                .scalars()
                .all()
            )
        for run_id in candidates:
            if run_id in mine:
                continue
            # Conditional on the heartbeat still being old: a process that
            # stamps the run in between keeps it.
            closed = await self.service._end_stored_run(
                run_id,
                ("pending", "running"),
                "failed",
                RUN_INTERRUPTED_ERROR,
                stale_before=cutoff,
            )
            if closed is not None:
                report.closed.append(run_id)

    async def _resume_settled_runs(self, report: SweepReport) -> None:
        """Continue every waiting run that has something to go on with.

        All of them, oldest first, however many there are: their ids are
        read in one light query, then taken a batch at a time, each batch
        with one query for its runs' states and one for their requests.
        """
        from sqlalchemy import select

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
                    str(row[0]): row[1]
                    for row in (
                        await db.execute(
                            select(WorkflowRun.id, WorkflowRun.suspend_state).where(
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

    async def run_loop(self) -> None:
        """Sweep once at startup, then heartbeat and sweep until cancelled."""
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
        except Exception:  # pragma: no cover - sweep() already catches
            logger.exception("Workflow run startup sweep failed")
        last_sweep = time.monotonic()
        while True:
            try:
                await asyncio.sleep(self.heartbeat_seconds)
                await self.beat()
                if time.monotonic() - last_sweep >= self.sweep_seconds:
                    last_sweep = time.monotonic()
                    await self.sweep()
            except asyncio.CancelledError:
                logger.info("Workflow run supervisor stopped")
                break
            except Exception:
                logger.exception("Workflow run supervisor pass failed")


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

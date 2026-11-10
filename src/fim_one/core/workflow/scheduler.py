"""Workflow scheduler daemon — triggers cron-based workflow runs.

This module implements a background service that periodically checks for
workflows with enabled cron schedules, evaluates whether a run is due,
and fires workflow executions as asyncio tasks.

Usage::

    from fim_one.core.workflow.scheduler import WorkflowScheduler

    scheduler = WorkflowScheduler()
    task = asyncio.create_task(scheduler.run())
    # ... later ...
    await scheduler.stop()
"""

from __future__ import annotations

import asyncio
import logging
import time
import zoneinfo
from datetime import UTC, datetime
from typing import Any

from croniter import croniter  # type: ignore[import-untyped]

logger = logging.getLogger("fim_one.scheduler")

# Default interval between scheduler ticks (seconds)
_DEFAULT_POLL_INTERVAL = 60

# Maximum concurrent scheduled workflow executions
_DEFAULT_MAX_CONCURRENT_RUNS = 5


class WorkflowScheduler:
    """Async background service that evaluates cron schedules and triggers runs.

    Parameters
    ----------
    poll_interval:
        Seconds between each scan for due workflows.  Defaults to 60.
    max_concurrent_runs:
        Maximum number of scheduled runs that may execute simultaneously.
        Additional due workflows are deferred until a slot opens.
    """

    def __init__(
        self,
        poll_interval: int = _DEFAULT_POLL_INTERVAL,
        max_concurrent_runs: int = _DEFAULT_MAX_CONCURRENT_RUNS,
    ) -> None:
        self._poll_interval = poll_interval
        self._semaphore = asyncio.Semaphore(max_concurrent_runs)
        self._stop_event = asyncio.Event()
        self._running_tasks: set[asyncio.Task[None]] = set()

    async def run(self) -> None:
        """Main scheduler loop.  Runs until ``stop()`` is called or cancelled."""
        logger.info(
            "Workflow scheduler started (poll=%ds, max_concurrent=%d)",
            self._poll_interval,
            self._semaphore._value,
        )
        try:
            while not self._stop_event.is_set():
                tick_start = time.monotonic()
                try:
                    await self._tick()
                except Exception:
                    logger.exception("Scheduler tick failed — will retry next cycle")

                # Sleep for the remaining interval (subtract tick duration)
                elapsed = time.monotonic() - tick_start
                sleep_for = max(self._poll_interval - elapsed, 1.0)
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=sleep_for
                    )
                    # If we get here, stop was requested
                    break
                except asyncio.TimeoutError:
                    # Normal — just means the sleep period elapsed
                    pass
        except asyncio.CancelledError:
            logger.info("Workflow scheduler cancelled")
        finally:
            await self._cleanup()
            logger.info("Workflow scheduler stopped")

    async def stop(self) -> None:
        """Signal the scheduler to stop gracefully."""
        logger.info("Workflow scheduler stop requested")
        self._stop_event.set()

    async def _cleanup(self) -> None:
        """Wait for in-flight scheduled runs to finish (with timeout)."""
        if self._running_tasks:
            logger.info(
                "Waiting for %d in-flight scheduled runs to complete...",
                len(self._running_tasks),
            )
            # Give runs up to 30 seconds to finish gracefully
            done, pending = await asyncio.wait(
                self._running_tasks, timeout=30.0
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.wait(pending, timeout=5.0)
            logger.info("All in-flight scheduled runs settled")

    async def _tick(self) -> None:
        """Single scheduler cycle: query due workflows and dispatch runs."""
        from sqlalchemy import select

        from fim_one.db import create_session
        from fim_one.db.models import Workflow

        # The Workflows module is soft-shelvable (off by default). While it
        # is disabled, cron-triggered runs do not fire — the scheduler tick
        # is a no-op, matching the router-level gate on manual/API triggers.
        from fim_one.web.services.feature_flags import (
            SETTING_FEATURE_WORKFLOWS,
            is_feature_enabled,
        )

        async with create_session() as db:
            if not await is_feature_enabled(db, SETTING_FEATURE_WORKFLOWS):
                return
            result = await db.execute(
                select(Workflow).where(
                    Workflow.schedule_enabled == True,  # noqa: E712
                    Workflow.schedule_cron.isnot(None),
                    Workflow.is_active == True,  # noqa: E712
                )
            )
            workflows = result.scalars().all()

        if not workflows:
            return

        logger.debug("Scheduler tick: found %d scheduled workflows", len(workflows))

        now_utc = datetime.now(UTC)

        for wf in workflows:
            try:
                if self._is_due(wf, now_utc):
                    await self._dispatch_run(wf)
            except Exception:
                logger.exception(
                    "Error evaluating schedule for workflow %s (%s)",
                    wf.id,
                    wf.name,
                )

    def _is_due(self, wf: Any, now_utc: datetime) -> bool:
        """Check whether a workflow's cron expression indicates a run is due.

        Compares the last scheduled execution time (``wf.last_scheduled_at``)
        with the most recent cron fire time.  If the cron should have fired
        since the last run (or if there has never been a scheduled run),
        returns ``True``.
        """
        cron_expr: str = wf.schedule_cron
        tz_name: str = wf.schedule_timezone or "UTC"

        try:
            tz = zoneinfo.ZoneInfo(tz_name)
        except Exception:
            tz = zoneinfo.ZoneInfo("UTC")

        now_local = now_utc.astimezone(tz)

        try:
            cron = croniter(cron_expr, now_local)
        except (ValueError, KeyError):
            logger.warning(
                "Invalid cron expression '%s' for workflow %s — skipping",
                cron_expr,
                wf.id,
            )
            return False

        # croniter.get_prev() returns the most recent time the cron *should*
        # have fired (relative to ``now_local``).
        prev_fire: datetime = cron.get_prev(datetime)

        # Determine the last time the scheduler actually triggered this workflow
        last_scheduled: datetime | None = getattr(wf, "last_scheduled_at", None)

        if last_scheduled is None:
            # Never been triggered by the scheduler before.
            # Only trigger if the previous fire time is within the last poll
            # interval — avoids a flood of back-fills on first startup.
            delta = (now_local - prev_fire).total_seconds()
            return delta <= self._poll_interval * 2
        else:
            # Ensure timezone-aware comparison
            if last_scheduled.tzinfo is None:
                last_scheduled = last_scheduled.replace(tzinfo=UTC)

            # The cron fired since the last scheduled run
            return prev_fire > last_scheduled.astimezone(tz)

    async def _dispatch_run(self, wf: Any) -> None:
        """Create a WorkflowRun record and launch execution in the background."""
        # Skip if all concurrent slots are occupied — avoids updating
        # last_scheduled_at when we cannot actually run the workflow.
        if self._semaphore.locked():
            logger.info(
                "Scheduler: all concurrent slots busy, skipping '%s' (id=%s)",
                wf.name,
                wf.id,
            )
            return

        # One scheduled run at a time: while the previous one is still
        # running, or waiting for a decision, this tick is skipped (and
        # counted as taken, so it is not made up for later). The schedule
        # endpoint shows which run holds it (``unfinished_scheduled_run``).
        held_by = await unfinished_scheduled_run(wf.id)
        if held_by is not None:
            logger.info(
                "Scheduler: skipping '%s' (id=%s): its scheduled run %s is still %s",
                wf.name,
                wf.id,
                held_by[0],
                held_by[1],
            )
            await self._mark_scheduled(wf.id)
            return

        logger.info(
            "Scheduler triggering workflow '%s' (id=%s, cron=%s, tz=%s)",
            wf.name,
            wf.id,
            wf.schedule_cron,
            wf.schedule_timezone,
        )

        # Update last_scheduled_at immediately to prevent duplicate triggers
        await self._mark_scheduled(wf.id)

        # Launch execution as a background task
        task = asyncio.create_task(self._execute_workflow(wf))
        self._running_tasks.add(task)
        task.add_done_callback(self._running_tasks.discard)

    @staticmethod
    async def _mark_scheduled(workflow_id: str) -> None:
        """Record that the schedule's current tick has been dealt with."""
        from sqlalchemy import select

        from fim_one.db import create_session
        from fim_one.db.models import Workflow

        async with create_session() as db:
            result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
            db_wf = result.scalar_one_or_none()
            if db_wf is not None:
                db_wf.last_scheduled_at = datetime.now(UTC)
                await db.commit()

    async def _execute_workflow(self, wf: Any) -> None:
        """Execute one scheduled run through the run service.

        The run executes as the workflow's author and passes the same gates
        as every other entry point. A refused run is recorded as failed with
        the refusal code, so the author can see why the schedule did not run.
        """
        from .run_service import RunMode, RunRefusedError, get_run_service

        async with self._semaphore:
            try:
                outcome = await get_run_service().run_to_completion(
                    wf,
                    caller_id=None,
                    inputs=wf.schedule_inputs,
                    mode=RunMode.SCHEDULE,
                    # The scheduler bounds concurrency with its own semaphore.
                    acquire_slot=False,
                    record_refusal=True,
                )
            except RunRefusedError as exc:
                logger.warning(
                    "Scheduled workflow %s refused: %s", wf.id, exc.error_code
                )
                return
            except Exception:
                logger.exception("Scheduled workflow %s could not start", wf.id)
                return

            logger.info(
                "Scheduled run %s for workflow %s finished: %s (%dms)",
                outcome.run_id,
                wf.id,
                outcome.status,
                outcome.duration_ms,
            )


async def unfinished_scheduled_run(
    workflow_id: str, db: Any = None
) -> tuple[str, str, datetime | None] | None:
    """The scheduled run of the workflow that has not ended, if any.

    ``(run id, status, started at)`` of the oldest one. While there is one
    the schedule skips its ticks: runs of a schedule do not pile up behind
    one that is slow or waits for a decision. A run left behind by a dead
    process stops counting once the recovery sweep has closed it.
    """
    from sqlalchemy import select

    from fim_one.db import create_session
    from fim_one.db.models import WorkflowRun

    from .run_service import UNFINISHED_STATUSES

    query = (
        select(WorkflowRun.id, WorkflowRun.status, WorkflowRun.started_at)
        .where(
            WorkflowRun.workflow_id == workflow_id,
            WorkflowRun.mode == "schedule",
            WorkflowRun.status.in_(UNFINISHED_STATUSES),
        )
        .order_by(WorkflowRun.created_at)
        .limit(1)
    )
    if db is not None:
        row = (await db.execute(query)).first()
    else:
        async with create_session() as own:
            row = (await own.execute(query)).first()
    return (str(row[0]), str(row[1]), row[2]) if row is not None else None

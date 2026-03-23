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
import uuid
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
        self._webhook_tasks: set[asyncio.Task[None]] = set()

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
        from fim_one.db import create_session
        from fim_one.web.models import Workflow

        from sqlalchemy import select

        async with create_session() as db:
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

        logger.info(
            "Scheduler triggering workflow '%s' (id=%s, cron=%s, tz=%s)",
            wf.name,
            wf.id,
            wf.schedule_cron,
            wf.schedule_timezone,
        )

        # Update last_scheduled_at immediately to prevent duplicate triggers
        from fim_one.db import create_session
        from fim_one.web.models import Workflow

        from sqlalchemy import select

        now = datetime.now(UTC)
        async with create_session() as db:
            result = await db.execute(
                select(Workflow).where(Workflow.id == wf.id)
            )
            db_wf = result.scalar_one_or_none()
            if db_wf is not None:
                db_wf.last_scheduled_at = now
                await db.commit()

        # Launch execution as a background task
        task = asyncio.create_task(
            self._execute_workflow(
                workflow_id=wf.id,
                user_id=wf.user_id,
                blueprint=wf.blueprint,
                inputs=wf.schedule_inputs,
                env_vars_blob=wf.env_vars_blob,
                webhook_url=getattr(wf, "webhook_url", None),
            )
        )
        self._running_tasks.add(task)
        task.add_done_callback(self._running_tasks.discard)

    async def _execute_workflow(
        self,
        *,
        workflow_id: str,
        user_id: str,
        blueprint: dict[str, Any],
        inputs: dict[str, Any] | None,
        env_vars_blob: str | None,
        webhook_url: str | None,
    ) -> None:
        """Execute a single scheduled workflow run, guarded by the semaphore."""
        async with self._semaphore:
            run_id = str(uuid.uuid4())
            start_time = time.time()
            final_status = "completed"
            outputs: dict[str, Any] = {}
            node_results: dict[str, Any] = {}
            error_msg: str | None = None

            # Create the run record
            from fim_one.db import create_session
            from fim_one.web.models import WorkflowRun

            try:
                async with create_session() as db:
                    run = WorkflowRun(
                        id=run_id,
                        workflow_id=workflow_id,
                        user_id=user_id,
                        blueprint_snapshot=blueprint,
                        inputs=inputs,
                        status="running",
                        started_at=datetime.now(UTC),
                    )
                    db.add(run)
                    await db.commit()
            except Exception:
                logger.exception(
                    "Failed to create run record for scheduled workflow %s",
                    workflow_id,
                )
                return

            # Decrypt env vars
            env_vars: dict[str, str] = {}
            if env_vars_blob:
                try:
                    from fim_one.core.security.encryption import decrypt_credential

                    env_vars = decrypt_credential(env_vars_blob)
                except Exception:
                    logger.warning(
                        "Failed to decrypt env vars for scheduled workflow %s",
                        workflow_id,
                    )

            # Parse and execute
            try:
                from fim_one.core.workflow.engine import WorkflowEngine
                from fim_one.core.workflow.parser import parse_blueprint

                parsed = parse_blueprint(blueprint)
                engine = WorkflowEngine(
                    max_concurrency=5,
                    env_vars=env_vars,
                    run_id=run_id,
                    user_id=user_id,
                    workflow_id=workflow_id,
                )

                async for event_name, event_data in engine.execute_streaming(
                    parsed, inputs
                ):
                    if event_name in (
                        "node_started",
                        "node_completed",
                        "node_failed",
                        "node_skipped",
                    ):
                        nid = event_data.get("node_id", "")
                        node_results[nid] = {
                            **(node_results.get(nid) or {}),
                            **event_data,
                        }
                    elif event_name == "run_completed":
                        outputs = event_data.get("outputs", {})
                        final_status = event_data.get("status", "completed")
                    elif event_name == "run_failed":
                        final_status = "failed"
                        error_msg = event_data.get("error")

            except Exception as exc:
                final_status = "failed"
                error_msg = f"{type(exc).__name__}: {exc}"
                logger.exception(
                    "Scheduled workflow execution failed for run %s (workflow %s)",
                    run_id,
                    workflow_id,
                )

            elapsed_ms = int((time.time() - start_time) * 1000)

            # Persist run results
            try:
                from sqlalchemy import select as sa_select

                async with create_session() as persist_db:
                    result = await persist_db.execute(
                        sa_select(WorkflowRun).where(WorkflowRun.id == run_id)
                    )
                    db_run = result.scalar_one_or_none()
                    if db_run:
                        db_run.status = final_status
                        db_run.outputs = outputs or None
                        db_run.node_results = node_results or None
                        db_run.started_at = datetime.fromtimestamp(
                            start_time, tz=UTC
                        )
                        db_run.completed_at = datetime.now(UTC)
                        db_run.duration_ms = elapsed_ms
                        db_run.error = error_msg
                        await persist_db.commit()
            except Exception:
                logger.exception(
                    "Failed to persist scheduled run %s", run_id
                )

            # Fire webhook if configured
            if webhook_url and final_status in ("completed", "failed"):
                wh_task = asyncio.create_task(
                    self._deliver_webhook(
                        webhook_url,
                        {
                            "event": (
                                "run_completed"
                                if final_status == "completed"
                                else "run_failed"
                            ),
                            "workflow_id": workflow_id,
                            "run_id": run_id,
                            "status": final_status,
                            "outputs": outputs or None,
                            "error": error_msg,
                            "duration_ms": elapsed_ms,
                            "completed_at": datetime.now(UTC).isoformat(),
                            "trigger": "scheduled",
                        },
                    )
                )
                self._webhook_tasks.add(wh_task)
                wh_task.add_done_callback(self._webhook_tasks.discard)

            logger.info(
                "Scheduled run %s for workflow %s finished: %s (%dms)",
                run_id,
                workflow_id,
                final_status,
                elapsed_ms,
            )

    @staticmethod
    async def _deliver_webhook(
        webhook_url: str, payload: dict[str, Any]
    ) -> None:
        """Fire-and-forget POST to a workflow webhook URL."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    webhook_url,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-Webhook-Source": "fim-one",
                    },
                )
                logger.info(
                    "Scheduler webhook delivered to %s — status %d",
                    webhook_url,
                    resp.status_code,
                )
        except Exception:
            logger.exception(
                "Scheduler webhook delivery failed for %s", webhook_url
            )

"""Workflow run retention/cleanup system.

Periodically deletes old WorkflowRun records based on configurable
retention policies (max age and max runs per workflow). Skips runs
that are still in-progress (status ``pending`` or ``running``).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.web.models.workflow import Workflow, WorkflowRun

logger = logging.getLogger(__name__)

# Statuses considered "active" — never cleaned up even if old.
_ACTIVE_STATUSES = ("pending", "running")


class WorkflowRunCleaner:
    """Configurable cleanup of old workflow run records.

    Parameters
    ----------
    max_age_days:
        Delete completed/failed runs older than this many days.
        Per-workflow ``run_retention_days`` overrides this global default.
    max_runs_per_workflow:
        Keep at most this many runs per workflow (newest first).
    cleanup_interval_hours:
        How often the background loop runs (in hours).
    """

    def __init__(
        self,
        *,
        max_age_days: int = 30,
        max_runs_per_workflow: int = 100,
        cleanup_interval_hours: int = 24,
    ) -> None:
        self.max_age_days = max_age_days
        self.max_runs_per_workflow = max_runs_per_workflow
        self.cleanup_interval_hours = cleanup_interval_hours

    async def cleanup(
        self,
        db: AsyncSession,
        *,
        max_age_days: int | None = None,
        max_runs_per_workflow: int | None = None,
    ) -> int:
        """Run one cleanup pass and return the total number of deleted runs.

        Parameters
        ----------
        db:
            An async SQLAlchemy session (caller manages commit/rollback).
        max_age_days:
            Override the instance default for this invocation.
        max_runs_per_workflow:
            Override the instance default for this invocation.
        """
        age_days = max_age_days if max_age_days is not None else self.max_age_days
        max_runs = (
            max_runs_per_workflow
            if max_runs_per_workflow is not None
            else self.max_runs_per_workflow
        )

        deleted_by_age = await self._delete_old_runs(db, age_days)
        deleted_by_count = await self._delete_excess_runs(db, max_runs)

        total = deleted_by_age + deleted_by_count
        if total > 0:
            logger.info(
                "Workflow run cleanup: deleted %d runs (age=%d, excess=%d)",
                total,
                deleted_by_age,
                deleted_by_count,
            )
        else:
            logger.debug("Workflow run cleanup: no runs to delete")

        return total

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _delete_old_runs(self, db: AsyncSession, global_age_days: int) -> int:
        """Delete runs older than the retention period.

        Uses per-workflow ``run_retention_days`` when set, otherwise falls
        back to *global_age_days*.
        """
        deleted = 0

        # 1. Workflows with custom retention
        result = await db.execute(
            select(Workflow.id, Workflow.run_retention_days).where(
                Workflow.run_retention_days.isnot(None)
            )
        )
        custom_workflows = result.all()

        for wf_id, retention_days in custom_workflows:
            cutoff = datetime.now(UTC) - timedelta(days=retention_days)
            stmt = (
                delete(WorkflowRun)
                .where(
                    WorkflowRun.workflow_id == wf_id,
                    WorkflowRun.created_at < cutoff,
                    WorkflowRun.status.notin_(_ACTIVE_STATUSES),
                )
            )
            res = cast(CursorResult[Any], await db.execute(stmt))
            deleted += res.rowcount

        # 2. All other workflows: use global default
        custom_ids = [wf_id for wf_id, _ in custom_workflows]
        global_cutoff = datetime.now(UTC) - timedelta(days=global_age_days)

        global_stmt = delete(WorkflowRun).where(
            WorkflowRun.created_at < global_cutoff,
            WorkflowRun.status.notin_(_ACTIVE_STATUSES),
        )
        if custom_ids:
            global_stmt = global_stmt.where(
                WorkflowRun.workflow_id.notin_(custom_ids)
            )
        res = cast(CursorResult[Any], await db.execute(global_stmt))
        deleted += res.rowcount

        await db.commit()
        return deleted

    async def _delete_excess_runs(
        self, db: AsyncSession, max_runs: int
    ) -> int:
        """For each workflow, keep at most *max_runs* (newest first)."""
        deleted = 0

        # Find workflows that exceed the limit
        count_q = (
            select(
                WorkflowRun.workflow_id,
                func.count().label("cnt"),
            )
            .where(WorkflowRun.status.notin_(_ACTIVE_STATUSES))
            .group_by(WorkflowRun.workflow_id)
            .having(func.count() > max_runs)
        )
        result = await db.execute(count_q)
        over_limit = result.all()

        for wf_id, _cnt in over_limit:
            # Find IDs of runs to keep (newest N)
            keep_ids_q = (
                select(WorkflowRun.id)
                .where(
                    WorkflowRun.workflow_id == wf_id,
                    WorkflowRun.status.notin_(_ACTIVE_STATUSES),
                )
                .order_by(WorkflowRun.created_at.desc())
                .limit(max_runs)
            )
            keep_result = await db.execute(keep_ids_q)
            keep_ids = {row[0] for row in keep_result.all()}

            if not keep_ids:
                continue

            # Delete all non-active runs for this workflow NOT in the keep set
            del_q = (
                select(WorkflowRun.id)
                .where(
                    WorkflowRun.workflow_id == wf_id,
                    WorkflowRun.status.notin_(_ACTIVE_STATUSES),
                    WorkflowRun.id.notin_(keep_ids),
                )
            )
            del_result = await db.execute(del_q)
            ids_to_delete = [row[0] for row in del_result.all()]

            if ids_to_delete:
                stmt = delete(WorkflowRun).where(WorkflowRun.id.in_(ids_to_delete))
                del_res = cast(CursorResult[Any], await db.execute(stmt))
                deleted += del_res.rowcount

        await db.commit()
        return deleted

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    async def run_loop(self) -> None:
        """Run cleanup periodically until the task is cancelled.

        Intended to be launched via ``asyncio.create_task`` in the
        FastAPI lifespan.
        """
        from fim_one.db import create_session

        logger.info(
            "Workflow run cleaner started (interval=%dh, max_age=%dd, max_runs=%d)",
            self.cleanup_interval_hours,
            self.max_age_days,
            self.max_runs_per_workflow,
        )

        while True:
            try:
                await asyncio.sleep(self.cleanup_interval_hours * 3600)
                async with create_session() as db:
                    await self.cleanup(db)
            except asyncio.CancelledError:
                logger.info("Workflow run cleaner stopped")
                break
            except Exception:
                logger.exception("Workflow run cleanup failed")

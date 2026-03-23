"""Admin endpoints for workflow scheduled jobs management."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.auth import get_current_admin
from fim_one.web.exceptions import AppError
from fim_one.web.models import User, Workflow, WorkflowRun
from fim_one.web.schemas.common import PaginatedResponse
from fim_one.web.schemas.workflow import _compute_next_run

from fim_one.web.api.admin_utils import write_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AdminScheduleInfo(BaseModel):
    workflow_id: str
    workflow_name: str
    owner_id: str
    owner_username: str | None = None
    owner_email: str | None = None
    schedule_cron: str | None = None
    schedule_timezone: str = "UTC"
    schedule_enabled: bool = False
    next_run_at: str | None = None
    last_scheduled_at: str | None = None
    created_at: str


class ScheduleToggleRequest(BaseModel):
    schedule_enabled: bool


class ScheduleStatsResponse(BaseModel):
    total_active_schedules: int
    total_schedules: int
    upcoming_runs: list[UpcomingRun] = Field(default_factory=list)
    failed_runs_24h: int


class UpcomingRun(BaseModel):
    workflow_id: str
    workflow_name: str
    next_run_at: str | None = None
    schedule_cron: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/schedules", response_model=PaginatedResponse)
async def list_all_schedules(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    active_only: bool = Query(False),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    """List all workflow scheduled triggers across users. Requires admin privileges."""
    # Only fetch workflows that have a schedule_cron set
    stmt = (
        select(Workflow, User)
        .join(User, Workflow.user_id == User.id)
        .where(Workflow.schedule_cron.isnot(None))
    )
    count_base = select(Workflow).where(Workflow.schedule_cron.isnot(None))

    if active_only:
        stmt = stmt.where(Workflow.schedule_enabled == True)  # noqa: E712
        count_base = count_base.where(Workflow.schedule_enabled == True)  # noqa: E712

    count_stmt = select(func.count()).select_from(count_base.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    rows = (
        await db.execute(
            stmt.order_by(Workflow.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()

    items = []
    for workflow, user in rows:
        next_run = None
        if workflow.schedule_enabled and workflow.schedule_cron:
            next_run = _compute_next_run(
                workflow.schedule_cron,
                workflow.schedule_timezone or "UTC",
            )

        items.append(
            AdminScheduleInfo(
                workflow_id=workflow.id,
                workflow_name=workflow.name,
                owner_id=workflow.user_id,
                owner_username=user.username,
                owner_email=user.email,
                schedule_cron=workflow.schedule_cron,
                schedule_timezone=workflow.schedule_timezone or "UTC",
                schedule_enabled=workflow.schedule_enabled,
                next_run_at=next_run,
                last_scheduled_at=(
                    workflow.last_scheduled_at.isoformat()
                    if workflow.last_scheduled_at
                    else None
                ),
                created_at=workflow.created_at.isoformat() if workflow.created_at else "",
            ).model_dump()
        )

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total > 0 else 1,
    )


@router.patch("/schedules/{workflow_id}/active")
async def toggle_schedule_active(
    workflow_id: str,
    body: ScheduleToggleRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, object]:
    """Pause/resume a workflow schedule. Requires admin privileges."""
    result = await db.execute(
        select(Workflow).where(Workflow.id == workflow_id)
    )
    workflow = result.scalar_one_or_none()
    if workflow is None:
        raise AppError("workflow_not_found", status_code=404)

    if not workflow.schedule_cron:
        raise AppError(
            "no_schedule_configured",
            status_code=400,
            detail="This workflow has no schedule configured",
        )

    workflow.schedule_enabled = body.schedule_enabled
    await db.commit()

    action = "schedule.admin_resume" if body.schedule_enabled else "schedule.admin_pause"
    await write_audit(
        db,
        current_user,
        action,
        target_type="workflow",
        target_id=workflow_id,
        target_label=workflow.name,
        detail=f"schedule_enabled={body.schedule_enabled}",
    )

    return {"ok": True, "schedule_enabled": workflow.schedule_enabled}


@router.get("/schedules/stats", response_model=ScheduleStatsResponse)
async def schedule_stats(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ScheduleStatsResponse:
    """Schedule stats. Requires admin privileges."""
    # Total schedules (workflows with cron set)
    total_schedules = (
        await db.execute(
            select(func.count())
            .select_from(Workflow)
            .where(Workflow.schedule_cron.isnot(None))
        )
    ).scalar_one()

    # Active schedules
    total_active = (
        await db.execute(
            select(func.count())
            .select_from(Workflow)
            .where(
                Workflow.schedule_cron.isnot(None),
                Workflow.schedule_enabled == True,  # noqa: E712
            )
        )
    ).scalar_one()

    # Next 10 upcoming runs
    active_workflows_result = await db.execute(
        select(Workflow)
        .where(
            Workflow.schedule_cron.isnot(None),
            Workflow.schedule_enabled == True,  # noqa: E712
        )
        .limit(50)  # Fetch more, then sort by computed next_run
    )
    active_workflows = active_workflows_result.scalars().all()

    upcoming: list[UpcomingRun] = []
    for wf in active_workflows:
        next_run = _compute_next_run(
            wf.schedule_cron or "",
            wf.schedule_timezone or "UTC",
        )
        if next_run:
            upcoming.append(
                UpcomingRun(
                    workflow_id=wf.id,
                    workflow_name=wf.name,
                    next_run_at=next_run,
                    schedule_cron=wf.schedule_cron,
                )
            )

    # Sort by next_run_at ascending and take top 10
    upcoming.sort(key=lambda x: x.next_run_at or "")
    upcoming = upcoming[:10]

    # Failed scheduled runs in last 24h
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    failed_24h = (
        await db.execute(
            select(func.count())
            .select_from(WorkflowRun)
            .where(
                WorkflowRun.status == "failed",
                WorkflowRun.created_at >= cutoff,
            )
        )
    ).scalar_one()

    return ScheduleStatsResponse(
        total_active_schedules=total_active,
        total_schedules=total_schedules,
        upcoming_runs=upcoming,
        failed_runs_24h=failed_24h,
    )

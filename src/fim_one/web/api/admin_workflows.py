"""Admin endpoints for workflow management across all users."""

from __future__ import annotations

import logging
import math
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.auth import get_current_admin
from fim_one.web.exceptions import AppError
from fim_one.web.models import User, Workflow, WorkflowRun
from fim_one.web.schemas.common import ApiResponse, PaginatedResponse
from fim_one.web.schemas.workflow import (
    BatchOperationResponse,
    BatchWorkflowDeleteRequest,
    BatchWorkflowPublishRequest,
    BatchWorkflowToggleRequest,
)

from fim_one.web.api.admin_utils import write_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AdminWorkflowInfo(BaseModel):
    id: str
    name: str
    icon: str | None = None
    description: str | None = None
    status: str = "draft"
    is_active: bool = True
    node_count: int = 0
    total_runs: int = 0
    success_rate: float | None = None
    last_run_at: str | None = None
    user_id: str
    username: str | None = None
    email: str | None = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_nodes(blueprint: Any) -> int:
    """Count nodes in a workflow blueprint JSON."""
    if not blueprint or not isinstance(blueprint, dict):
        return 0
    nodes = blueprint.get("nodes")
    if isinstance(nodes, list):
        return len(nodes)
    return 0


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/workflows", response_model=PaginatedResponse)
async def list_all_workflows(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    status: str | None = Query(None),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    """List all workflows across all users. Requires admin privileges."""

    # --- Run stats subquery ---
    run_stats = (
        select(
            WorkflowRun.workflow_id,
            func.count(WorkflowRun.id).label("total_runs"),
            func.sum(
                case((WorkflowRun.status == "completed", 1), else_=0)
            ).label("success_count"),
            func.max(WorkflowRun.created_at).label("last_run_at"),
        )
        .group_by(WorkflowRun.workflow_id)
        .subquery()
    )

    # --- Main query ---
    stmt = (
        select(Workflow, User, run_stats)
        .join(User, Workflow.user_id == User.id)
        .outerjoin(run_stats, Workflow.id == run_stats.c.workflow_id)
    )
    count_base = select(Workflow)

    if search:
        pattern = f"%{search}%"
        filter_clause = Workflow.name.ilike(pattern)
        stmt = stmt.where(filter_clause)
        count_base = count_base.where(filter_clause)

    if status:
        status_clause = Workflow.status == status
        stmt = stmt.where(status_clause)
        count_base = count_base.where(status_clause)

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
    for row in rows:
        workflow = row[0]
        user = row[1]
        total_runs = int(getattr(row, "total_runs", None) or 0)
        success_count = int(getattr(row, "success_count", None) or 0)
        last_run_at = getattr(row, "last_run_at", None)

        success_rate = None
        if total_runs > 0:
            success_rate = round((success_count / total_runs) * 100, 1)

        items.append(
            AdminWorkflowInfo(
                id=workflow.id,
                name=workflow.name,
                icon=workflow.icon,
                description=workflow.description,
                status=workflow.status,
                is_active=workflow.is_active,
                node_count=_count_nodes(workflow.blueprint),
                total_runs=total_runs,
                success_rate=success_rate,
                last_run_at=last_run_at.isoformat() if last_run_at else None,
                user_id=user.id,
                username=user.username,
                email=user.email,
                created_at=workflow.created_at.isoformat() if workflow.created_at else "",
                updated_at=workflow.updated_at.isoformat() if workflow.updated_at else "",
            ).model_dump()
        )

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total > 0 else 1,
    )


@router.post("/workflows/{workflow_id}/toggle")
async def toggle_workflow_active(
    workflow_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, object]:
    """Toggle workflow is_active state."""
    result = await db.execute(
        select(Workflow).where(Workflow.id == workflow_id)
    )
    workflow = result.scalar_one_or_none()
    if workflow is None:
        raise AppError("workflow_not_found", status_code=404)

    workflow.is_active = not workflow.is_active
    await db.commit()

    await write_audit(
        db,
        current_user,
        "workflow.admin_toggle",
        target_type="workflow",
        target_id=workflow_id,
        target_label=workflow.name,
        detail=f"is_active={workflow.is_active}",
    )

    return {"ok": True, "is_active": workflow.is_active}


@router.delete("/workflows/{workflow_id}", status_code=204)
async def admin_delete_workflow(
    workflow_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """Delete any workflow by ID. Requires admin privileges."""
    result = await db.execute(
        select(Workflow).where(Workflow.id == workflow_id)
    )
    workflow = result.scalar_one_or_none()
    if workflow is None:
        raise AppError("workflow_not_found", status_code=404)

    workflow_name = workflow.name
    await db.delete(workflow)
    await db.commit()

    await write_audit(
        db,
        current_user,
        "workflow.admin_delete",
        target_type="workflow",
        target_id=workflow_id,
        target_label=workflow_name,
    )


# ---------------------------------------------------------------------------
# Batch operations
# ---------------------------------------------------------------------------


@router.post("/workflows/batch-delete", response_model=BatchOperationResponse)
async def batch_delete_workflows(
    body: BatchWorkflowDeleteRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> BatchOperationResponse:
    """Batch-delete workflows by IDs. Skips IDs that do not exist."""
    result = await db.execute(
        select(Workflow).where(Workflow.id.in_(body.workflow_ids))
    )
    workflows = result.scalars().all()

    count = 0
    deleted_names: list[str] = []
    for wf in workflows:
        deleted_names.append(wf.name)
        await db.delete(wf)
        count += 1

    await db.commit()

    if count > 0:
        await write_audit(
            db,
            current_user,
            "workflow.admin_batch_delete",
            target_type="workflow",
            detail=f"Deleted {count} workflow(s): {', '.join(deleted_names[:10])}",
        )

    return BatchOperationResponse(
        count=count,
        message=f"Deleted {count} workflow(s)",
    )


@router.post("/workflows/batch-toggle", response_model=BatchOperationResponse)
async def batch_toggle_workflows(
    body: BatchWorkflowToggleRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> BatchOperationResponse:
    """Batch-set is_active for workflows by IDs. Skips IDs that do not exist."""
    result = await db.execute(
        select(Workflow).where(Workflow.id.in_(body.workflow_ids))
    )
    workflows = result.scalars().all()

    count = 0
    for wf in workflows:
        wf.is_active = body.is_active
        count += 1

    await db.commit()

    if count > 0:
        await write_audit(
            db,
            current_user,
            "workflow.admin_batch_toggle",
            target_type="workflow",
            detail=f"Set is_active={body.is_active} for {count} workflow(s)",
        )

    return BatchOperationResponse(
        count=count,
        message=f"Updated {count} workflow(s) to is_active={body.is_active}",
    )


@router.post("/workflows/batch-publish", response_model=BatchOperationResponse)
async def batch_publish_workflows(
    body: BatchWorkflowPublishRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> BatchOperationResponse:
    """Batch-set status (active/draft) for workflows by IDs."""
    result = await db.execute(
        select(Workflow).where(Workflow.id.in_(body.workflow_ids))
    )
    workflows = result.scalars().all()

    count = 0
    for wf in workflows:
        wf.status = body.status
        count += 1

    await db.commit()

    if count > 0:
        await write_audit(
            db,
            current_user,
            "workflow.admin_batch_publish",
            target_type="workflow",
            detail=f"Set status={body.status} for {count} workflow(s)",
        )

    return BatchOperationResponse(
        count=count,
        message=f"Updated {count} workflow(s) to status={body.status}",
    )


# ---------------------------------------------------------------------------
# Workflow run cleanup
# ---------------------------------------------------------------------------


class CleanupRunsRequest(BaseModel):
    """Optional overrides for the cleanup operation."""

    max_age_days: int | None = Field(
        default=None,
        ge=1,
        description="Delete runs older than this many days (default: 30)",
    )
    max_runs_per_workflow: int | None = Field(
        default=None,
        ge=1,
        description="Keep at most N runs per workflow (default: 100)",
    )


class CleanupRunsResponse(BaseModel):
    """Result of a manual cleanup operation."""

    deleted_count: int


@router.post("/workflows/cleanup-runs", response_model=ApiResponse)
async def cleanup_runs(
    body: CleanupRunsRequest | None = None,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Manually trigger workflow run cleanup.

    Deletes old runs based on age and per-workflow max run limits.
    Runs with status ``pending`` or ``running`` are never deleted.
    """
    if body is None:
        body = CleanupRunsRequest()

    from fim_one.core.workflow.run_cleanup import WorkflowRunCleaner

    cleaner = WorkflowRunCleaner()
    deleted = await cleaner.cleanup(
        db,
        max_age_days=body.max_age_days,
        max_runs_per_workflow=body.max_runs_per_workflow,
    )

    await write_audit(
        db,
        current_user,
        "workflow.cleanup_runs",
        detail=f"Deleted {deleted} workflow runs",
    )

    return ApiResponse(
        data=CleanupRunsResponse(deleted_count=deleted).model_dump()
    )

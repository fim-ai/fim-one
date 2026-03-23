"""Admin endpoints for Eval Center management across all users."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.auth import get_current_admin
from fim_one.web.exceptions import AppError
from fim_one.web.models import User
from fim_one.web.models.eval import EvalCaseResult, EvalDataset, EvalRun
from fim_one.web.schemas.common import PaginatedResponse

from fim_one.web.api.admin_utils import write_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AdminEvalDatasetInfo(BaseModel):
    id: str
    name: str
    description: str | None = None
    user_id: str
    username: str | None = None
    email: str | None = None
    case_count: int = 0
    run_count: int = 0
    created_at: str


class AdminEvalRunInfo(BaseModel):
    id: str
    dataset_id: str
    dataset_name: str | None = None
    agent_id: str
    user_id: str
    username: str | None = None
    email: str | None = None
    status: str = "pending"
    total_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    pass_rate: float | None = None
    total_tokens: int | None = None
    avg_latency_ms: float | None = None
    created_at: str
    completed_at: str | None = None


class EvalStatsResponse(BaseModel):
    total_datasets: int
    total_runs: int
    avg_pass_rate: float | None = None
    total_tokens_consumed: int


class CleanupEvalRunsRequest(BaseModel):
    max_age_days: int = Field(30, ge=1, le=365)


class CleanupEvalRunsResponse(BaseModel):
    deleted_count: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/eval/datasets", response_model=PaginatedResponse)
async def list_eval_datasets(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    """List all eval datasets across all users. Requires admin privileges."""
    # Subquery: case count per dataset
    from fim_one.web.models.eval import EvalCase

    case_count_sub = (
        select(
            EvalCase.dataset_id,
            func.count(EvalCase.id).label("case_count"),
        )
        .group_by(EvalCase.dataset_id)
        .subquery()
    )

    # Subquery: run count per dataset
    run_count_sub = (
        select(
            EvalRun.dataset_id,
            func.count(EvalRun.id).label("run_count"),
        )
        .group_by(EvalRun.dataset_id)
        .subquery()
    )

    stmt = (
        select(
            EvalDataset,
            User,
            func.coalesce(case_count_sub.c.case_count, 0).label("case_count"),
            func.coalesce(run_count_sub.c.run_count, 0).label("run_count"),
        )
        .join(User, EvalDataset.user_id == User.id)
        .outerjoin(case_count_sub, EvalDataset.id == case_count_sub.c.dataset_id)
        .outerjoin(run_count_sub, EvalDataset.id == run_count_sub.c.dataset_id)
    )
    count_base = select(EvalDataset)

    if q:
        pattern = f"%{q}%"
        filter_clause = EvalDataset.name.ilike(pattern)
        stmt = stmt.where(filter_clause)
        count_base = count_base.where(filter_clause)

    count_stmt = select(func.count()).select_from(count_base.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    rows = (
        await db.execute(
            stmt.order_by(EvalDataset.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()

    items = []
    for row in rows:
        dataset = row[0]
        user = row[1]
        case_count = row[2]
        run_count = row[3]
        items.append(
            AdminEvalDatasetInfo(
                id=dataset.id,
                name=dataset.name,
                description=dataset.description,
                user_id=dataset.user_id,
                username=user.username,
                email=user.email,
                case_count=case_count,
                run_count=run_count,
                created_at=dataset.created_at.isoformat() if dataset.created_at else "",
            ).model_dump()
        )

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total > 0 else 1,
    )


@router.get("/eval/runs", response_model=PaginatedResponse)
async def list_eval_runs(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    """List all eval runs across all users. Requires admin privileges."""
    stmt = (
        select(EvalRun, User, EvalDataset.name.label("dataset_name"))
        .join(User, EvalRun.user_id == User.id)
        .join(EvalDataset, EvalRun.dataset_id == EvalDataset.id)
    )
    count_base = select(EvalRun)

    if status:
        stmt = stmt.where(EvalRun.status == status)
        count_base = count_base.where(EvalRun.status == status)

    count_stmt = select(func.count()).select_from(count_base.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    rows = (
        await db.execute(
            stmt.order_by(EvalRun.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()

    items = []
    for row in rows:
        run = row[0]
        user = row[1]
        dataset_name = row[2]

        pass_rate = None
        if run.total_cases > 0:
            pass_rate = round((run.passed_cases / run.total_cases) * 100, 1)

        items.append(
            AdminEvalRunInfo(
                id=run.id,
                dataset_id=run.dataset_id,
                dataset_name=dataset_name,
                agent_id=run.agent_id,
                user_id=run.user_id,
                username=user.username,
                email=user.email,
                status=run.status,
                total_cases=run.total_cases,
                passed_cases=run.passed_cases,
                failed_cases=run.failed_cases,
                pass_rate=pass_rate,
                total_tokens=run.total_tokens,
                avg_latency_ms=run.avg_latency_ms,
                created_at=run.created_at.isoformat() if run.created_at else "",
                completed_at=run.completed_at.isoformat() if run.completed_at else None,
            ).model_dump()
        )

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total > 0 else 1,
    )


@router.delete("/eval/datasets/{dataset_id}", status_code=204)
async def admin_delete_eval_dataset(
    dataset_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """Delete eval dataset and all its runs/cases. Requires admin privileges."""
    result = await db.execute(
        select(EvalDataset).where(EvalDataset.id == dataset_id)
    )
    dataset = result.scalar_one_or_none()
    if dataset is None:
        raise AppError("eval_dataset_not_found", status_code=404)

    dataset_name = dataset.name

    # Delete case results for all runs of this dataset
    run_ids_stmt = select(EvalRun.id).where(EvalRun.dataset_id == dataset_id)
    await db.execute(
        delete(EvalCaseResult).where(EvalCaseResult.run_id.in_(run_ids_stmt))
    )

    # Delete runs
    await db.execute(delete(EvalRun).where(EvalRun.dataset_id == dataset_id))

    # Delete dataset (cascade deletes cases)
    await db.delete(dataset)
    await db.commit()

    await write_audit(
        db,
        current_user,
        "eval_dataset.admin_delete",
        target_type="eval_dataset",
        target_id=dataset_id,
        target_label=dataset_name,
    )


@router.delete("/eval/runs/{run_id}", status_code=204)
async def admin_delete_eval_run(
    run_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """Delete an individual eval run. Requires admin privileges."""
    result = await db.execute(
        select(EvalRun).where(EvalRun.id == run_id)
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise AppError("eval_run_not_found", status_code=404)

    # Delete case results first
    await db.execute(
        delete(EvalCaseResult).where(EvalCaseResult.run_id == run_id)
    )

    await db.delete(run)
    await db.commit()

    await write_audit(
        db,
        current_user,
        "eval_run.admin_delete",
        target_type="eval_run",
        target_id=run_id,
    )


@router.post("/eval/cleanup", response_model=CleanupEvalRunsResponse)
async def cleanup_old_eval_runs(
    body: CleanupEvalRunsRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> CleanupEvalRunsResponse:
    """Cleanup eval runs older than N days. Requires admin privileges."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=body.max_age_days)

    # Find old run IDs
    old_run_ids_stmt = select(EvalRun.id).where(EvalRun.created_at < cutoff)
    old_runs = (await db.execute(old_run_ids_stmt)).scalars().all()

    if old_runs:
        # Delete case results first
        await db.execute(
            delete(EvalCaseResult).where(EvalCaseResult.run_id.in_(old_runs))
        )
        # Delete old runs
        await db.execute(
            delete(EvalRun).where(EvalRun.id.in_(old_runs))
        )
        await db.commit()

    deleted_count = len(old_runs)

    await write_audit(
        db,
        current_user,
        "eval_run.admin_cleanup",
        detail=f"Deleted {deleted_count} eval runs older than {body.max_age_days} days",
    )

    return CleanupEvalRunsResponse(deleted_count=deleted_count)


@router.get("/eval/stats", response_model=EvalStatsResponse)
async def eval_stats(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> EvalStatsResponse:
    """Summary stats for eval center. Requires admin privileges."""
    # Total datasets
    total_datasets = (
        await db.execute(select(func.count()).select_from(EvalDataset))
    ).scalar_one()

    # Total runs
    total_runs = (
        await db.execute(select(func.count()).select_from(EvalRun))
    ).scalar_one()

    # Average pass rate (only for completed runs with cases)
    avg_pass_rate_result = await db.execute(
        select(
            func.avg(
                EvalRun.passed_cases * 100.0 / func.nullif(EvalRun.total_cases, 0)
            )
        ).where(EvalRun.status == "completed", EvalRun.total_cases > 0)
    )
    avg_pass_rate_raw = avg_pass_rate_result.scalar_one()
    avg_pass_rate = round(float(avg_pass_rate_raw), 1) if avg_pass_rate_raw is not None else None

    # Total tokens consumed
    total_tokens_result = await db.execute(
        select(func.coalesce(func.sum(EvalRun.total_tokens), 0))
    )
    total_tokens_consumed = total_tokens_result.scalar_one()

    return EvalStatsResponse(
        total_datasets=total_datasets,
        total_runs=total_runs,
        avg_pass_rate=avg_pass_rate,
        total_tokens_consumed=int(total_tokens_consumed or 0),
    )

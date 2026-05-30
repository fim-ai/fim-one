"""Admin-only metrics/analytics API for system-wide observability.

Provides aggregated data on conversations, workflow runs, connector calls,
token usage, and active users.  All endpoints require admin access.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.auth import get_current_admin
from fim_one.db.models import (
    Connector,
    ConnectorCallLog,
    Conversation,
    User,
    Workflow,
    WorkflowRun,
)
from fim_one.web.schemas.common import ApiResponse
from fim_one.web.schemas.metrics import (
    ConnectorCallMetrics,
    ConnectorUsageItem,
    ConnectorUsageResponse,
    ConversationMetrics,
    DailyTokenUsage,
    MetricsOverview,
    ModelTokenBreakdown,
    TokenUsageMetrics,
    TokenUsageResponse,
    TokenUsageTotals,
    WorkflowPerformanceItem,
    WorkflowPerformanceResponse,
    WorkflowRunMetrics,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_PERIODS = {"24h", "7d", "30d"}


def _period_cutoff(period: str) -> datetime:
    """Return a UTC datetime for the start of the given period."""
    now = datetime.now(timezone.utc)
    if period == "24h":
        return now - timedelta(hours=24)
    if period == "7d":
        return now - timedelta(days=7)
    # default 30d
    return now - timedelta(days=30)


def _safe_rate(numerator: int, denominator: int) -> float:
    """Compute a ratio, returning 0.0 when the denominator is zero."""
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


# ---------------------------------------------------------------------------
# GET /api/metrics/overview
# ---------------------------------------------------------------------------


@router.get("/overview", response_model=ApiResponse)
async def get_metrics_overview(
    period: str = Query("7d", description="Time window: 24h, 7d, 30d"),
    _admin: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """System-wide summary statistics for the chosen time period."""
    if period not in _VALID_PERIODS:
        period = "7d"
    cutoff = _period_cutoff(period)
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    # -- Conversations -------------------------------------------------------
    total_conv = (
        await db.execute(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.created_at >= cutoff)
        )
    ).scalar_one()

    active_today = (
        await db.execute(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.created_at >= today_start)
        )
    ).scalar_one()

    # -- Workflow runs -------------------------------------------------------
    wf_runs_base = select(WorkflowRun).where(WorkflowRun.created_at >= cutoff)

    total_runs = (
        await db.execute(
            select(func.count()).select_from(wf_runs_base.subquery())
        )
    ).scalar_one()

    success_runs = (
        await db.execute(
            select(func.count())
            .select_from(WorkflowRun)
            .where(
                WorkflowRun.created_at >= cutoff,
                WorkflowRun.status == "completed",
            )
        )
    ).scalar_one()

    avg_duration = (
        await db.execute(
            select(func.coalesce(func.avg(WorkflowRun.duration_ms), 0))
            .where(
                WorkflowRun.created_at >= cutoff,
                WorkflowRun.duration_ms.isnot(None),
            )
        )
    ).scalar_one()

    # -- Connector calls -----------------------------------------------------
    total_calls = (
        await db.execute(
            select(func.count())
            .select_from(ConnectorCallLog)
            .where(ConnectorCallLog.created_at >= cutoff)
        )
    ).scalar_one()

    failed_calls = (
        await db.execute(
            select(func.count())
            .select_from(ConnectorCallLog)
            .where(
                ConnectorCallLog.created_at >= cutoff,
                ConnectorCallLog.success == False,  # noqa: E712
            )
        )
    ).scalar_one()

    # -- Token usage ---------------------------------------------------------
    # Conversation.total_tokens stores aggregate (input + output).
    # Conversation.fast_llm_tokens tracks the "fast" model portion.
    # We approximate: input = total_tokens - fast_llm_tokens (main model),
    #                 output = fast_llm_tokens (fast model).
    # This is a rough split -- exact per-message input/output is not stored.
    total_tokens = (
        await db.execute(
            select(func.coalesce(func.sum(Conversation.total_tokens), 0))
            .where(Conversation.created_at >= cutoff)
        )
    ).scalar_one()

    fast_tokens = (
        await db.execute(
            select(func.coalesce(func.sum(Conversation.fast_llm_tokens), 0))
            .where(Conversation.created_at >= cutoff)
        )
    ).scalar_one()

    total_input = total_tokens - fast_tokens
    total_output = fast_tokens

    # -- Active users --------------------------------------------------------
    active_users = (
        await db.execute(
            select(func.count(distinct(Conversation.user_id)))
            .where(Conversation.created_at >= cutoff)
        )
    ).scalar_one()

    overview = MetricsOverview(
        conversations=ConversationMetrics(
            total=total_conv,
            active_today=active_today,
        ),
        workflow_runs=WorkflowRunMetrics(
            total=total_runs,
            success_rate=_safe_rate(success_runs, total_runs),
            avg_duration_ms=round(float(avg_duration), 1),
        ),
        connector_calls=ConnectorCallMetrics(
            total=total_calls,
            failure_rate=_safe_rate(failed_calls, total_calls),
        ),
        token_usage=TokenUsageMetrics(
            total_input=total_input,
            total_output=total_output,
        ),
        active_users=active_users,
    )
    return ApiResponse(data=overview.model_dump())


# ---------------------------------------------------------------------------
# GET /api/metrics/token-usage
# ---------------------------------------------------------------------------


@router.get("/token-usage", response_model=ApiResponse)
async def get_token_usage(
    period: str = Query("7d", description="Time window: 24h, 7d, 30d"),
    _admin: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Token usage breakdown by model and by day."""
    if period not in _VALID_PERIODS:
        period = "7d"
    cutoff = _period_cutoff(period)

    # -- Daily breakdown by model_name ---------------------------------------
    # Conversation.model_name may be NULL; we coalesce to "unknown".
    daily_rows = (
        await db.execute(
            select(
                func.date(Conversation.created_at).label("day"),
                func.coalesce(Conversation.model_name, "unknown").label("model"),
                func.coalesce(
                    func.sum(Conversation.total_tokens - Conversation.fast_llm_tokens),
                    0,
                ).label("input_tokens"),
                func.coalesce(
                    func.sum(Conversation.fast_llm_tokens), 0
                ).label("output_tokens"),
            )
            .where(Conversation.created_at >= cutoff)
            .group_by(
                func.date(Conversation.created_at),
                func.coalesce(Conversation.model_name, "unknown"),
            )
            .order_by(func.date(Conversation.created_at))
        )
    ).all()

    daily = [
        DailyTokenUsage(
            date=str(r.day),
            input_tokens=int(r.input_tokens),
            output_tokens=int(r.output_tokens),
            model=r.model,
        )
        for r in daily_rows
    ]

    # -- Aggregate by model ---------------------------------------------------
    by_model: dict[str, ModelTokenBreakdown] = {}
    grand_input = 0
    grand_output = 0
    for d in daily:
        existing = by_model.get(d.model)
        if existing:
            existing.input += d.input_tokens
            existing.output += d.output_tokens
        else:
            by_model[d.model] = ModelTokenBreakdown(
                input=d.input_tokens, output=d.output_tokens
            )
        grand_input += d.input_tokens
        grand_output += d.output_tokens

    result = TokenUsageResponse(
        daily=daily,
        by_model=by_model,
        total=TokenUsageTotals(input=grand_input, output=grand_output),
    )
    return ApiResponse(data=result.model_dump())


# ---------------------------------------------------------------------------
# GET /api/metrics/connector-usage
# ---------------------------------------------------------------------------


@router.get("/connector-usage", response_model=ApiResponse)
async def get_connector_usage(
    period: str = Query("7d", description="Time window: 24h, 7d, 30d"),
    _admin: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Per-connector call statistics for the given period."""
    if period not in _VALID_PERIODS:
        period = "7d"
    cutoff = _period_cutoff(period)

    rows = (
        await db.execute(
            select(
                ConnectorCallLog.connector_id,
                ConnectorCallLog.connector_name,
                func.count().label("total_calls"),
                func.sum(
                    case((ConnectorCallLog.success == True, 1), else_=0)  # noqa: E712
                ).label("success_count"),
                func.coalesce(
                    func.avg(ConnectorCallLog.response_time_ms), 0
                ).label("avg_latency"),
                func.max(ConnectorCallLog.created_at).label("last_called"),
            )
            .where(ConnectorCallLog.created_at >= cutoff)
            .group_by(
                ConnectorCallLog.connector_id,
                ConnectorCallLog.connector_name,
            )
            .order_by(func.count().desc())
        )
    ).all()

    connectors = [
        ConnectorUsageItem(
            connector_id=r.connector_id,
            connector_name=r.connector_name,
            total_calls=r.total_calls,
            success_rate=_safe_rate(int(r.success_count), r.total_calls),
            avg_latency_ms=round(float(r.avg_latency), 1),
            last_called_at=(
                r.last_called.isoformat() if r.last_called else None
            ),
        )
        for r in rows
    ]

    return ApiResponse(data=ConnectorUsageResponse(connectors=connectors).model_dump())


# ---------------------------------------------------------------------------
# GET /api/metrics/workflow-performance
# ---------------------------------------------------------------------------


@router.get("/workflow-performance", response_model=ApiResponse)
async def get_workflow_performance(
    period: str = Query("7d", description="Time window: 24h, 7d, 30d"),
    _admin: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Top workflows ranked by usage and performance metrics."""
    if period not in _VALID_PERIODS:
        period = "7d"
    cutoff = _period_cutoff(period)

    # Main aggregation query
    rows = (
        await db.execute(
            select(
                WorkflowRun.workflow_id,
                Workflow.name.label("workflow_name"),
                func.count().label("total_runs"),
                func.sum(
                    case(
                        (WorkflowRun.status == "completed", 1),
                        else_=0,
                    )
                ).label("success_count"),
                func.coalesce(
                    func.avg(WorkflowRun.duration_ms), 0
                ).label("avg_duration"),
            )
            .join(Workflow, Workflow.id == WorkflowRun.workflow_id)
            .where(WorkflowRun.created_at >= cutoff)
            .group_by(WorkflowRun.workflow_id, Workflow.name)
            .order_by(func.count().desc())
            .limit(50)
        )
    ).all()

    workflow_ids = [r.workflow_id for r in rows]

    # p95 duration: fetch all durations per workflow for approximation.
    # For large data sets a proper percentile function would be needed; this
    # is practical for reasonable run volumes.
    p95_map: dict[str, float] = {}
    if workflow_ids:
        duration_rows = (
            await db.execute(
                select(
                    WorkflowRun.workflow_id,
                    WorkflowRun.duration_ms,
                )
                .where(
                    WorkflowRun.created_at >= cutoff,
                    WorkflowRun.workflow_id.in_(workflow_ids),
                    WorkflowRun.duration_ms.isnot(None),
                )
                .order_by(WorkflowRun.workflow_id, WorkflowRun.duration_ms)
            )
        ).all()

        # Group durations by workflow
        durations_by_wf: dict[str, list[int]] = defaultdict(list)
        for dr in duration_rows:
            durations_by_wf[dr.workflow_id].append(dr.duration_ms)

        for wf_id, durations in durations_by_wf.items():
            if durations:
                idx = int(math.ceil(0.95 * len(durations))) - 1
                idx = max(0, min(idx, len(durations) - 1))
                p95_map[wf_id] = float(durations[idx])

    workflows = [
        WorkflowPerformanceItem(
            workflow_id=r.workflow_id,
            name=r.workflow_name,
            total_runs=r.total_runs,
            success_rate=_safe_rate(int(r.success_count), r.total_runs),
            avg_duration_ms=round(float(r.avg_duration), 1),
            p95_duration_ms=p95_map.get(r.workflow_id, 0.0),
        )
        for r in rows
    ]

    return ApiResponse(
        data=WorkflowPerformanceResponse(workflows=workflows).model_dump()
    )

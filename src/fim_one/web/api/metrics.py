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

from fim_one.core.usage import ledger as usage_ledger
from fim_one.db import get_session
from fim_one.db.models import (
    Connector,
    ConnectorCallLog,
    Conversation,
    User,
    Workflow,
    WorkflowRun,
)
from fim_one.db.models.workflow import history_run_clause
from fim_one.web.auth import get_current_admin
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
from fim_one.web.services.call_log_stats import any_database, is_mcp, kind_of

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
    # Single-node test rows only carry token usage; they are not runs.
    wf_runs_base = select(WorkflowRun).where(
        WorkflowRun.created_at >= cutoff, history_run_clause()
    )

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
                history_run_clause(),
            )
        )
    ).scalar_one()

    avg_duration = (
        await db.execute(
            select(func.coalesce(func.avg(WorkflowRun.duration_ms), 0))
            .where(
                WorkflowRun.created_at >= cutoff,
                WorkflowRun.duration_ms.isnot(None),
                history_run_clause(),
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
    # From the usage ledger: everything recorded in the period, deleted
    # conversations and runs included. The split is not input and output:
    # "input" is the turns' own work, "output" the work done after them
    # (entry "background": titles; on the rows carried over from before the
    # ledger, the conversations' fast-model tokens), as it was before.
    total_input = 0
    total_output = 0
    for usage_slice in await usage_ledger.used_by(db, ["background"], since=cutoff):
        if usage_slice.background:
            total_output += usage_slice.total_tokens
        else:
            total_input += usage_slice.total_tokens
    total_input, total_output = max(total_input, 0), max(total_output, 0)

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

    # -- Daily breakdown by model --------------------------------------------
    # From the usage ledger, by the day it was recorded and the model it was
    # consumed on ("unknown" when the row has none: the rows carried over
    # from before the ledger for runs and for the conversations' fast-model
    # tokens). "input" and "output" are the split described in the overview.
    split: dict[tuple[str, str], list[int]] = {}
    for usage_slice in await usage_ledger.used_by(db, ["day", "model", "background"], since=cutoff):
        key = (str(usage_slice.day), usage_slice.model or "unknown")
        tokens = split.setdefault(key, [0, 0])
        tokens[1 if usage_slice.background else 0] += usage_slice.total_tokens

    daily = [
        DailyTokenUsage(
            date=day,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
        )
        for (day, model), (input_tokens, output_tokens) in sorted(split.items())
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
        total=TokenUsageTotals(input=max(grand_input, 0), output=max(grand_output, 0)),
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
    """Per-connector call statistics for the given period.

    MCP servers called in the period are listed too, with ``kind="mcp"``.
    """
    if period not in _VALID_PERIODS:
        period = "7d"
    cutoff = _period_cutoff(period)

    rows = (
        await db.execute(
            select(
                is_mcp().label("mcp_flag"),
                any_database().label("database_flag"),
                ConnectorCallLog.connector_id,
                ConnectorCallLog.mcp_server_id,
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
                is_mcp(),
                ConnectorCallLog.connector_id,
                ConnectorCallLog.mcp_server_id,
                ConnectorCallLog.connector_name,
            )
            .order_by(func.count().desc())
        )
    ).all()

    connectors = [
        ConnectorUsageItem(
            kind=kind_of(r.mcp_flag, r.database_flag),
            connector_id=r.connector_id or r.mcp_server_id or "",
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
            .where(WorkflowRun.created_at >= cutoff, history_run_clause())
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
                    history_run_clause(),
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

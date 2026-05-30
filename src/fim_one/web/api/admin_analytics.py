"""Admin endpoints for enhanced per-resource analytics and cost projection."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.auth import get_current_admin
from fim_one.db.models import (
    Agent,
    Connector,
    ConnectorCallLog,
    Conversation,
    User,
    Workflow,
    WorkflowRun,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _period_to_cutoff(period: str) -> datetime:
    """Convert period string to a UTC cutoff datetime."""
    now = datetime.now(timezone.utc)
    if period == "7d":
        return now - timedelta(days=7)
    elif period == "90d":
        return now - timedelta(days=90)
    else:
        # Default 30d
        return now - timedelta(days=30)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AgentAnalyticsItem(BaseModel):
    agent_id: str
    agent_name: str
    owner_id: str | None = None
    owner_username: str | None = None
    total_conversations: int = 0
    total_tokens: int = 0
    avg_tokens_per_conversation: float | None = None


class ConnectorAnalyticsItem(BaseModel):
    connector_id: str
    connector_name: str
    total_calls: int = 0
    success_count: int = 0
    error_count: int = 0
    success_rate: float | None = None
    avg_response_time_ms: float | None = None


class WorkflowAnalyticsItem(BaseModel):
    workflow_id: str
    workflow_name: str
    owner_id: str | None = None
    owner_username: str | None = None
    total_runs: int = 0
    success_count: int = 0
    success_rate: float | None = None
    avg_duration_ms: float | None = None


class CostProjectionResponse(BaseModel):
    trailing_7d_tokens: int
    daily_avg_tokens: int
    projected_30d_tokens: int
    trailing_7d_conversations: int


# ---------------------------------------------------------------------------
# Per-agent analytics
# ---------------------------------------------------------------------------


@router.get("/analytics/by-agent", response_model=list[AgentAnalyticsItem])
async def analytics_by_agent(
    period: str = Query("30d", pattern="^(7d|30d|90d)$"),
    top_n: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[AgentAnalyticsItem]:
    """Per-agent analytics: conversations, tokens, avg tokens/conversation."""
    cutoff = _period_to_cutoff(period)

    # Aggregate conversations per agent
    conv_sub = (
        select(
            Conversation.agent_id,
            func.count(Conversation.id).label("total_conversations"),
            func.coalesce(func.sum(Conversation.total_tokens), 0).label("total_tokens"),
        )
        .where(
            Conversation.agent_id.isnot(None),
            Conversation.created_at >= cutoff,
        )
        .group_by(Conversation.agent_id)
        .subquery()
    )

    stmt = (
        select(
            Agent.id,
            Agent.name,
            Agent.user_id,
            User.username,
            func.coalesce(conv_sub.c.total_conversations, 0).label("total_conversations"),
            func.coalesce(conv_sub.c.total_tokens, 0).label("total_tokens"),
        )
        .outerjoin(conv_sub, Agent.id == conv_sub.c.agent_id)
        .outerjoin(User, Agent.user_id == User.id)
        .order_by(func.coalesce(conv_sub.c.total_tokens, 0).desc())
        .limit(top_n)
    )

    result = await db.execute(stmt)
    items = []
    for row in result.all():
        total_conv = row.total_conversations
        total_tok = row.total_tokens
        avg_tok = round(total_tok / total_conv, 1) if total_conv > 0 else None
        items.append(
            AgentAnalyticsItem(
                agent_id=row.id,
                agent_name=row.name,
                owner_id=row.user_id,
                owner_username=row.username,
                total_conversations=total_conv,
                total_tokens=total_tok,
                avg_tokens_per_conversation=avg_tok,
            )
        )
    return items


# ---------------------------------------------------------------------------
# Per-connector analytics
# ---------------------------------------------------------------------------


@router.get("/analytics/by-connector", response_model=list[ConnectorAnalyticsItem])
async def analytics_by_connector(
    period: str = Query("30d", pattern="^(7d|30d|90d)$"),
    top_n: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[ConnectorAnalyticsItem]:
    """Per-connector analytics: calls, success rate, avg response time."""
    cutoff = _period_to_cutoff(period)

    stmt = (
        select(
            ConnectorCallLog.connector_id,
            ConnectorCallLog.connector_name,
            func.count(ConnectorCallLog.id).label("total_calls"),
            func.sum(
                case((ConnectorCallLog.success == True, 1), else_=0)  # noqa: E712
            ).label("success_count"),
            func.sum(
                case((ConnectorCallLog.success == False, 1), else_=0)  # noqa: E712
            ).label("error_count"),
            func.avg(ConnectorCallLog.response_time_ms).label("avg_response_time_ms"),
        )
        .where(ConnectorCallLog.created_at >= cutoff)
        .group_by(ConnectorCallLog.connector_id, ConnectorCallLog.connector_name)
        .order_by(func.count(ConnectorCallLog.id).desc())
        .limit(top_n)
    )

    result = await db.execute(stmt)
    items = []
    for row in result.all():
        total = row.total_calls
        success = row.success_count or 0
        errors = row.error_count or 0
        rate = round((success / total) * 100, 1) if total > 0 else None
        avg_rt = round(float(row.avg_response_time_ms), 1) if row.avg_response_time_ms else None
        items.append(
            ConnectorAnalyticsItem(
                connector_id=row.connector_id,
                connector_name=row.connector_name,
                total_calls=total,
                success_count=success,
                error_count=errors,
                success_rate=rate,
                avg_response_time_ms=avg_rt,
            )
        )
    return items


# ---------------------------------------------------------------------------
# Per-workflow analytics
# ---------------------------------------------------------------------------


@router.get("/analytics/by-workflow", response_model=list[WorkflowAnalyticsItem])
async def analytics_by_workflow(
    period: str = Query("30d", pattern="^(7d|30d|90d)$"),
    top_n: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[WorkflowAnalyticsItem]:
    """Per-workflow analytics: runs, success rate, avg duration."""
    cutoff = _period_to_cutoff(period)

    run_sub = (
        select(
            WorkflowRun.workflow_id,
            func.count(WorkflowRun.id).label("total_runs"),
            func.sum(
                case((WorkflowRun.status == "completed", 1), else_=0)
            ).label("success_count"),
            func.avg(WorkflowRun.duration_ms).label("avg_duration_ms"),
        )
        .where(WorkflowRun.created_at >= cutoff)
        .group_by(WorkflowRun.workflow_id)
        .subquery()
    )

    stmt = (
        select(
            Workflow.id,
            Workflow.name,
            Workflow.user_id,
            User.username,
            func.coalesce(run_sub.c.total_runs, 0).label("total_runs"),
            func.coalesce(run_sub.c.success_count, 0).label("success_count"),
            run_sub.c.avg_duration_ms,
        )
        .outerjoin(run_sub, Workflow.id == run_sub.c.workflow_id)
        .outerjoin(User, Workflow.user_id == User.id)
        .order_by(func.coalesce(run_sub.c.total_runs, 0).desc())
        .limit(top_n)
    )

    result = await db.execute(stmt)
    items = []
    for row in result.all():
        total = row.total_runs
        success = row.success_count
        rate = round((success / total) * 100, 1) if total > 0 else None
        avg_dur = round(float(row.avg_duration_ms), 1) if row.avg_duration_ms else None
        items.append(
            WorkflowAnalyticsItem(
                workflow_id=row.id,
                workflow_name=row.name,
                owner_id=row.user_id,
                owner_username=row.username,
                total_runs=total,
                success_count=success,
                success_rate=rate,
                avg_duration_ms=avg_dur,
            )
        )
    return items


# ---------------------------------------------------------------------------
# Cost projection
# ---------------------------------------------------------------------------


@router.get("/analytics/cost-projection", response_model=CostProjectionResponse)
async def cost_projection(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> CostProjectionResponse:
    """Project token cost for next 30 days based on trailing 7-day average."""
    cutoff_7d = datetime.now(timezone.utc) - timedelta(days=7)

    result = await db.execute(
        select(
            func.coalesce(func.sum(Conversation.total_tokens), 0).label("total_tokens"),
            func.count(Conversation.id).label("total_conversations"),
        ).where(Conversation.created_at >= cutoff_7d)
    )
    row = result.one()
    trailing_tokens = row.total_tokens
    trailing_conversations = row.total_conversations

    daily_avg = trailing_tokens // 7 if trailing_tokens > 0 else 0
    projected_30d = daily_avg * 30

    return CostProjectionResponse(
        trailing_7d_tokens=trailing_tokens,
        daily_avg_tokens=daily_avg,
        projected_30d_tokens=projected_30d,
        trailing_7d_conversations=trailing_conversations,
    )

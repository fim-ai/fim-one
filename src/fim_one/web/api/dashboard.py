"""Personal dashboard stats endpoint for the logged-in user."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.auth import get_current_user, get_user_org_ids
from fim_one.web.models import Agent, Connector, ConnectorCallLog, Conversation, KnowledgeBase, User
from fim_one.web.visibility import build_visibility_filter

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class DashboardConversation(BaseModel):
    id: str
    title: str
    agent_id: str | None
    agent_name: str | None
    created_at: str
    updated_at: str | None


class DashboardAgent(BaseModel):
    id: str
    name: str
    icon: str | None
    description: str | None
    conversation_count: int


class DashboardKB(BaseModel):
    id: str
    name: str
    document_count: int
    total_chunks: int


class DashboardConnectorHealth(BaseModel):
    id: str
    name: str
    type: str
    status: str  # "active" | "inactive" | "error"
    call_count_today: int


class DashboardDayStat(BaseModel):
    date: str  # "YYYY-MM-DD"
    count: int


class DashboardStatsResponse(BaseModel):
    # totals
    total_conversations: int
    total_agents: int
    total_tokens: int
    active_connectors: int
    # week-over-week trends (% change, can be negative)
    conversations_week_trend: float  # e.g. 12.5 means +12.5%
    tokens_week_trend: float
    # sections
    recent_conversations: list[DashboardConversation]  # last 6, ordered by updated_at desc
    top_agents: list[DashboardAgent]                   # top 4 by conversation count
    top_kbs: list[DashboardKB]                         # top 3 by document_count
    connector_health: list[DashboardConnectorHealth]   # all connectors accessible to user
    activity_trend: list[DashboardDayStat]             # 14 days, zero-filled


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _week_trend(current: int, previous: int) -> float:
    """Calculate week-over-week percentage change."""
    if previous == 0 and current > 0:
        return 100.0
    if previous == 0 and current == 0:
        return 0.0
    return round(((current - previous) / previous) * 100, 2)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=DashboardStatsResponse)
async def get_dashboard_stats(
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> DashboardStatsResponse:
    """Return personal dashboard statistics for the logged-in user."""
    user_org_ids = await get_user_org_ids(current_user.id, db)

    # ------------------------------------------------------------------
    # 1. Aggregate totals (individual scalar queries for clarity)
    # ------------------------------------------------------------------

    # total_conversations: conversations owned by user
    total_conv_result = await db.execute(
        select(func.count())
        .select_from(Conversation)
        .where(Conversation.user_id == current_user.id)
    )
    total_conversations: int = total_conv_result.scalar_one()

    # total_tokens: sum of tokens across all user conversations
    total_tokens_result = await db.execute(
        select(func.coalesce(func.sum(Conversation.total_tokens), 0))
        .select_from(Conversation)
        .where(Conversation.user_id == current_user.id)
    )
    total_tokens: int = total_tokens_result.scalar_one()

    # total_agents: non-builder agents owned by the user
    total_agents_result = await db.execute(
        select(func.count())
        .select_from(Agent)
        .where(
            Agent.user_id == current_user.id,
            Agent.is_builder == False,  # noqa: E712
        )
    )
    total_agents: int = total_agents_result.scalar_one()

    # active_connectors: connectors visible to user
    active_conn_result = await db.execute(
        select(func.count())
        .select_from(Connector)
        .where(build_visibility_filter(Connector, current_user.id, user_org_ids))
    )
    active_connectors: int = active_conn_result.scalar_one()

    # ------------------------------------------------------------------
    # 2. Week-over-week trends
    # ------------------------------------------------------------------

    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=7)   # last 7 days
    two_weeks_start = now - timedelta(days=14)  # previous 7 days (days 8–14 ago)

    # Current week conversations
    curr_conv_result = await db.execute(
        select(func.count())
        .select_from(Conversation)
        .where(
            Conversation.user_id == current_user.id,
            Conversation.created_at >= week_start,
        )
    )
    curr_week_conv: int = curr_conv_result.scalar_one()

    # Previous week conversations
    prev_conv_result = await db.execute(
        select(func.count())
        .select_from(Conversation)
        .where(
            Conversation.user_id == current_user.id,
            Conversation.created_at >= two_weeks_start,
            Conversation.created_at < week_start,
        )
    )
    prev_week_conv: int = prev_conv_result.scalar_one()

    conversations_week_trend = _week_trend(curr_week_conv, prev_week_conv)

    # Current week tokens
    curr_tok_result = await db.execute(
        select(func.coalesce(func.sum(Conversation.total_tokens), 0))
        .select_from(Conversation)
        .where(
            Conversation.user_id == current_user.id,
            Conversation.created_at >= week_start,
        )
    )
    curr_week_tokens: int = curr_tok_result.scalar_one()

    # Previous week tokens
    prev_tok_result = await db.execute(
        select(func.coalesce(func.sum(Conversation.total_tokens), 0))
        .select_from(Conversation)
        .where(
            Conversation.user_id == current_user.id,
            Conversation.created_at >= two_weeks_start,
            Conversation.created_at < week_start,
        )
    )
    prev_week_tokens: int = prev_tok_result.scalar_one()

    tokens_week_trend = _week_trend(curr_week_tokens, prev_week_tokens)

    # ------------------------------------------------------------------
    # 3. Recent conversations (last 6)
    # ------------------------------------------------------------------

    recent_conv_rows = await db.execute(
        select(
            Conversation.id,
            Conversation.title,
            Conversation.agent_id,
            Conversation.created_at,
            Conversation.updated_at,
            Agent.name.label("agent_name"),
        )
        .outerjoin(Agent, Agent.id == Conversation.agent_id)
        .where(Conversation.user_id == current_user.id)
        .order_by(
            Conversation.updated_at.desc().nullslast(),
            Conversation.created_at.desc(),
        )
        .limit(6)
    )
    recent_conversations = [
        DashboardConversation(
            id=r.id,
            title=r.title or "",
            agent_id=r.agent_id,
            agent_name=r.agent_name,
            created_at=r.created_at.isoformat() if r.created_at else "",
            updated_at=r.updated_at.isoformat() if r.updated_at else None,
        )
        for r in recent_conv_rows.all()
    ]

    # ------------------------------------------------------------------
    # 4. Top agents (top 4 by conversation count)
    # ------------------------------------------------------------------

    top_agents_rows = await db.execute(
        select(
            Agent.id,
            Agent.name,
            Agent.icon,
            Agent.description,
            func.count(Conversation.id).label("conv_count"),
        )
        .outerjoin(
            Conversation,
            and_(
                Conversation.agent_id == Agent.id,
                Conversation.user_id == current_user.id,
            ),
        )
        .where(
            Agent.user_id == current_user.id,
            Agent.is_builder == False,  # noqa: E712
        )
        .group_by(Agent.id, Agent.name, Agent.icon, Agent.description)
        .order_by(func.count(Conversation.id).desc())
        .limit(4)
    )
    top_agents = [
        DashboardAgent(
            id=r.id,
            name=r.name,
            icon=r.icon,
            description=r.description,
            conversation_count=r.conv_count,
        )
        for r in top_agents_rows.all()
    ]

    # ------------------------------------------------------------------
    # 5. Top KBs (top 3 by document_count, accessible to user)
    # ------------------------------------------------------------------

    top_kbs_rows = await db.execute(
        select(
            KnowledgeBase.id,
            KnowledgeBase.name,
            KnowledgeBase.document_count,
            KnowledgeBase.total_chunks,
        )
        .where(build_visibility_filter(KnowledgeBase, current_user.id, user_org_ids))
        .order_by(KnowledgeBase.document_count.desc())
        .limit(3)
    )
    top_kbs = [
        DashboardKB(
            id=r.id,
            name=r.name,
            document_count=r.document_count,
            total_chunks=r.total_chunks,
        )
        for r in top_kbs_rows.all()
    ]

    # ------------------------------------------------------------------
    # 6. Connector health (all accessible, max 10)
    # ------------------------------------------------------------------

    today = datetime.now(timezone.utc).date()

    # Fetch accessible connectors (up to 10)
    connector_rows = await db.execute(
        select(
            Connector.id,
            Connector.name,
            Connector.type,
            Connector.status,
        )
        .where(build_visibility_filter(Connector, current_user.id, user_org_ids))
        .limit(10)
    )
    connector_list = connector_rows.all()

    # Batch fetch today's call counts for these connectors
    connector_ids = [r.id for r in connector_list]
    call_count_map: dict[str, int] = {}
    if connector_ids:
        call_count_rows = await db.execute(
            select(
                ConnectorCallLog.connector_id,
                func.count().label("cnt"),
            )
            .where(
                ConnectorCallLog.connector_id.in_(connector_ids),
                func.date(ConnectorCallLog.created_at) == today,
            )
            .group_by(ConnectorCallLog.connector_id)
        )
        for ccr in call_count_rows.all():
            call_count_map[ccr.connector_id] = ccr.cnt

    connector_health = [
        DashboardConnectorHealth(
            id=r.id,
            name=r.name,
            type=r.type,
            status=r.status,
            call_count_today=call_count_map.get(r.id, 0),
        )
        for r in connector_list
    ]

    # ------------------------------------------------------------------
    # 7. Activity trend (14 days, zero-filled)
    # ------------------------------------------------------------------

    activity_cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    trend_rows = await db.execute(
        select(
            func.date(Conversation.created_at).label("day"),
            func.count().label("cnt"),
        )
        .where(
            Conversation.user_id == current_user.id,
            Conversation.created_at >= activity_cutoff,
        )
        .group_by(func.date(Conversation.created_at))
        .order_by(func.date(Conversation.created_at))
    )

    today_date = date.today()
    # Build ordered map from oldest to newest (13 days ago → today)
    date_map: dict[str, int] = {
        str(today_date - timedelta(days=i)): 0 for i in range(13, -1, -1)
    }
    for row in trend_rows.all():
        key = str(row.day)
        if key in date_map:
            date_map[key] = row.cnt

    activity_trend = [
        DashboardDayStat(date=d, count=c) for d, c in date_map.items()
    ]

    # ------------------------------------------------------------------
    # Assemble and return
    # ------------------------------------------------------------------

    return DashboardStatsResponse(
        total_conversations=total_conversations,
        total_agents=total_agents,
        total_tokens=total_tokens,
        active_connectors=active_connectors,
        conversations_week_trend=conversations_week_trend,
        tokens_week_trend=tokens_week_trend,
        recent_conversations=recent_conversations,
        top_agents=top_agents,
        top_kbs=top_kbs,
        connector_health=connector_health,
        activity_trend=activity_trend,
    )

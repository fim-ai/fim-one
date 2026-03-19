"""Personal dashboard stats endpoint for the logged-in user."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.auth import get_current_user, get_user_org_ids
from fim_one.web.models import Agent, Connector, ConnectorCallLog, Conversation, KnowledgeBase, User
from fim_one.web.models.resource_subscription import ResourceSubscription
from fim_one.web.models.workflow import Workflow, WorkflowRun
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
    icon: str | None = None
    type: str
    status: str  # "active" | "inactive" | "error"
    call_count_today: int


class DashboardWorkflowRun(BaseModel):
    id: str
    workflow_id: str
    workflow_name: str
    status: str  # "completed" | "failed" | "cancelled" | "running"
    created_at: str


class DashboardDayStat(BaseModel):
    date: str  # "YYYY-MM-DD"
    count: int
    tokens: int  # total tokens consumed that day


class DashboardStatsResponse(BaseModel):
    # totals
    total_conversations: int
    total_agents: int
    total_tokens: int
    active_connectors: int
    # secondary metrics for stat cards
    agent_conversations_today: int  # conversations using agents, created today
    connector_calls_today: int      # total connector calls today
    # week-over-week trends (% change, can be negative)
    conversations_week_trend: float  # e.g. 12.5 means +12.5%
    tokens_week_trend: float
    # sections
    recent_conversations: list[DashboardConversation]  # last 6, ordered by updated_at desc
    top_agents: list[DashboardAgent]                   # top 4 by conversation count
    top_kbs: list[DashboardKB]                         # top 3 by document_count
    connector_health: list[DashboardConnectorHealth]   # all connectors accessible to user
    activity_trend: list[DashboardDayStat]             # 7 days, zero-filled
    # workflow stats
    total_workflows: int                               # total active workflows owned by user
    workflow_runs_today: int                            # runs started today
    workflow_success_rate: float                        # percentage 0-100 across all runs (last 30 days)
    recent_workflow_runs: list[DashboardWorkflowRun]   # last 5 runs


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

    # Fetch subscribed connector IDs for visibility filter
    conn_sub_result = await db.execute(
        select(ResourceSubscription.resource_id).where(
            ResourceSubscription.user_id == current_user.id,
            ResourceSubscription.resource_type == "connector",
        )
    )
    subscribed_connector_ids = list(conn_sub_result.scalars().all())

    # Fetch subscribed KB IDs for visibility filter
    kb_sub_result = await db.execute(
        select(ResourceSubscription.resource_id).where(
            ResourceSubscription.user_id == current_user.id,
            ResourceSubscription.resource_type == "knowledge_base",
        )
    )
    subscribed_kb_ids = list(kb_sub_result.scalars().all())

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
        .where(build_visibility_filter(Connector, current_user.id, user_org_ids, subscribed_ids=subscribed_connector_ids))
    )
    active_connectors: int = active_conn_result.scalar_one()

    # ------------------------------------------------------------------
    # 1b. Secondary stat-card metrics
    # ------------------------------------------------------------------

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # agent_conversations_today: conversations with an agent, created today
    agent_conv_today_result = await db.execute(
        select(func.count())
        .select_from(Conversation)
        .where(
            Conversation.user_id == current_user.id,
            Conversation.agent_id.isnot(None),
            Conversation.created_at >= today_start,
        )
    )
    agent_conversations_today: int = agent_conv_today_result.scalar_one()

    # connector_calls_today: total call-log entries today across visible connectors
    connector_calls_today_result = await db.execute(
        select(func.count())
        .select_from(ConnectorCallLog)
        .where(
            ConnectorCallLog.user_id == current_user.id,
            ConnectorCallLog.created_at >= today_start,
        )
    )
    connector_calls_today: int = connector_calls_today_result.scalar_one()

    # ------------------------------------------------------------------
    # 2. Week-over-week trends
    # ------------------------------------------------------------------

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
        .where(
            Conversation.user_id == current_user.id,
            or_(Conversation.agent_id.is_(None), Agent.is_builder == False),
        )
        .order_by(Conversation.created_at.desc())
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
        .where(build_visibility_filter(KnowledgeBase, current_user.id, user_org_ids, subscribed_ids=subscribed_kb_ids))
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
            Connector.icon,
            Connector.type,
            Connector.status,
        )
        .where(build_visibility_filter(Connector, current_user.id, user_org_ids, subscribed_ids=subscribed_connector_ids))
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
            icon=r.icon,
            type=r.type,
            status=r.status,
            call_count_today=call_count_map.get(r.id, 0),
        )
        for r in connector_list
    ]

    # ------------------------------------------------------------------
    # 7. Activity trend (7 days, zero-filled)
    # ------------------------------------------------------------------

    activity_cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    trend_rows = await db.execute(
        select(
            func.date(Conversation.created_at).label("day"),
            func.count().label("cnt"),
            func.coalesce(func.sum(Conversation.total_tokens), 0).label("tok"),
        )
        .where(
            Conversation.user_id == current_user.id,
            Conversation.created_at >= activity_cutoff,
        )
        .group_by(func.date(Conversation.created_at))
        .order_by(func.date(Conversation.created_at))
    )

    today_date = date.today()
    # Build ordered map from oldest to newest (6 days ago → today)
    date_map: dict[str, dict] = {
        str(today_date - timedelta(days=i)): {"count": 0, "tokens": 0}
        for i in range(6, -1, -1)
    }
    for row in trend_rows.all():
        key = str(row.day)
        if key in date_map:
            date_map[key]["count"] = row.cnt
            date_map[key]["tokens"] = row.tok

    activity_trend = [
        DashboardDayStat(date=d, count=v["count"], tokens=v["tokens"])
        for d, v in date_map.items()
    ]

    # ------------------------------------------------------------------
    # 8. Workflow stats
    # ------------------------------------------------------------------

    # total_workflows: active workflows owned by user
    total_wf_result = await db.execute(
        select(func.count())
        .select_from(Workflow)
        .where(
            Workflow.user_id == current_user.id,
            Workflow.is_active == True,  # noqa: E712
        )
    )
    total_workflows: int = total_wf_result.scalar_one()

    # workflow_runs_today: runs started today
    wf_runs_today_result = await db.execute(
        select(func.count())
        .select_from(WorkflowRun)
        .where(
            WorkflowRun.user_id == current_user.id,
            WorkflowRun.created_at >= today_start,
        )
    )
    workflow_runs_today: int = wf_runs_today_result.scalar_one()

    # workflow_success_rate: completed / total * 100 over last 30 days
    thirty_days_ago = now - timedelta(days=30)

    wf_total_30d_result = await db.execute(
        select(func.count())
        .select_from(WorkflowRun)
        .where(
            WorkflowRun.user_id == current_user.id,
            WorkflowRun.created_at >= thirty_days_ago,
        )
    )
    wf_total_30d: int = wf_total_30d_result.scalar_one()

    if wf_total_30d > 0:
        wf_completed_30d_result = await db.execute(
            select(func.count())
            .select_from(WorkflowRun)
            .where(
                WorkflowRun.user_id == current_user.id,
                WorkflowRun.created_at >= thirty_days_ago,
                WorkflowRun.status == "completed",
            )
        )
        wf_completed_30d: int = wf_completed_30d_result.scalar_one()
        workflow_success_rate = round((wf_completed_30d / wf_total_30d) * 100, 2)
    else:
        workflow_success_rate = 0.0

    # recent_workflow_runs: last 5 runs joined with Workflow for name
    recent_wf_rows = await db.execute(
        select(
            WorkflowRun.id,
            WorkflowRun.workflow_id,
            Workflow.name.label("workflow_name"),
            WorkflowRun.status,
            WorkflowRun.created_at,
        )
        .join(Workflow, Workflow.id == WorkflowRun.workflow_id)
        .where(WorkflowRun.user_id == current_user.id)
        .order_by(WorkflowRun.created_at.desc())
        .limit(5)
    )
    recent_workflow_runs = [
        DashboardWorkflowRun(
            id=r.id,
            workflow_id=r.workflow_id,
            workflow_name=r.workflow_name,
            status=r.status,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in recent_wf_rows.all()
    ]

    # ------------------------------------------------------------------
    # Assemble and return
    # ------------------------------------------------------------------

    return DashboardStatsResponse(
        total_conversations=total_conversations,
        total_agents=total_agents,
        total_tokens=total_tokens,
        active_connectors=active_connectors,
        agent_conversations_today=agent_conversations_today,
        connector_calls_today=connector_calls_today,
        conversations_week_trend=conversations_week_trend,
        tokens_week_trend=tokens_week_trend,
        recent_conversations=recent_conversations,
        top_agents=top_agents,
        top_kbs=top_kbs,
        connector_health=connector_health,
        activity_trend=activity_trend,
        total_workflows=total_workflows,
        workflow_runs_today=workflow_runs_today,
        workflow_success_rate=workflow_success_rate,
        recent_workflow_runs=recent_workflow_runs,
    )

"""Admin-only API endpoints for system statistics and user management."""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_agent.db import get_session
from fim_agent.web.auth import get_current_admin, hash_password
from fim_agent.web.models import Agent, Connector, ConnectorCallLog, Conversation, KnowledgeBase, Message, SystemSetting, User
from fim_agent.web.schemas.common import PaginatedResponse

# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

SETTING_REGISTRATION_ENABLED = "registration_enabled"


async def get_setting(db: AsyncSession, key: str, default: str = "") -> str:
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    row = result.scalar_one_or_none()
    return row.value if row is not None else default


async def set_setting(db: AsyncSession, key: str, value: str) -> None:
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    row = result.scalar_one_or_none()
    if row is None:
        db.add(SystemSetting(key=key, value=value))
    else:
        row.value = value
    await db.commit()

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ModelStat(BaseModel):
    model: str
    count: int


class AgentStat(BaseModel):
    agent_id: str
    name: str
    count: int


class DayStat(BaseModel):
    date: str
    count: int


class ConnectorCallStat(BaseModel):
    connector_id: str
    connector_name: str
    call_count: int


class ConnectorActionStat(BaseModel):
    action_name: str
    connector_name: str
    call_count: int


class ConnectorStatsResponse(BaseModel):
    total_calls: int
    today_calls: int
    success_rate: float
    avg_response_time_ms: float
    top_connectors: list[ConnectorCallStat]
    top_actions: list[ConnectorActionStat]
    recent_days: list[DayStat]


class AgentTokenStat(BaseModel):
    agent_id: str
    name: str
    total_tokens: int


class StatsResponse(BaseModel):
    total_users: int
    total_conversations: int
    total_messages: int
    total_tokens: int
    total_fast_llm_tokens: int = 0
    total_agents: int
    total_kbs: int
    total_documents: int = 0
    total_chunks: int = 0
    total_connectors: int = 0
    today_conversations: int = 0
    tokens_by_agent: list[AgentTokenStat] = []
    conversations_by_model: list[ModelStat]
    tokens_by_model: list[ModelStat] = []
    top_agents: list[AgentStat]
    recent_days: list[DayStat]


class AdminUserInfo(BaseModel):
    id: str
    username: str
    display_name: str | None
    email: str | None
    is_admin: bool
    is_active: bool
    created_at: str


class UpdateAdminRequest(BaseModel):
    is_admin: bool


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class AdminCreateUserRequest(BaseModel):
    username: str = Field(min_length=2, max_length=50)
    password: str = Field(min_length=6, max_length=100)
    email: str = Field(..., max_length=255)
    display_name: str | None = None
    is_admin: bool = False

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email address")
        return v.lower()


class AdminUpdateUserRequest(BaseModel):
    display_name: str | None = None
    email: str | None = None


class AdminResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=6, max_length=100)


class AdminToggleActiveRequest(BaseModel):
    is_active: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_to_info(user: User) -> AdminUserInfo:
    return AdminUserInfo(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        is_admin=user.is_admin,
        is_active=user.is_active,
        created_at=user.created_at.isoformat() if user.created_at else "",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> StatsResponse:
    """Return system-wide statistics. Requires admin privileges."""
    # Total users
    total_users_result = await db.execute(select(func.count()).select_from(User))
    total_users: int = total_users_result.scalar_one()

    # Total conversations and total tokens
    conv_agg_result = await db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(Conversation.total_tokens), 0),
            func.coalesce(func.sum(Conversation.fast_llm_tokens), 0),
        ).select_from(Conversation)
    )
    row = conv_agg_result.one()
    total_conversations: int = row[0]
    total_tokens: int = row[1]
    total_fast_llm_tokens: int = row[2]

    # Total messages
    total_messages_result = await db.execute(select(func.count()).select_from(Message))
    total_messages: int = total_messages_result.scalar_one()

    # Total agents
    total_agents_result = await db.execute(select(func.count()).select_from(Agent))
    total_agents: int = total_agents_result.scalar_one()

    # Total knowledge bases
    total_kbs_result = await db.execute(select(func.count()).select_from(KnowledgeBase))
    total_kbs: int = total_kbs_result.scalar_one()

    # Conversations by model (top 10, ordered by count desc)
    # Group by LLM role: "LLM (model)" / "Fast LLM (model)" for known models
    llm_model = os.environ.get("LLM_MODEL", "")
    fast_llm_model = os.environ.get("FAST_LLM_MODEL", "")

    def _model_label(raw: str) -> str:
        if raw == "Unknown":
            return raw
        if llm_model and raw == llm_model:
            return f"LLM ({raw})"
        if fast_llm_model and raw == fast_llm_model:
            return f"Fast LLM ({raw})"
        return raw

    model_rows = await db.execute(
        select(
            func.coalesce(Conversation.model_name, "Unknown").label("model"),
            func.count().label("cnt"),
        )
        .group_by(func.coalesce(Conversation.model_name, "Unknown"))
        .order_by(func.count().desc())
        .limit(20)
    )
    label_counts: Counter[str] = Counter()
    for r in model_rows.all():
        label_counts[_model_label(r[0])] += r[1]
    conversations_by_model = [
        ModelStat(model=label, count=count)
        for label, count in label_counts.most_common(10)
    ]

    # Tokens by model (same grouping logic, but SUM(total_tokens))
    token_model_rows = await db.execute(
        select(
            func.coalesce(Conversation.model_name, "Unknown").label("model"),
            func.coalesce(
                func.sum(Conversation.total_tokens - Conversation.fast_llm_tokens), 0
            ).label("tokens"),
        )
        .group_by(func.coalesce(Conversation.model_name, "Unknown"))
        .order_by(func.sum(Conversation.total_tokens - Conversation.fast_llm_tokens).desc())
        .limit(20)
    )
    token_label_counts: Counter[str] = Counter()
    for r in token_model_rows.all():
        token_label_counts[_model_label(r[0])] += r[1]
    tokens_by_model = [
        ModelStat(model=label, count=count)
        for label, count in token_label_counts.most_common(10)
    ]
    # Add fast LLM tokens as a separate pie chart entry
    if total_fast_llm_tokens > 0:
        fast_label = f"Fast LLM ({fast_llm_model})" if fast_llm_model else "Fast LLM"
        tokens_by_model.append(ModelStat(model=fast_label, count=total_fast_llm_tokens))

    # Top agents by conversation count (top 5), joined to get agent name
    agent_rows = await db.execute(
        select(
            Conversation.agent_id,
            Agent.name,
            func.count().label("cnt"),
        )
        .join(Agent, Agent.id == Conversation.agent_id)
        .where(Conversation.agent_id.isnot(None))
        .group_by(Conversation.agent_id, Agent.name)
        .order_by(func.count().desc())
        .limit(5)
    )
    top_agents = [
        AgentStat(agent_id=r[0], name=r[1], count=r[2]) for r in agent_rows.all()
    ]

    # Conversations per day for the last 14 days
    cutoff: datetime = datetime.now(timezone.utc) - timedelta(days=14)
    day_rows = await db.execute(
        select(
            func.date(Conversation.created_at).label("day"),
            func.count().label("cnt"),
        )
        .where(Conversation.created_at >= cutoff)
        .group_by(func.date(Conversation.created_at))
        .order_by(func.date(Conversation.created_at))
    )
    recent_days = [DayStat(date=str(r[0]), count=r[1]) for r in day_rows.all()]

    # KB documents & chunks
    kb_agg_result = await db.execute(
        select(
            func.coalesce(func.sum(KnowledgeBase.document_count), 0),
            func.coalesce(func.sum(KnowledgeBase.total_chunks), 0),
        ).select_from(KnowledgeBase)
    )
    kb_row = kb_agg_result.one()
    total_documents: int = kb_row[0]
    total_chunks: int = kb_row[1]

    # Total connectors
    total_connectors_result = await db.execute(
        select(func.count()).select_from(Connector)
    )
    total_connectors: int = total_connectors_result.scalar_one()

    # Today's conversations
    today = datetime.now(timezone.utc).date()
    today_conv_result = await db.execute(
        select(func.count()).select_from(Conversation).where(
            func.date(Conversation.created_at) == today
        )
    )
    today_conversations: int = today_conv_result.scalar_one()

    # Tokens by agent (top 10)
    tokens_by_agent_rows = await db.execute(
        select(
            Conversation.agent_id,
            Agent.name,
            func.sum(Conversation.total_tokens).label("total"),
        )
        .join(Agent, Agent.id == Conversation.agent_id)
        .where(Conversation.agent_id.isnot(None))
        .group_by(Conversation.agent_id, Agent.name)
        .order_by(func.sum(Conversation.total_tokens).desc())
        .limit(10)
    )
    tokens_by_agent = [
        AgentTokenStat(agent_id=r[0], name=r[1], total_tokens=r[2])
        for r in tokens_by_agent_rows.all()
    ]

    return StatsResponse(
        total_users=total_users,
        total_conversations=total_conversations,
        total_messages=total_messages,
        total_tokens=total_tokens,
        total_fast_llm_tokens=total_fast_llm_tokens,
        total_agents=total_agents,
        total_kbs=total_kbs,
        total_documents=total_documents,
        total_chunks=total_chunks,
        total_connectors=total_connectors,
        today_conversations=today_conversations,
        tokens_by_agent=tokens_by_agent,
        conversations_by_model=conversations_by_model,
        tokens_by_model=tokens_by_model,
        top_agents=top_agents,
        recent_days=recent_days,
    )


@router.get("/connector-stats", response_model=ConnectorStatsResponse)
async def get_connector_stats(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ConnectorStatsResponse:
    """Return connector call statistics. Requires admin privileges."""
    # Total calls
    total_result = await db.execute(
        select(func.count()).select_from(ConnectorCallLog)
    )
    total_calls: int = total_result.scalar_one()

    # Today's calls
    today = datetime.now(timezone.utc).date()
    today_result = await db.execute(
        select(func.count()).select_from(ConnectorCallLog).where(
            func.date(ConnectorCallLog.created_at) == today
        )
    )
    today_calls: int = today_result.scalar_one()

    # Success rate
    if total_calls > 0:
        success_result = await db.execute(
            select(func.count()).select_from(ConnectorCallLog).where(
                ConnectorCallLog.success == True  # noqa: E712
            )
        )
        success_count: int = success_result.scalar_one()
        success_rate = success_count / total_calls
    else:
        success_rate = 0.0

    # Average response time
    avg_result = await db.execute(
        select(func.avg(ConnectorCallLog.response_time_ms)).where(
            ConnectorCallLog.response_time_ms.isnot(None)
        )
    )
    avg_response_time_ms = avg_result.scalar_one() or 0.0

    # Top connectors (top 10)
    top_conn_rows = await db.execute(
        select(
            ConnectorCallLog.connector_id,
            ConnectorCallLog.connector_name,
            func.count().label("cnt"),
        )
        .group_by(ConnectorCallLog.connector_id, ConnectorCallLog.connector_name)
        .order_by(func.count().desc())
        .limit(10)
    )
    top_connectors = [
        ConnectorCallStat(connector_id=r[0], connector_name=r[1], call_count=r[2])
        for r in top_conn_rows.all()
    ]

    # Top actions (top 10)
    top_action_rows = await db.execute(
        select(
            ConnectorCallLog.action_name,
            ConnectorCallLog.connector_name,
            func.count().label("cnt"),
        )
        .group_by(ConnectorCallLog.action_name, ConnectorCallLog.connector_name)
        .order_by(func.count().desc())
        .limit(10)
    )
    top_actions = [
        ConnectorActionStat(action_name=r[0], connector_name=r[1], call_count=r[2])
        for r in top_action_rows.all()
    ]

    # 14-day daily trend
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    day_rows = await db.execute(
        select(
            func.date(ConnectorCallLog.created_at).label("day"),
            func.count().label("cnt"),
        )
        .where(ConnectorCallLog.created_at >= cutoff)
        .group_by(func.date(ConnectorCallLog.created_at))
        .order_by(func.date(ConnectorCallLog.created_at))
    )
    recent_days = [DayStat(date=str(r[0]), count=r[1]) for r in day_rows.all()]

    return ConnectorStatsResponse(
        total_calls=total_calls,
        today_calls=today_calls,
        success_rate=round(success_rate, 4),
        avg_response_time_ms=round(avg_response_time_ms, 1),
        top_connectors=top_connectors,
        top_actions=top_actions,
        recent_days=recent_days,
    )


@router.get("/users", response_model=PaginatedResponse)
async def list_users(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    """Return paginated users with optional search. Requires admin privileges."""
    query = select(User)
    count_query = select(func.count()).select_from(User)

    if q:
        pattern = f"%{q}%"
        filter_clause = or_(User.username.ilike(pattern), User.email.ilike(pattern))
        query = query.where(filter_clause)
        count_query = count_query.where(filter_clause)

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    query = query.order_by(User.created_at.asc()).offset((page - 1) * size).limit(size)
    result = await db.execute(query)
    users = result.scalars().all()

    return PaginatedResponse(
        items=[_user_to_info(u).model_dump() for u in users],
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total > 0 else 1,
    )


@router.post("/users", response_model=AdminUserInfo)
async def create_user(
    body: AdminCreateUserRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> AdminUserInfo:
    """Create a new user. Requires admin privileges."""
    # Check username uniqueness
    result = await db.execute(select(User).where(User.username == body.username))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        )
    # Check email uniqueness
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        email=body.email,
        display_name=body.display_name,
        is_admin=body.is_admin,
    )
    db.add(user)
    await db.commit()

    result = await db.execute(select(User).where(User.id == user.id))
    user = result.scalar_one()
    return _user_to_info(user)


@router.patch("/users/{user_id}", response_model=AdminUserInfo)
async def update_user(
    user_id: str,
    body: AdminUpdateUserRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> AdminUserInfo:
    """Update a user's display_name and/or email. Requires admin privileges."""
    result = await db.execute(select(User).where(User.id == user_id))
    target_user = result.scalar_one_or_none()
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if body.display_name is not None:
        target_user.display_name = body.display_name or None
    if body.email is not None:
        if body.email:
            # Check email uniqueness (exclude self)
            email_result = await db.execute(
                select(User).where(User.email == body.email, User.id != user_id)
            )
            if email_result.scalar_one_or_none() is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Email already registered",
                )
        target_user.email = body.email or None

    await db.commit()
    result = await db.execute(select(User).where(User.id == user_id))
    target_user = result.scalar_one()
    return _user_to_info(target_user)


@router.patch("/users/{user_id}/admin", response_model=AdminUserInfo)
async def update_user_admin(
    user_id: str,
    body: UpdateAdminRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> AdminUserInfo:
    """Toggle the admin status of a user.

    An admin cannot revoke their own admin privileges through this endpoint.
    Requires admin privileges.
    """
    if current_user.id == user_id and not body.is_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot revoke your own admin status",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    target_user = result.scalar_one_or_none()
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    target_user.is_admin = body.is_admin
    await db.commit()

    result = await db.execute(select(User).where(User.id == user_id))
    target_user = result.scalar_one()
    return _user_to_info(target_user)


@router.post("/users/{user_id}/reset-password", response_model=AdminUserInfo)
async def reset_password(
    user_id: str,
    body: AdminResetPasswordRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> AdminUserInfo:
    """Reset a user's password and invalidate their refresh token. Requires admin privileges."""
    result = await db.execute(select(User).where(User.id == user_id))
    target_user = result.scalar_one_or_none()
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    target_user.password_hash = hash_password(body.new_password)
    target_user.refresh_token = None
    target_user.refresh_token_expires_at = None
    await db.commit()

    result = await db.execute(select(User).where(User.id == user_id))
    target_user = result.scalar_one()
    return _user_to_info(target_user)


@router.patch("/users/{user_id}/active", response_model=AdminUserInfo)
async def toggle_user_active(
    user_id: str,
    body: AdminToggleActiveRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> AdminUserInfo:
    """Enable or disable a user account. Requires admin privileges."""
    if current_user.id == user_id and not body.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot disable your own account",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    target_user = result.scalar_one_or_none()
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    target_user.is_active = body.is_active
    if not body.is_active:
        # Invalidate refresh token to kick the user offline
        target_user.refresh_token = None
        target_user.refresh_token_expires_at = None
    await db.commit()

    result = await db.execute(select(User).where(User.id == user_id))
    target_user = result.scalar_one()
    return _user_to_info(target_user)


# ---------------------------------------------------------------------------
# System settings endpoints
# ---------------------------------------------------------------------------


class SystemSettingsResponse(BaseModel):
    registration_enabled: bool


class UpdateSystemSettingsRequest(BaseModel):
    registration_enabled: bool


@router.get("/settings", response_model=SystemSettingsResponse)
async def get_system_settings(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> SystemSettingsResponse:
    """Return current system settings. Requires admin privileges."""
    value = await get_setting(db, SETTING_REGISTRATION_ENABLED, default="true")
    return SystemSettingsResponse(registration_enabled=value.lower() != "false")


@router.patch("/settings", response_model=SystemSettingsResponse)
async def update_system_settings(
    body: UpdateSystemSettingsRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> SystemSettingsResponse:
    """Update system settings. Requires admin privileges."""
    await set_setting(
        db, SETTING_REGISTRATION_ENABLED, "true" if body.registration_enabled else "false"
    )
    return SystemSettingsResponse(registration_enabled=body.registration_enabled)

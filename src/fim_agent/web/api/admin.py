"""Admin-only API endpoints for system statistics and user management."""

from __future__ import annotations

import math
import os
import re
import secrets
import shutil
import string
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator

from fim_agent.web.exceptions import AppError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_agent.db import get_session
from fim_agent.web.auth import get_current_admin, hash_password
from fim_agent.web.models import Agent, AuditLog, Connector, ConnectorCallLog, Conversation, InviteCode, KnowledgeBase, MCPServer as MCPServerModel, Message, SystemSetting, User
from fim_agent.web.schemas.common import PaginatedResponse
from fim_agent.web.schemas.mcp_server import MCPServerCreate, MCPServerUpdate

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


class AdminUserInfoExtended(BaseModel):
    id: str
    username: str
    display_name: str | None
    email: str | None
    is_admin: bool
    is_active: bool
    created_at: str
    has_active_session: bool
    monthly_tokens: int
    token_quota: int | None


class SetQuotaRequest(BaseModel):
    token_quota: int | None


class AdminConversationInfo(BaseModel):
    id: str
    title: str | None
    mode: str | None
    model_name: str | None
    total_tokens: int
    message_count: int
    user_id: str
    username: str
    created_at: str


class UserStorageStat(BaseModel):
    user_id: str
    username: str
    file_count: int
    total_bytes: int


class StorageStatsResponse(BaseModel):
    total_bytes: int
    users: list[UserStorageStat]


class IntegrationHealth(BaseModel):
    key: str
    label: str
    configured: bool
    detail: str | None


class InviteCodeInfo(BaseModel):
    id: str
    code: str
    note: str | None
    max_uses: int
    use_count: int
    expires_at: str | None
    is_active: bool
    created_at: str


class CreateInviteCodeRequest(BaseModel):
    note: str | None = None
    max_uses: int = 1
    expires_at: datetime | None = None


class AdminMCPServerInfo(BaseModel):
    id: str
    name: str
    description: str | None
    transport: str
    command: str | None
    args: list[str] | None
    url: str | None
    is_active: bool
    is_global: bool
    tool_count: int
    created_at: str


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

    # Bulk monthly token aggregation
    first_of_month = datetime(date.today().year, date.today().month, 1, tzinfo=timezone.utc)
    monthly_rows = await db.execute(
        select(Conversation.user_id, func.coalesce(func.sum(Conversation.total_tokens), 0))
        .where(Conversation.created_at >= first_of_month)
        .group_by(Conversation.user_id)
    )
    monthly_tokens_map = dict(monthly_rows.all())

    now = datetime.now(timezone.utc)
    items = [
        AdminUserInfoExtended(
            id=u.id,
            username=u.username,
            display_name=u.display_name,
            email=u.email,
            is_admin=u.is_admin,
            is_active=u.is_active,
            created_at=u.created_at.isoformat() if u.created_at else "",
            has_active_session=(
                u.refresh_token is not None
                and u.refresh_token_expires_at is not None
                and u.refresh_token_expires_at.replace(tzinfo=timezone.utc) > now
            ),
            monthly_tokens=monthly_tokens_map.get(u.id, 0),
            token_quota=u.token_quota,
        ).model_dump()
        for u in users
    ]

    return PaginatedResponse(
        items=items,
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
        raise AppError("username_taken", status_code=409)
    # Check email uniqueness
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none() is not None:
        raise AppError("email_already_registered", status_code=409)

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
    await write_audit(
        db, current_user, "user.create",
        target_type="user", target_id=user.id, target_label=user.username,
    )
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
        raise AppError("user_not_found", status_code=404)

    if body.display_name is not None:
        target_user.display_name = body.display_name or None
    if body.email is not None:
        if body.email:
            # Check email uniqueness (exclude self)
            email_result = await db.execute(
                select(User).where(User.email == body.email, User.id != user_id)
            )
            if email_result.scalar_one_or_none() is not None:
                raise AppError("email_already_registered", status_code=409)
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
        raise AppError("cannot_revoke_own_admin")

    result = await db.execute(select(User).where(User.id == user_id))
    target_user = result.scalar_one_or_none()
    if target_user is None:
        raise AppError("user_not_found", status_code=404)

    target_user.is_admin = body.is_admin
    await db.commit()

    result = await db.execute(select(User).where(User.id == user_id))
    target_user = result.scalar_one()
    await write_audit(
        db, current_user,
        "user.grant_admin" if body.is_admin else "user.revoke_admin",
        target_type="user", target_id=user_id, target_label=target_user.username,
    )
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
        raise AppError("user_not_found", status_code=404)

    target_user.password_hash = hash_password(body.new_password)
    target_user.refresh_token = None
    target_user.refresh_token_expires_at = None
    target_user.tokens_invalidated_at = datetime.now(timezone.utc)
    await db.commit()

    result = await db.execute(select(User).where(User.id == user_id))
    target_user = result.scalar_one()
    await write_audit(
        db, current_user, "user.reset_password",
        target_type="user", target_id=user_id, target_label=target_user.username,
    )
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
        raise AppError("cannot_disable_own_account")

    result = await db.execute(select(User).where(User.id == user_id))
    target_user = result.scalar_one_or_none()
    if target_user is None:
        raise AppError("user_not_found", status_code=404)

    target_user.is_active = body.is_active
    if not body.is_active:
        # Invalidate refresh token to kick the user offline
        target_user.refresh_token = None
        target_user.refresh_token_expires_at = None
    await db.commit()

    result = await db.execute(select(User).where(User.id == user_id))
    target_user = result.scalar_one()
    await write_audit(
        db, current_user,
        "user.enable" if body.is_active else "user.disable",
        target_type="user", target_id=user_id, target_label=target_user.username,
    )
    return _user_to_info(target_user)


# ---------------------------------------------------------------------------
# Audit log helper
# ---------------------------------------------------------------------------

SETTING_MAINTENANCE_MODE = "maintenance_mode"
SETTING_ANNOUNCEMENT_ENABLED = "announcement_enabled"
SETTING_ANNOUNCEMENT_TEXT = "announcement_text"
SETTING_REGISTRATION_MODE = "registration_mode"
SETTING_DEFAULT_TOKEN_QUOTA = "default_token_quota"


async def write_audit(
    db: AsyncSession,
    admin: User,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    target_label: str | None = None,
    detail: str | None = None,
) -> None:
    """Append an audit log entry. Call after the main db.commit() succeeds."""
    db.add(
        AuditLog(
            admin_id=admin.id,
            admin_username=admin.username,
            action=action,
            target_type=target_type,
            target_id=target_id,
            target_label=target_label,
            detail=detail,
        )
    )
    await db.commit()


# ---------------------------------------------------------------------------
# System settings endpoints
# ---------------------------------------------------------------------------


class SystemSettingsResponse(BaseModel):
    registration_enabled: bool
    registration_mode: str
    maintenance_mode: bool
    announcement_enabled: bool
    announcement_text: str
    default_token_quota: int


class UpdateSystemSettingsRequest(BaseModel):
    registration_enabled: bool | None = None
    registration_mode: str | None = None
    maintenance_mode: bool | None = None
    announcement_enabled: bool | None = None
    announcement_text: str | None = None
    default_token_quota: int | None = None


async def _load_all_settings(db: AsyncSession) -> SystemSettingsResponse:
    reg = await get_setting(db, SETTING_REGISTRATION_ENABLED, default="true")
    reg_mode = await get_setting(db, SETTING_REGISTRATION_MODE, default="")
    maint = await get_setting(db, SETTING_MAINTENANCE_MODE, default="false")
    ann_en = await get_setting(db, SETTING_ANNOUNCEMENT_ENABLED, default="false")
    ann_txt = await get_setting(db, SETTING_ANNOUNCEMENT_TEXT, default="")
    quota_str = await get_setting(db, SETTING_DEFAULT_TOKEN_QUOTA, default="0")
    # Derive registration_mode: prefer explicit setting, fall back to legacy boolean
    if not reg_mode:
        reg_mode = "open" if reg.lower() != "false" else "disabled"
    return SystemSettingsResponse(
        registration_enabled=reg.lower() != "false",
        registration_mode=reg_mode,
        maintenance_mode=maint.lower() == "true",
        announcement_enabled=ann_en.lower() == "true",
        announcement_text=ann_txt,
        default_token_quota=int(quota_str) if quota_str.isdigit() else 0,
    )


@router.get("/settings", response_model=SystemSettingsResponse)
async def get_system_settings(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> SystemSettingsResponse:
    """Return current system settings. Requires admin privileges."""
    return await _load_all_settings(db)


@router.patch("/settings", response_model=SystemSettingsResponse)
async def update_system_settings(
    body: UpdateSystemSettingsRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> SystemSettingsResponse:
    """Update one or more system settings. Requires admin privileges."""
    changed: list[str] = []
    if body.registration_enabled is not None:
        await set_setting(db, SETTING_REGISTRATION_ENABLED, "true" if body.registration_enabled else "false")
        changed.append(f"registration_enabled={body.registration_enabled}")
    if body.registration_mode is not None:
        if body.registration_mode not in ("open", "invite", "disabled"):
            raise AppError(
                "invalid_registration_mode",
                detail=f"Invalid registration mode: {body.registration_mode}",
                detail_args={"mode": body.registration_mode},
            )
        await set_setting(db, SETTING_REGISTRATION_MODE, body.registration_mode)
        changed.append(f"registration_mode={body.registration_mode}")
    if body.default_token_quota is not None:
        await set_setting(db, SETTING_DEFAULT_TOKEN_QUOTA, str(body.default_token_quota))
        changed.append(f"default_token_quota={body.default_token_quota}")
    if body.maintenance_mode is not None:
        await set_setting(db, SETTING_MAINTENANCE_MODE, "true" if body.maintenance_mode else "false")
        changed.append(f"maintenance_mode={body.maintenance_mode}")
    if body.announcement_enabled is not None:
        await set_setting(db, SETTING_ANNOUNCEMENT_ENABLED, "true" if body.announcement_enabled else "false")
        changed.append(f"announcement_enabled={body.announcement_enabled}")
    if body.announcement_text is not None:
        await set_setting(db, SETTING_ANNOUNCEMENT_TEXT, body.announcement_text)
        changed.append("announcement_text updated")
    if changed:
        await write_audit(db, current_user, "settings.update", detail="; ".join(changed))
    return await _load_all_settings(db)


# ---------------------------------------------------------------------------
# Delete user
# ---------------------------------------------------------------------------


@router.delete("/users/{user_id}", response_model=AdminUserInfo)
async def delete_user(
    user_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> AdminUserInfo:
    """Permanently delete a user account. Requires admin privileges."""
    if current_user.id == user_id:
        raise AppError("cannot_delete_own_account")

    result = await db.execute(select(User).where(User.id == user_id))
    target_user = result.scalar_one_or_none()
    if target_user is None:
        raise AppError("user_not_found", status_code=404)

    info = _user_to_info(target_user)
    label = target_user.username
    await db.delete(target_user)
    await db.commit()
    await write_audit(
        db, current_user, "user.delete",
        target_type="user", target_id=user_id, target_label=label,
    )
    return info


# ---------------------------------------------------------------------------
# Force logout all users
# ---------------------------------------------------------------------------


@router.post("/actions/force-logout-all")
async def force_logout_all(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict:
    """Invalidate all refresh tokens and mark a force-logout timestamp,
    causing active access tokens to also be rejected immediately."""
    now = datetime.now(timezone.utc)
    result = await db.execute(select(User).where(User.id != current_user.id))
    users = result.scalars().all()
    count = len(users)
    for u in users:
        u.refresh_token = None
        u.refresh_token_expires_at = None
        u.tokens_invalidated_at = now
    await db.commit()
    await write_audit(db, current_user, "auth.force_logout_all", detail=f"invalidated {count} sessions")
    return {"invalidated": count}


# ---------------------------------------------------------------------------
# Audit log endpoint
# ---------------------------------------------------------------------------


class AuditLogEntry(BaseModel):
    id: str
    admin_id: str
    admin_username: str
    action: str
    target_type: str | None
    target_id: str | None
    target_label: str | None
    detail: str | None
    created_at: str


@router.get("/audit-log", response_model=PaginatedResponse)
async def list_audit_log(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    """Return paginated audit log entries, newest first. Requires admin privileges."""
    total_result = await db.execute(select(func.count()).select_from(AuditLog))
    total: int = total_result.scalar_one()

    rows_result = await db.execute(
        select(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    rows = rows_result.scalars().all()

    items = [
        AuditLogEntry(
            id=r.id,
            admin_id=r.admin_id,
            admin_username=r.admin_username,
            action=r.action,
            target_type=r.target_type,
            target_id=r.target_id,
            target_label=r.target_label,
            detail=r.detail,
            created_at=r.created_at.isoformat() if r.created_at else "",
        ).model_dump()
        for r in rows
    ]

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total > 0 else 1,
    )


# ---------------------------------------------------------------------------
# Feature 6 — Force logout single user
# ---------------------------------------------------------------------------


@router.post("/users/{user_id}/force-logout")
async def force_logout_user(
    user_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """Invalidate a single user's session. Requires admin privileges."""
    if user_id == current_user.id:
        raise AppError("cannot_force_logout_self")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise AppError("user_not_found", status_code=404)
    user.refresh_token = None
    user.refresh_token_expires_at = None
    user.tokens_invalidated_at = datetime.now(timezone.utc)
    await db.commit()
    await write_audit(
        db, current_user, "user.force_logout",
        target_type="user", target_id=user_id, target_label=user.username,
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Feature 1 — Token quota
# ---------------------------------------------------------------------------


@router.patch("/users/{user_id}/quota")
async def set_user_quota(
    user_id: str,
    body: SetQuotaRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """Set monthly token quota for a user. Requires admin privileges."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise AppError("user_not_found", status_code=404)
    user.token_quota = body.token_quota
    await db.commit()
    await write_audit(
        db, current_user, "user.set_quota",
        target_type="user", target_id=user_id, target_label=user.username,
        detail=f"quota={body.token_quota}",
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Feature 2 — API health / integration status
# ---------------------------------------------------------------------------


@router.get("/system/health", response_model=list[IntegrationHealth])
async def get_system_health(
    current_user: User = Depends(get_current_admin),  # noqa: B008
):
    """Return configuration status for key integrations. Requires admin privileges."""
    checks: list[IntegrationHealth] = []

    llm_model = os.environ.get("LLM_MODEL", "")
    llm_key = os.environ.get("LLM_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    checks.append(IntegrationHealth(
        key="llm", label="Main LLM",
        configured=bool(llm_model and llm_key),
        detail=llm_model if llm_model else None,
    ))

    fast_model = os.environ.get("FAST_LLM_MODEL", "")
    checks.append(IntegrationHealth(
        key="fast_llm", label="Fast LLM",
        configured=bool(fast_model),
        detail=fast_model if fast_model else None,
    ))

    jina_key = os.environ.get("JINA_API_KEY", "")
    emb_model = os.environ.get("EMBEDDING_MODEL", "")
    # Jina is the default embedding provider; JINA_API_KEY alone is sufficient
    emb_configured = bool(emb_model) or bool(jina_key)
    emb_detail = emb_model if emb_model else ("jina (default)" if jina_key else None)
    checks.append(IntegrationHealth(
        key="embedding", label="Embedding",
        configured=emb_configured,
        detail=emb_detail,
    ))

    reranker = os.environ.get("RERANKER_PROVIDER", "")
    # Jina reranker works with JINA_API_KEY even without explicit RERANKER_PROVIDER
    reranker_configured = bool(reranker) or bool(jina_key)
    reranker_detail = reranker if reranker else ("jina (default)" if jina_key else None)
    checks.append(IntegrationHealth(
        key="reranker", label="Reranker",
        configured=reranker_configured,
        detail=reranker_detail,
    ))

    search_provider = os.environ.get("WEB_SEARCH_PROVIDER", "jina")
    search_key = (
        os.environ.get("JINA_API_KEY", "")
        or os.environ.get("TAVILY_API_KEY", "")
        or os.environ.get("BRAVE_API_KEY", "")
    )
    checks.append(IntegrationHealth(
        key="web_search", label="Web Search",
        configured=True,
        detail=f"{search_provider} (default)" if not search_key else search_provider,
    ))

    fetch_provider = os.environ.get("WEB_FETCH_PROVIDER", "jina")
    checks.append(IntegrationHealth(
        key="web_fetch", label="Web Fetch",
        configured=True,
        detail=fetch_provider,
    ))

    image_key = os.environ.get("IMAGE_GEN_API_KEY", "")
    checks.append(IntegrationHealth(
        key="image_gen", label="Image Generation",
        configured=bool(image_key),
        detail=None,
    ))

    return checks


# ---------------------------------------------------------------------------
# Feature 3 — Conversation moderation
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_uploads_base = Path(os.environ.get("UPLOADS_DIR", "uploads"))
_UPLOADS_CONVERSATIONS_DIR = (
    _uploads_base if _uploads_base.is_absolute() else _PROJECT_ROOT / _uploads_base
) / "conversations"
_CONVERSATIONS_DIR = _PROJECT_ROOT / "tmp" / "conversations"


@router.get("/conversations", response_model=PaginatedResponse)
async def list_all_conversations(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user_id: str | None = Query(None),
    q: str | None = Query(None),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """List all conversations with optional user/search filter. Requires admin privileges."""
    stmt = (
        select(Conversation, User)
        .join(User, User.id == Conversation.user_id)
    )
    if user_id:
        stmt = stmt.where(Conversation.user_id == user_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Conversation.title.ilike(like), User.username.ilike(like)))

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    rows = (await db.execute(
        stmt.order_by(Conversation.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )).all()

    # Bulk message counts
    conv_ids = [row[0].id for row in rows]
    msg_counts: dict[str, int] = {}
    if conv_ids:
        msg_count_rows = await db.execute(
            select(Message.conversation_id, func.count())
            .where(Message.conversation_id.in_(conv_ids))
            .group_by(Message.conversation_id)
        )
        msg_counts = dict(msg_count_rows.all())

    items = [
        AdminConversationInfo(
            id=conv.id,
            title=conv.title,
            mode=conv.mode,
            model_name=conv.model_name,
            total_tokens=conv.total_tokens,
            message_count=msg_counts.get(conv.id, 0),
            user_id=user.id,
            username=user.username,
            created_at=conv.created_at.isoformat() if conv.created_at else "",
        ).model_dump()
        for conv, user in rows
    ]
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total > 0 else 1,
    )


@router.delete("/conversations/{conv_id}", status_code=204)
async def admin_delete_conversation(
    conv_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """Delete any conversation by ID. Requires admin privileges."""
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(Conversation).options(selectinload(Conversation.messages)).where(Conversation.id == conv_id)
    )
    conv = result.scalar_one_or_none()
    if conv is None:
        raise AppError("conversation_not_found", status_code=404)

    await db.delete(conv)
    await db.commit()

    # Clean up file system
    sandbox_dir = _CONVERSATIONS_DIR / conv_id
    if sandbox_dir.exists():
        shutil.rmtree(sandbox_dir, ignore_errors=True)
    uploads_dir = _UPLOADS_CONVERSATIONS_DIR / conv_id
    if uploads_dir.exists():
        shutil.rmtree(uploads_dir, ignore_errors=True)

    await write_audit(
        db, current_user, "conversation.delete",
        target_type="conversation", target_id=conv_id,
    )


# ---------------------------------------------------------------------------
# Feature 4 — Invite codes
# ---------------------------------------------------------------------------


@router.get("/invite-codes", response_model=list[InviteCodeInfo])
async def list_invite_codes(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """List all invite codes. Requires admin privileges."""
    result = await db.execute(select(InviteCode).order_by(InviteCode.created_at.desc()))
    codes = result.scalars().all()
    return [
        InviteCodeInfo(
            id=c.id, code=c.code, note=c.note,
            max_uses=c.max_uses, use_count=c.use_count,
            expires_at=c.expires_at.isoformat() if c.expires_at else None,
            is_active=c.is_active,
            created_at=c.created_at.isoformat() if c.created_at else "",
        )
        for c in codes
    ]


@router.post("/invite-codes", response_model=InviteCodeInfo, status_code=201)
async def create_invite_code(
    body: CreateInviteCodeRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """Create a new invite code. Requires admin privileges."""
    code_str = "".join(
        secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8)
    )
    code = InviteCode(
        code=code_str,
        created_by_id=current_user.id,
        note=body.note,
        max_uses=body.max_uses,
        expires_at=body.expires_at,
    )
    db.add(code)
    await db.commit()
    await db.refresh(code)
    await write_audit(
        db, current_user, "invite_code.create",
        detail=f"code={code_str}",
    )
    return InviteCodeInfo(
        id=code.id, code=code.code, note=code.note,
        max_uses=code.max_uses, use_count=code.use_count,
        expires_at=code.expires_at.isoformat() if code.expires_at else None,
        is_active=code.is_active,
        created_at=code.created_at.isoformat() if code.created_at else "",
    )


@router.delete("/invite-codes/{code_id}", status_code=204)
async def revoke_invite_code(
    code_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """Revoke (deactivate) an invite code. Requires admin privileges."""
    result = await db.execute(select(InviteCode).where(InviteCode.id == code_id))
    code = result.scalar_one_or_none()
    if code is None:
        raise AppError("invite_code_not_found", status_code=404)
    code.is_active = False
    await db.commit()
    await write_audit(
        db, current_user, "invite_code.revoke",
        detail=f"code_id={code_id}",
    )


# ---------------------------------------------------------------------------
# Feature 5 — Storage management
# ---------------------------------------------------------------------------


@router.get("/storage", response_model=StorageStatsResponse)
async def get_storage_stats(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """Return per-user file storage statistics. Requires admin privileges."""
    import os as _os

    uploads_dir = Path("uploads")
    user_stats: dict[str, dict] = {}

    if uploads_dir.exists():
        for item in uploads_dir.iterdir():
            if item.is_dir() and item.name.startswith("user_"):
                uid = item.name[5:]  # strip "user_"
                total_bytes = 0
                file_count = 0
                for dirpath, _, filenames in _os.walk(item):
                    for fn in filenames:
                        fp = Path(dirpath) / fn
                        try:
                            total_bytes += fp.stat().st_size
                            file_count += 1
                        except OSError:
                            pass
                user_stats[uid] = {"file_count": file_count, "total_bytes": total_bytes}

    # Resolve usernames
    if user_stats:
        user_rows = await db.execute(
            select(User.id, User.username).where(User.id.in_(list(user_stats.keys())))
        )
        username_map = dict(user_rows.all())
    else:
        username_map = {}

    users = [
        UserStorageStat(
            user_id=uid,
            username=username_map.get(uid, "unknown"),
            file_count=stats["file_count"],
            total_bytes=stats["total_bytes"],
        )
        for uid, stats in sorted(user_stats.items(), key=lambda x: -x[1]["total_bytes"])
    ]
    total_bytes = sum(s.total_bytes for s in users)
    return StorageStatsResponse(total_bytes=total_bytes, users=users)


@router.delete("/storage/user/{user_id}", status_code=204)
async def clear_user_storage(
    user_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """Delete all uploaded files for a user. Requires admin privileges."""
    user_dir = Path("uploads") / f"user_{user_id}"
    if user_dir.exists():
        shutil.rmtree(user_dir)
    await write_audit(
        db, current_user, "storage.clear_user",
        target_type="user", target_id=user_id,
    )


@router.delete("/storage/orphaned", status_code=204)
async def clean_orphaned_storage(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """Remove conversation upload directories for deleted conversations. Requires admin privileges."""
    conv_uploads = _UPLOADS_CONVERSATIONS_DIR
    if not conv_uploads.exists():
        return

    result = await db.execute(select(Conversation.id))
    existing_ids = {row[0] for row in result.fetchall()}

    deleted_count = 0
    for item in conv_uploads.iterdir():
        if item.is_dir() and item.name not in existing_ids:
            shutil.rmtree(item)
            deleted_count += 1

    await write_audit(
        db, current_user, "storage.cleanup_orphaned",
        detail=f"deleted {deleted_count} orphaned dirs",
    )


# ---------------------------------------------------------------------------
# Feature 7 — Global MCP servers
# ---------------------------------------------------------------------------


def _mcp_to_admin_info(s: MCPServerModel) -> AdminMCPServerInfo:
    return AdminMCPServerInfo(
        id=s.id, name=s.name, description=s.description,
        transport=s.transport, command=s.command, args=s.args,
        url=s.url, is_active=s.is_active, is_global=s.is_global,
        tool_count=s.tool_count,
        created_at=s.created_at.isoformat() if s.created_at else "",
    )


@router.get("/mcp-servers", response_model=list[AdminMCPServerInfo])
async def list_global_mcp_servers(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """List all global MCP servers. Requires admin privileges."""
    result = await db.execute(
        select(MCPServerModel).where(MCPServerModel.is_global == True)  # noqa: E712
    )
    servers = result.scalars().all()
    return [_mcp_to_admin_info(s) for s in servers]


@router.post("/mcp-servers", response_model=AdminMCPServerInfo, status_code=201)
async def create_global_mcp_server(
    body: MCPServerCreate,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """Create a global MCP server. Requires admin privileges."""
    server = MCPServerModel(
        user_id=None,
        is_global=True,
        name=body.name,
        description=body.description,
        transport=body.transport,
        command=body.command,
        args=body.args,
        env=body.env,
        url=body.url,
        working_dir=body.working_dir,
        headers=body.headers,
    )
    db.add(server)
    await db.commit()
    await db.refresh(server)
    await write_audit(
        db, current_user, "mcp_server.create_global",
        target_type="mcp_server", target_id=server.id, target_label=server.name,
    )
    return _mcp_to_admin_info(server)


@router.put("/mcp-servers/{server_id}", response_model=AdminMCPServerInfo)
async def update_global_mcp_server(
    server_id: str,
    body: MCPServerUpdate,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """Update a global MCP server. Requires admin privileges."""
    result = await db.execute(
        select(MCPServerModel).where(
            MCPServerModel.id == server_id,
            MCPServerModel.is_global == True,  # noqa: E712
        )
    )
    server = result.scalar_one_or_none()
    if server is None:
        raise AppError("global_mcp_server_not_found", status_code=404)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(server, field, value)
    await db.commit()
    await db.refresh(server)
    return _mcp_to_admin_info(server)


@router.delete("/mcp-servers/{server_id}", status_code=204)
async def delete_global_mcp_server(
    server_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """Delete a global MCP server. Requires admin privileges."""
    result = await db.execute(
        select(MCPServerModel).where(
            MCPServerModel.id == server_id,
            MCPServerModel.is_global == True,  # noqa: E712
        )
    )
    server = result.scalar_one_or_none()
    if server is None:
        raise AppError("global_mcp_server_not_found", status_code=404)
    await db.delete(server)
    await db.commit()
    await write_audit(
        db, current_user, "mcp_server.delete_global",
        target_type="mcp_server", target_id=server_id, target_label=server.name,
    )


@router.post("/mcp-servers/{server_id}/test")
async def test_global_mcp_server(
    server_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """Test a global MCP server connection. Requires admin privileges."""
    result = await db.execute(
        select(MCPServerModel).where(
            MCPServerModel.id == server_id,
            MCPServerModel.is_global == True,  # noqa: E712
        )
    )
    server = result.scalar_one_or_none()
    if server is None:
        raise AppError("global_mcp_server_not_found", status_code=404)

    try:
        from fim_agent.core.mcp import MCPClient
    except ImportError:
        return {"ok": False, "error": "mcp package not installed"}

    client = MCPClient()
    try:
        if server.transport == "stdio":
            tools = await client.connect_stdio(
                name=server.name,
                command=server.command or "",
                args=server.args or [],
                env=server.env,
                working_dir=server.working_dir,
            )
        elif server.transport == "sse":
            tools = await client.connect_sse(
                name=server.name,
                url=server.url or "",
                headers=server.headers,
            )
        else:
            tools = await client.connect_streamable_http(
                name=server.name,
                url=server.url or "",
                headers=server.headers,
            )

        count = len(tools)
        server.tool_count = count
        await db.commit()
        tool_names = [t.name for t in tools]
        return {"ok": True, "tool_count": count, "tools": tool_names}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        await client.disconnect_all()

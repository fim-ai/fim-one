"""Admin-only API endpoints for system statistics and user management."""

from __future__ import annotations

import csv
import io
import json
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
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from fim_agent.web.email import _smtp_configured
from fim_agent.web.exceptions import AppError
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fim_agent.db import get_session
from fim_agent.web.auth import get_current_admin, hash_password
from fim_agent.web.models import Agent, AuditLog, Connector, ConnectorCallLog, Conversation, InviteCode, KnowledgeBase, MCPServer as MCPServerModel, Message, ModelConfig, SystemSetting, User
from fim_agent.web.schemas.common import PaginatedResponse
from fim_agent.web.schemas.mcp_server import MCPServerCreate, MCPServerUpdate
from fim_agent.web.schemas.model_config import ModelConfigResponse

# ---------------------------------------------------------------------------
# Settings helpers (delegated to admin_utils, re-exported for compatibility)
# ---------------------------------------------------------------------------

from fim_agent.web.api.admin_utils import get_setting, set_setting, write_audit  # noqa: F811,E402
from fim_agent.web.api.files import _load_index, _user_dir

SETTING_REGISTRATION_ENABLED = "registration_enabled"

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
    username: str | None = None
    display_name: str | None
    email: str | None
    is_admin: bool
    is_active: bool
    created_at: str


class UpdateAdminRequest(BaseModel):
    is_admin: bool


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class AdminCreateUserRequest(BaseModel):
    username: str | None = Field(None, min_length=2, max_length=50)
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
    username: str | None = None
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
    username: str | None = None
    created_at: str


class AdminMessageInfo(BaseModel):
    id: str
    role: str
    content: str | None
    created_at: str


class UserStorageStat(BaseModel):
    user_id: str
    username: str | None = None
    file_count: int
    total_bytes: int


class StorageStatsResponse(BaseModel):
    total_bytes: int
    users: list[UserStorageStat]


class AdminFileItem(BaseModel):
    file_id: str
    filename: str
    size: int
    mime_type: str
    stored_name: str


class PaginatedFiles(BaseModel):
    items: list[AdminFileItem]
    total: int
    page: int
    size: int
    pages: int


class IntegrationHealth(BaseModel):
    key: str
    label: str
    configured: bool
    detail: str | None
    impact: str | None = None
    level: str = "optional"  # "required" | "recommended" | "optional"


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
    cloned_from_server_id: str | None = None
    cloned_from_user_id: str | None = None
    cloned_from_username: str | None = None
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
    # Consolidate all aggregate counts into a single query using scalar subqueries
    stats_stmt = select(
        select(func.count()).select_from(User).scalar_subquery().label("total_users"),
        select(func.count()).select_from(Conversation).scalar_subquery().label("total_conversations"),
        select(func.coalesce(func.sum(Conversation.total_tokens), 0)).scalar_subquery().label("total_tokens"),
        select(func.coalesce(func.sum(Conversation.fast_llm_tokens), 0)).scalar_subquery().label("total_fast_llm_tokens"),
        select(func.count()).select_from(Message).scalar_subquery().label("total_messages"),
        select(func.count()).select_from(Agent).scalar_subquery().label("total_agents"),
        select(func.count()).select_from(KnowledgeBase).scalar_subquery().label("total_kbs"),
    )
    stats_result = await db.execute(stats_stmt)
    stats_row = stats_result.one()
    total_users: int = stats_row.total_users
    total_conversations: int = stats_row.total_conversations
    total_tokens: int = stats_row.total_tokens
    total_fast_llm_tokens: int = stats_row.total_fast_llm_tokens
    total_messages: int = stats_row.total_messages
    total_agents: int = stats_row.total_agents
    total_kbs: int = stats_row.total_kbs

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
    # Check username uniqueness (only if provided)
    if body.username is not None:
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
        target_type="user", target_id=user.id, target_label=user.username or user.email,
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
        target_type="user", target_id=user_id, target_label=target_user.username or target_user.email,
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
        target_type="user", target_id=user_id, target_label=target_user.username or target_user.email,
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
        target_type="user", target_id=user_id, target_label=target_user.username or target_user.email,
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
SETTING_EMAIL_VERIFICATION_ENABLED = "email_verification_enabled"
SETTING_DISABLED_BUILTIN_TOOLS = "disabled_builtin_tools"


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
    email_verification_enabled: bool
    smtp_configured: bool
    disabled_builtin_tools: list[str] = []


class UpdateSystemSettingsRequest(BaseModel):
    registration_enabled: bool | None = None
    registration_mode: str | None = None
    maintenance_mode: bool | None = None
    announcement_enabled: bool | None = None
    announcement_text: str | None = None
    default_token_quota: int | None = None
    email_verification_enabled: bool | None = None
    disabled_builtin_tools: list[str] | None = None


async def _load_all_settings(db: AsyncSession) -> SystemSettingsResponse:
    reg = await get_setting(db, SETTING_REGISTRATION_ENABLED, default="true")
    reg_mode = await get_setting(db, SETTING_REGISTRATION_MODE, default="")
    maint = await get_setting(db, SETTING_MAINTENANCE_MODE, default="false")
    ann_en = await get_setting(db, SETTING_ANNOUNCEMENT_ENABLED, default="false")
    ann_txt = await get_setting(db, SETTING_ANNOUNCEMENT_TEXT, default="")
    quota_str = await get_setting(db, SETTING_DEFAULT_TOKEN_QUOTA, default="0")
    email_verif = await get_setting(db, SETTING_EMAIL_VERIFICATION_ENABLED, default="false")
    disabled_tools_raw = await get_setting(db, SETTING_DISABLED_BUILTIN_TOOLS, default="[]")
    try:
        disabled_tools = json.loads(disabled_tools_raw)
        if not isinstance(disabled_tools, list):
            disabled_tools = []
    except (json.JSONDecodeError, TypeError):
        disabled_tools = []
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
        email_verification_enabled=email_verif.lower() == "true",
        smtp_configured=_smtp_configured(),
        disabled_builtin_tools=disabled_tools,
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
    if body.email_verification_enabled is not None:
        if body.email_verification_enabled and not _smtp_configured():
            raise AppError("smtp_not_configured", detail="Cannot enable email verification without SMTP")
        await set_setting(db, SETTING_EMAIL_VERIFICATION_ENABLED, "true" if body.email_verification_enabled else "false")
        changed.append(f"email_verification_enabled={body.email_verification_enabled}")
    if body.disabled_builtin_tools is not None:
        await set_setting(db, SETTING_DISABLED_BUILTIN_TOOLS, json.dumps(body.disabled_builtin_tools))
        changed.append(f"disabled_builtin_tools={body.disabled_builtin_tools}")
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

    result = await db.execute(
        select(User)
        .options(
            selectinload(User.conversations).selectinload(Conversation.messages),
            selectinload(User.agents),
            selectinload(User.knowledge_bases),
            selectinload(User.model_configs),
            selectinload(User.connectors),
            selectinload(User.mcp_servers),
            selectinload(User.oauth_bindings),
        )
        .where(User.id == user_id)
    )
    target_user = result.scalar_one_or_none()
    if target_user is None:
        raise AppError("user_not_found", status_code=404)

    info = _user_to_info(target_user)
    label = target_user.username or target_user.email

    # --- Clean up file-system resources before DB delete ---
    conv_result = await db.execute(
        select(Conversation.id).where(Conversation.user_id == user_id)
    )
    conv_ids = [row[0] for row in conv_result.fetchall()]

    for conv_id in conv_ids:
        sandbox_dir = _CONVERSATIONS_DIR / conv_id
        if sandbox_dir.exists():
            shutil.rmtree(sandbox_dir, ignore_errors=True)
        uploads_dir = _UPLOADS_CONVERSATIONS_DIR / conv_id
        if uploads_dir.exists():
            shutil.rmtree(uploads_dir, ignore_errors=True)

    user_uploads = _UPLOADS_BASE / f"user_{user_id}"
    if user_uploads.exists():
        shutil.rmtree(user_uploads, ignore_errors=True)

    # --- DB delete & audit ---
    await db.delete(target_user)
    await db.commit()
    await write_audit(
        db, current_user, "user.delete",
        target_type="user", target_id=user_id, target_label=label,
        detail=f"deleted user {label}; cleaned {len(conv_ids)} conversations, sandbox & upload dirs",
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
    admin_username: str | None = None
    action: str
    target_type: str | None
    target_id: str | None
    target_label: str | None
    detail: str | None
    created_at: str


def _build_audit_filters(
    action: str | None,
    admin_id: str | None,
    date_from: str | None,
    date_to: str | None,
) -> list:
    """Build SQLAlchemy filter clauses for audit log queries."""
    filters = []
    if action:
        filters.append(AuditLog.action == action)
    if admin_id:
        filters.append(AuditLog.admin_id == admin_id)
    if date_from:
        try:
            dt_from = datetime.strptime(date_from, "%Y-%m-%d").replace(
                hour=0, minute=0, second=0
            )
            filters.append(AuditLog.created_at >= dt_from)
        except ValueError:
            pass  # ignore invalid date strings
    if date_to:
        try:
            dt_to = datetime.strptime(date_to, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59
            )
            filters.append(AuditLog.created_at <= dt_to)
        except ValueError:
            pass  # ignore invalid date strings
    return filters


@router.get("/audit-log/export")
async def export_audit_log(
    action: str | None = None,
    admin_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> StreamingResponse:
    """Export filtered audit log entries as CSV. Limited to 10 000 rows."""
    filters = _build_audit_filters(action, admin_id, date_from, date_to)
    query = select(AuditLog).order_by(AuditLog.created_at.desc())
    for f in filters:
        query = query.where(f)
    query = query.limit(10_000)

    result = await db.execute(query)
    rows = result.scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["timestamp", "admin", "action", "target_type", "target_label", "detail"])
    for r in rows:
        writer.writerow([
            r.created_at.isoformat() if r.created_at else "",
            r.admin_username or "",
            r.action,
            r.target_type or "",
            r.target_label or "",
            r.detail or "",
        ])

    buf.seek(0)
    today = date.today().isoformat()
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="audit-log-{today}.csv"'},
    )


@router.get("/audit-log", response_model=PaginatedResponse)
async def list_audit_log(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    action: str | None = None,
    admin_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    """Return paginated audit log entries, newest first. Requires admin privileges."""
    filters = _build_audit_filters(action, admin_id, date_from, date_to)

    count_query = select(func.count()).select_from(AuditLog)
    for f in filters:
        count_query = count_query.where(f)
    total_result = await db.execute(count_query)
    total: int = total_result.scalar_one()

    data_query = (
        select(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    for f in filters:
        data_query = data_query.where(f)

    rows_result = await db.execute(data_query)
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
        target_type="user", target_id=user_id, target_label=user.username or user.email,
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
        target_type="user", target_id=user_id, target_label=user.username or user.email,
        detail=f"quota={body.token_quota}",
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Feature 2 — API health / integration status
# ---------------------------------------------------------------------------


@router.get("/system/health", response_model=list[IntegrationHealth])
async def get_system_health(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """Return configuration status for key integrations. Requires admin privileges."""
    from urllib.parse import urlparse

    def _host(url: str) -> str:
        """Extract hostname from a URL for display."""
        try:
            return urlparse(url).hostname or url
        except Exception:
            return url

    # Check DB for admin-configured models (system-level, user_id=NULL)
    from sqlalchemy import select as sa_select  # noqa: PLC0415
    from fim_agent.web.models.model_config import ModelConfig as ModelConfigORM  # noqa: PLC0415

    _db_result = await db.execute(
        sa_select(ModelConfigORM.role).where(
            ModelConfigORM.user_id == None,  # noqa: E711
            ModelConfigORM.is_active == True,  # noqa: E712
            ModelConfigORM.api_key != None,  # noqa: E711
            ModelConfigORM.api_key != "",
            ModelConfigORM.role.in_(["general", "fast"]),
        )
    )
    _db_roles = {r[0] for r in _db_result.all()}
    has_db_general = "general" in _db_roles
    has_db_fast = "fast" in _db_roles

    checks: list[IntegrationHealth] = []

    # ── Infrastructure ────────────────────────────────────────────────
    db_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./data/fim_agent.db")
    is_postgres = "postgresql" in db_url or "asyncpg" in db_url
    db_type = "PostgreSQL" if is_postgres else "SQLite"
    try:
        if is_postgres:
            _ver_result = await db.execute(text("SELECT version()"))
            _ver = _ver_result.scalar()
            db_version = " ".join(_ver.split()[:2]) if _ver else "PostgreSQL"
        else:
            _ver_result = await db.execute(text("SELECT sqlite_version()"))
            _ver = _ver_result.scalar()
            db_version = f"SQLite {_ver}" if _ver else "SQLite"
    except Exception:
        db_version = db_type
    checks.append(IntegrationHealth(
        key="database",
        label="Database",
        configured=True,
        detail=f"{db_version} · {_host(db_url) if is_postgres else 'File-based'}",
        impact=None if is_postgres else "SQLite is single-writer; use PostgreSQL for high-concurrency deployments",
        level="recommended",
    ))

    redis_url = os.environ.get("REDIS_URL", "")
    checks.append(IntegrationHealth(
        key="redis",
        label="Redis",
        configured=bool(redis_url),
        detail=_host(redis_url) if redis_url else None,
        impact=None if redis_url else "Without Redis, mid-stream interrupt/inject does not work across workers (WORKERS>1)",
        level="optional",
    ))

    # ── AI Models ────────────────────────────────────────────────────────
    llm_model = os.environ.get("LLM_MODEL", "").strip('"')
    llm_key = os.environ.get("LLM_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    llm_base = os.environ.get("LLM_BASE_URL", "")
    llm_configured = bool(llm_model and llm_key) or has_db_general
    reasoning_effort = os.environ.get("LLM_REASONING_EFFORT", "").strip().lower()
    reasoning_label = f"Reasoning: {reasoning_effort}" if reasoning_effort in ("low", "medium", "high") else None
    llm_detail_parts = [p for p in [llm_model, _host(llm_base) if llm_base else None, reasoning_label] if p]
    if has_db_general and not (llm_model and llm_key):
        llm_detail = "Admin Models"
        if reasoning_label:
            llm_detail += f" · {reasoning_label}"
    else:
        llm_detail = " · ".join(llm_detail_parts) if llm_detail_parts else None
    checks.append(IntegrationHealth(
        key="llm", label="Main LLM",
        configured=llm_configured,
        detail=llm_detail,
        impact=None if llm_configured else "Chat will not work — configure via Admin → Models or set LLM_API_KEY",
        level="required",
    ))

    fast_model = os.environ.get("FAST_LLM_MODEL", "").strip('"')
    fast_configured = bool(fast_model) or has_db_fast
    fast_parts = [p for p in [fast_model, _host(llm_base) if llm_base else None] if p]
    if has_db_fast and not fast_model:
        fast_detail = "Admin Models"
    else:
        fast_detail = " · ".join(fast_parts) if fast_parts else None
    checks.append(IntegrationHealth(
        key="fast_llm", label="Fast LLM",
        configured=fast_configured,
        detail=fast_detail,
        impact=None if fast_configured else "Falls back to main LLM; dedicated fast model improves speed and cost",
        level="recommended",
    ))

    # ── Retrieval ────────────────────────────────────────────────────────
    jina_key = os.environ.get("JINA_API_KEY", "")
    emb_model = os.environ.get("EMBEDDING_MODEL", "")
    emb_base = os.environ.get("EMBEDDING_BASE_URL", "")
    emb_configured = bool(emb_model) or bool(jina_key)
    emb_parts = [p for p in [
        emb_model,
        _host(emb_base) if emb_base else ("jina" if jina_key else None),
    ] if p]
    checks.append(IntegrationHealth(
        key="embedding", label="Embedding",
        configured=emb_configured,
        detail=" · ".join(emb_parts) if emb_parts else None,
        impact=None if emb_configured else "Knowledge base document ingestion and retrieval unavailable",
        level="recommended",
    ))

    reranker_provider = os.environ.get("RERANKER_PROVIDER", "")
    reranker_model = os.environ.get("RERANKER_MODEL", "")
    reranker_configured = bool(reranker_provider) or bool(jina_key)
    reranker_parts = [p for p in [
        reranker_model,
        reranker_provider or ("jina" if jina_key else None),
    ] if p]
    checks.append(IntegrationHealth(
        key="reranker", label="Reranker",
        configured=reranker_configured,
        detail=" · ".join(reranker_parts) if reranker_parts else None,
        impact=None if reranker_configured else "Search results use fusion scoring only; reranker improves precision",
        level="optional",
    ))

    # ── Web Tools ────────────────────────────────────────────────────────
    search_provider = os.environ.get("WEB_SEARCH_PROVIDER", "jina")
    search_key = (
        os.environ.get("JINA_API_KEY", "")
        or os.environ.get("TAVILY_API_KEY", "")
        or os.environ.get("BRAVE_API_KEY", "")
    )
    checks.append(IntegrationHealth(
        key="web_search", label="Web Search",
        configured=True,
        detail=search_provider + (" (no API key)" if not search_key else ""),
        level="optional",
    ))

    fetch_provider = os.environ.get("WEB_FETCH_PROVIDER", "")
    fetch_detail = fetch_provider if fetch_provider else ("jina" if jina_key else "httpx")
    checks.append(IntegrationHealth(
        key="web_fetch", label="Web Fetch",
        configured=True,
        detail=fetch_detail,
        level="optional",
    ))

    # ── Image Generation ─────────────────────────────────────────────────
    image_key = os.environ.get("IMAGE_GEN_API_KEY", "")
    image_provider = os.environ.get("IMAGE_GEN_PROVIDER", "google").lower()
    image_model = os.environ.get("IMAGE_GEN_MODEL", "gemini-3.1-flash-image-preview")
    image_base = os.environ.get("IMAGE_GEN_BASE_URL", "")
    image_configured = bool(image_key)
    image_parts = [p for p in [
        image_provider,
        image_model,
        _host(image_base) if image_base else None,
    ] if p]
    checks.append(IntegrationHealth(
        key="image_gen", label="Image Generation",
        configured=image_configured,
        detail=" · ".join(image_parts) if image_configured else None,
        impact=None if image_configured else "Image generation tool will not be available to agents",
        level="optional",
    ))

    # ── Email ────────────────────────────────────────────────────────────
    smtp_ok = _smtp_configured()
    smtp_host = os.environ.get("SMTP_HOST", "")
    smtp_from = os.environ.get("SMTP_FROM", "") or os.environ.get("SMTP_USER", "")
    smtp_parts = [p for p in [smtp_host, smtp_from] if p]
    checks.append(IntegrationHealth(
        key="smtp", label="SMTP (Email)",
        configured=smtp_ok,
        detail=" · ".join(smtp_parts) if smtp_ok and smtp_parts else None,
        impact=None if smtp_ok else "Email code login, email verification, and forgot password unavailable",
        level="recommended",
    ))

    # ── OAuth ────────────────────────────────────────────────────────────
    from fim_agent.web.oauth import get_configured_providers  # noqa: PLC0415
    oauth_providers = get_configured_providers()
    feishu_ok = "feishu" in oauth_providers
    feishu_app_id = os.environ.get("FEISHU_APP_ID", "")
    checks.append(IntegrationHealth(
        key="oauth_feishu", label="Feishu OAuth",
        configured=feishu_ok,
        detail=feishu_app_id if feishu_ok else None,
        impact=None if feishu_ok else "Feishu login button will not appear",
        level="optional",
    ))

    github_ok = "github" in oauth_providers
    github_client_id = os.environ.get("GITHUB_CLIENT_ID", "")
    checks.append(IntegrationHealth(
        key="oauth_github", label="GitHub OAuth",
        configured=github_ok,
        detail=github_client_id if github_ok else None,
        impact=None if github_ok else "GitHub login button will not appear",
        level="optional",
    ))

    google_ok = "google" in oauth_providers
    google_client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    checks.append(IntegrationHealth(
        key="oauth_google", label="Google OAuth",
        configured=google_ok,
        detail=google_client_id if google_ok else None,
        impact=None if google_ok else "Google login button will not appear",
        level="optional",
    ))

    discord_ok = "discord" in oauth_providers
    discord_client_id = os.environ.get("DISCORD_CLIENT_ID", "")
    checks.append(IntegrationHealth(
        key="oauth_discord", label="Discord OAuth",
        configured=discord_ok,
        detail=discord_client_id if discord_ok else None,
        impact=None if discord_ok else "Discord login button will not appear",
        level="optional",
    ))

    return checks


# ---------------------------------------------------------------------------
# Feature 3 — Conversation moderation
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_uploads_base = Path(os.environ.get("UPLOADS_DIR", "uploads"))
_UPLOADS_BASE = _uploads_base if _uploads_base.is_absolute() else _PROJECT_ROOT / _uploads_base
_UPLOADS_CONVERSATIONS_DIR = _UPLOADS_BASE / "conversations"
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
        stmt = stmt.where(or_(Conversation.title.ilike(like), User.username.ilike(like), User.email.ilike(like)))

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
            username=user.username or user.email,
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


@router.get("/conversations/{conv_id}/messages", response_model=list[AdminMessageInfo])
async def admin_get_conversation_messages(
    conv_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """Fetch all messages of a conversation for admin inspection. Read-only."""
    # Verify conversation exists
    conv_result = await db.execute(
        select(Conversation).where(Conversation.id == conv_id)
    )
    conv = conv_result.scalar_one_or_none()
    if conv is None:
        raise AppError("conversation_not_found", status_code=404)

    # Fetch messages ordered by created_at ASC
    msg_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv_id)
        .order_by(Message.created_at.asc())
    )
    messages = msg_result.scalars().all()

    # Audit log
    await write_audit(
        db, current_user, "conversation.viewed",
        target_type="conversation", target_id=conv_id,
        target_label=conv.title or None,
    )

    return [
        AdminMessageInfo(
            id=msg.id,
            role=msg.role,
            content=msg.content,
            created_at=msg.created_at.isoformat() if msg.created_at else "",
        )
        for msg in messages
    ]


@router.delete("/conversations/{conv_id}", status_code=204)
async def admin_delete_conversation(
    conv_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """Delete any conversation by ID. Requires admin privileges."""
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

    # Resolve usernames (with email fallback)
    if user_stats:
        user_rows = await db.execute(
            select(User.id, User.username, User.email).where(User.id.in_(list(user_stats.keys())))
        )
        username_map = {row[0]: (row[1] or row[2]) for row in user_rows.all()}
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


@router.get("/storage/user/{user_id}/files")
async def list_user_files(
    user_id: str,
    page: int = Query(1, ge=1),  # noqa: B008
    size: int = Query(50, ge=1, le=200),  # noqa: B008
    current_user: User = Depends(get_current_admin),  # noqa: B008
) -> PaginatedFiles:
    """List files uploaded by a specific user (paginated)."""
    index = _load_index(user_id)
    all_items = [
        AdminFileItem(
            file_id=fid,
            filename=meta["filename"],
            size=meta["size"],
            mime_type=meta.get("mime_type", "application/octet-stream"),
            stored_name=meta["stored_name"],
        )
        for fid, meta in index.items()
    ]
    total = len(all_items)
    pages = math.ceil(total / size) if total else 0
    start = (page - 1) * size
    return PaginatedFiles(
        items=all_items[start : start + size],
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


@router.get("/storage/user/{user_id}/files/{file_id}")
async def download_user_file(
    user_id: str,
    file_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
) -> FileResponse:
    """Download a specific file uploaded by a user."""
    index = _load_index(user_id)
    meta = index.get(file_id)
    if meta is None:
        raise AppError("file_not_found", status_code=404)
    file_path = _user_dir(user_id) / meta["stored_name"]
    if not file_path.exists():
        raise AppError("file_not_found", status_code=404)
    return FileResponse(
        path=str(file_path),
        filename=meta["filename"],
        media_type="application/octet-stream",
    )


# ---------------------------------------------------------------------------
# Feature 7 — Global MCP servers
# ---------------------------------------------------------------------------


def _mcp_to_admin_info(
    s: MCPServerModel,
    *,
    cloned_from_username: str | None = None,
) -> AdminMCPServerInfo:
    return AdminMCPServerInfo(
        id=s.id, name=s.name, description=s.description,
        transport=s.transport, command=s.command, args=s.args,
        url=s.url, is_active=s.is_active, is_global=s.is_global,
        tool_count=s.tool_count,
        cloned_from_server_id=s.cloned_from_server_id,
        cloned_from_user_id=s.cloned_from_user_id,
        cloned_from_username=cloned_from_username,
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

    # Batch-resolve cloned_from usernames
    clone_user_ids = {s.cloned_from_user_id for s in servers if s.cloned_from_user_id}
    username_map: dict[str, str] = {}
    if clone_user_ids:
        user_rows = (
            await db.execute(
                select(User.id, User.username).where(User.id.in_(clone_user_ids))
            )
        ).all()
        username_map = {uid: uname for uid, uname in user_rows}

    return [
        _mcp_to_admin_info(
            s, cloned_from_username=username_map.get(s.cloned_from_user_id or "")
        )
        for s in servers
    ]


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
    await write_audit(
        db, current_user, "mcp_server.update_global",
        target_type="mcp_server", target_id=server.id, target_label=server.name,
    )
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


@router.post(
    "/mcp-servers/clone/{server_id}",
    response_model=AdminMCPServerInfo,
    status_code=201,
)
async def clone_mcp_server_to_global(
    server_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """Clone a user MCP server to a global MCP server. Requires admin privileges."""
    result = await db.execute(
        select(MCPServerModel).where(MCPServerModel.id == server_id)
    )
    source = result.scalar_one_or_none()
    if source is None:
        raise AppError("mcp_server_not_found", status_code=404)

    server = MCPServerModel(
        user_id=None,
        is_global=True,
        name=source.name,
        description=source.description,
        transport=source.transport,
        command=source.command,
        args=source.args,
        env=source.env,
        url=source.url,
        working_dir=source.working_dir,
        headers=source.headers,
        is_active=True,
        tool_count=source.tool_count,
        cloned_from_server_id=source.id,
        cloned_from_user_id=source.user_id,
    )
    db.add(server)
    await db.commit()
    await db.refresh(server)

    # Resolve cloned_from username
    cloned_from_username: str | None = None
    if source.user_id:
        uname_result = await db.execute(
            select(User.username).where(User.id == source.user_id)
        )
        cloned_from_username = uname_result.scalar_one_or_none()

    await write_audit(
        db, current_user, "mcp_server.clone_to_global",
        target_type="mcp_server", target_id=server.id, target_label=server.name,
        detail=f"Cloned from server {source.id} (user {source.user_id})",
    )

    return _mcp_to_admin_info(server, cloned_from_username=cloned_from_username)


class AdminAllMCPServerInfo(BaseModel):
    """MCP server info with owner details, for the admin browse-all view."""
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
    user_id: str | None
    username: str | None = None
    email: str | None = None
    created_at: str


@router.get("/all-mcp-servers", response_model=PaginatedResponse)
async def list_all_mcp_servers(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """List all MCP servers across all users (for clone picker). Requires admin."""
    stmt = (
        select(MCPServerModel, User)
        .outerjoin(User, MCPServerModel.user_id == User.id)
    )
    count_base = select(MCPServerModel)

    if q:
        pattern = f"%{q}%"
        filter_clause = MCPServerModel.name.ilike(pattern)
        stmt = stmt.where(filter_clause)
        count_base = count_base.where(filter_clause)

    count_stmt = select(func.count()).select_from(count_base.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    rows = (
        await db.execute(
            stmt.order_by(MCPServerModel.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()

    items = [
        AdminAllMCPServerInfo(
            id=server.id,
            name=server.name,
            description=server.description,
            transport=server.transport,
            command=server.command,
            args=server.args,
            url=server.url,
            is_active=server.is_active,
            is_global=server.is_global,
            tool_count=server.tool_count,
            user_id=server.user_id,
            username=user.username if user else None,
            email=user.email if user else None,
            created_at=server.created_at.isoformat() if server.created_at else "",
        ).model_dump()
        for server, user in rows
    ]

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total > 0 else 1,
    )


# ---------------------------------------------------------------------------
# Admin Model Management
# ---------------------------------------------------------------------------


class AdminModelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    provider: str = ""
    model_name: str = Field(min_length=1, max_length=100)
    base_url: str | None = None
    api_key: str | None = None
    category: str = "llm"
    temperature: float | None = None
    max_output_tokens: int | None = None
    context_size: int | None = None
    role: str | None = None
    is_active: bool = True


class AdminModelUpdate(BaseModel):
    name: str | None = None
    provider: str | None = None
    model_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    category: str | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    context_size: int | None = None
    role: str | None = None
    is_active: bool | None = None


class AdminToggleActiveModelRequest(BaseModel):
    is_active: bool


class AdminSetModelRoleRequest(BaseModel):
    role: str | None = None


class EnvFallbackInfo(BaseModel):
    llm_model: str
    llm_base_url: str
    llm_temperature: float
    llm_context_size: int
    llm_max_output_tokens: int
    fast_llm_model: str
    fast_llm_context_size: int
    fast_llm_max_output_tokens: int
    has_api_key: bool


class AdminModelsListResponse(BaseModel):
    models: list[ModelConfigResponse]
    env_fallback: EnvFallbackInfo


def _model_config_to_response(cfg: ModelConfig) -> ModelConfigResponse:
    """Convert a ModelConfig ORM object to a response schema."""
    return ModelConfigResponse(
        id=cfg.id,
        name=cfg.name,
        provider=cfg.provider,
        model_name=cfg.model_name,
        base_url=cfg.base_url,
        category=cfg.category,
        temperature=cfg.temperature,
        max_output_tokens=getattr(cfg, "max_output_tokens", None),
        context_size=getattr(cfg, "context_size", None),
        role=getattr(cfg, "role", None),
        is_default=cfg.is_default,
        is_active=cfg.is_active,
        created_at=cfg.created_at.isoformat() if cfg.created_at else "",
        updated_at=cfg.updated_at.isoformat() if cfg.updated_at else None,
    )


def _build_env_fallback() -> EnvFallbackInfo:
    """Build environment variable fallback information."""
    llm_model = os.getenv("LLM_MODEL", "gpt-4o")
    llm_context_size = int(os.getenv("LLM_CONTEXT_SIZE", "128000"))
    llm_max_output_tokens = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "64000"))
    return EnvFallbackInfo(
        llm_model=llm_model,
        llm_base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
        llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.7")),
        llm_context_size=llm_context_size,
        llm_max_output_tokens=llm_max_output_tokens,
        fast_llm_model=os.getenv("FAST_LLM_MODEL") or llm_model,
        fast_llm_context_size=int(
            os.getenv("FAST_LLM_CONTEXT_SIZE") or str(llm_context_size)
        ),
        fast_llm_max_output_tokens=int(
            os.getenv("FAST_LLM_MAX_OUTPUT_TOKENS") or str(llm_max_output_tokens)
        ),
        has_api_key=bool(os.getenv("LLM_API_KEY")),
    )


async def _admin_unset_role(
    db: AsyncSession,
    role: str,
    exclude_id: str | None = None,
) -> None:
    """Ensure only one system-level config holds a given role."""
    stmt = select(ModelConfig).where(
        ModelConfig.role == role,
        ModelConfig.user_id.is_(None),
    )
    if exclude_id:
        stmt = stmt.where(ModelConfig.id != exclude_id)
    result = await db.execute(stmt)
    for cfg in result.scalars().all():
        cfg.role = None


@router.get("/models", response_model=AdminModelsListResponse)
async def admin_list_models(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> AdminModelsListResponse:
    """List all system-level model configurations with ENV fallback info."""
    from sqlalchemy import case

    # Order: general first, fast second, then by created_at
    role_order = case(
        (ModelConfig.role == "general", 0),
        (ModelConfig.role == "fast", 1),
        else_=2,
    )
    result = await db.execute(
        select(ModelConfig)
        .where(ModelConfig.user_id.is_(None))
        .order_by(role_order, ModelConfig.created_at.asc())
    )
    configs = result.scalars().all()
    return AdminModelsListResponse(
        models=[_model_config_to_response(c) for c in configs],
        env_fallback=_build_env_fallback(),
    )


@router.post("/models", response_model=ModelConfigResponse, status_code=201)
async def admin_create_model(
    body: AdminModelCreate,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ModelConfigResponse:
    """Create a system-level model configuration."""
    if body.role in ("general", "fast"):
        await _admin_unset_role(db, body.role)

    cfg = ModelConfig(
        user_id=None,
        name=body.name,
        provider=body.provider,
        model_name=body.model_name,
        base_url=body.base_url,
        api_key=body.api_key,
        category=body.category,
        temperature=body.temperature,
        max_output_tokens=body.max_output_tokens,
        context_size=body.context_size,
        role=body.role,
        is_active=body.is_active,
    )
    db.add(cfg)
    await db.commit()
    await db.refresh(cfg)
    await write_audit(
        db,
        current_user,
        "model.create",
        target_type="model",
        target_id=cfg.id,
        target_label=cfg.name,
        detail=f"provider={cfg.provider}, model={cfg.model_name}, role={cfg.role}",
    )
    return _model_config_to_response(cfg)


@router.put("/models/{model_id}", response_model=ModelConfigResponse)
async def admin_update_model(
    model_id: str,
    body: AdminModelUpdate,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ModelConfigResponse:
    """Update a system-level model configuration."""
    result = await db.execute(
        select(ModelConfig).where(
            ModelConfig.id == model_id,
            ModelConfig.user_id.is_(None),
        )
    )
    cfg = result.scalar_one_or_none()
    if cfg is None:
        raise AppError("admin_model_not_found", status_code=404)

    update_data = body.model_dump(exclude_unset=True)

    new_role = update_data.get("role")
    if new_role in ("general", "fast"):
        await _admin_unset_role(db, new_role, exclude_id=cfg.id)

    for field, value in update_data.items():
        setattr(cfg, field, value)

    await db.commit()
    await db.refresh(cfg)
    await write_audit(
        db,
        current_user,
        "model.update",
        target_type="model",
        target_id=cfg.id,
        target_label=cfg.name,
    )
    return _model_config_to_response(cfg)


@router.delete("/models/{model_id}", status_code=204)
async def admin_delete_model(
    model_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """Delete a system-level model configuration."""
    result = await db.execute(
        select(ModelConfig).where(
            ModelConfig.id == model_id,
            ModelConfig.user_id.is_(None),
        )
    )
    cfg = result.scalar_one_or_none()
    if cfg is None:
        raise AppError("admin_model_not_found", status_code=404)

    name = cfg.name
    await db.delete(cfg)
    await db.commit()
    await write_audit(
        db,
        current_user,
        "model.delete",
        target_type="model",
        target_id=model_id,
        target_label=name,
    )


@router.patch("/models/{model_id}/active")
async def admin_toggle_model_active(
    model_id: str,
    body: AdminToggleActiveModelRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict:
    """Toggle the active status of a system-level model configuration."""
    result = await db.execute(
        select(ModelConfig).where(
            ModelConfig.id == model_id,
            ModelConfig.user_id.is_(None),
        )
    )
    cfg = result.scalar_one_or_none()
    if cfg is None:
        raise AppError("admin_model_not_found", status_code=404)

    cfg.is_active = body.is_active
    await db.commit()
    action = "model.enable" if body.is_active else "model.disable"
    await write_audit(
        db,
        current_user,
        action,
        target_type="model",
        target_id=model_id,
        target_label=cfg.name,
    )
    return {"ok": True}


@router.patch("/models/{model_id}/role")
async def admin_set_model_role(
    model_id: str,
    body: AdminSetModelRoleRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict:
    """Set or clear the role for a system-level model configuration."""
    result = await db.execute(
        select(ModelConfig).where(
            ModelConfig.id == model_id,
            ModelConfig.user_id.is_(None),
        )
    )
    cfg = result.scalar_one_or_none()
    if cfg is None:
        raise AppError("admin_model_not_found", status_code=404)

    if body.role and body.role not in ("general", "fast"):
        raise AppError("invalid_model_role", status_code=400)

    if body.role:
        await _admin_unset_role(db, body.role, exclude_id=cfg.id)

    cfg.role = body.role
    await db.commit()
    await write_audit(
        db,
        current_user,
        "model.set_role",
        target_type="model",
        target_id=model_id,
        target_label=cfg.name,
        detail=f"role={body.role}",
    )
    return {"ok": True}

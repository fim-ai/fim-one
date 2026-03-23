"""Personal settings API endpoints: /api/me/

Provides CRUD for the authenticated user's API keys, sessions, credentials,
usage statistics, notification preferences, and Market subscriptions.
"""

from __future__ import annotations

import hashlib
import math
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.auth import get_current_user
from fim_one.web.exceptions import AppError
from fim_one.web.models import (
    ApiKey,
    ConnectorCredential,
    LoginHistory,
    MCPServerCredential,
    NotificationPreference,
    ResourceSubscription,
    User,
)
from fim_one.web.models.agent import Agent
from fim_one.web.models.connector import Connector
from fim_one.web.models.conversation import Conversation
from fim_one.web.models.mcp_server import MCPServer
from fim_one.web.schemas.user_settings import (
    AgentUsage,
    ConnectorCredentialInfo,
    CredentialsResponse,
    DailyUsage,
    McpCredentialInfo,
    NotificationPrefBulkRequest,
    NotificationPrefInfo,
    NotificationPrefListResponse,
    PaginatedUserApiKeyResponse,
    SessionInfo,
    SessionListResponse,
    SubscriptionInfo,
    SubscriptionListResponse,
    UsageResponse,
    UserApiKeyCreateRequest,
    UserApiKeyCreateResponse,
    UserApiKeyInfo,
    UserApiKeyToggleRequest,
)

router = APIRouter(prefix="/api/me", tags=["user-settings"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_api_key() -> str:
    """Generate a random API key: ``fim_`` + 44 URL-safe alphanumeric chars."""
    return "fim_" + secrets.token_urlsafe(33)[:44]


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


# ---------------------------------------------------------------------------
# A) Personal API Keys
# ---------------------------------------------------------------------------


@router.get("/api-keys", response_model=PaginatedUserApiKeyResponse)
async def list_my_api_keys(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedUserApiKeyResponse:
    """List the authenticated user's own API keys (paginated)."""
    base = select(ApiKey).where(ApiKey.user_id == current_user.id)
    count_q = select(func.count()).select_from(ApiKey).where(ApiKey.user_id == current_user.id)

    total = (await db.execute(count_q)).scalar() or 0
    result = await db.execute(
        base.order_by(ApiKey.created_at.desc()).offset((page - 1) * size).limit(size)
    )
    rows = result.scalars().all()

    items = [
        UserApiKeyInfo(
            id=row.id,
            name=row.name,
            key_prefix=row.key_prefix,
            scopes=row.scopes,
            is_active=row.is_active,
            expires_at=row.expires_at,
            last_used_at=row.last_used_at,
            total_requests=row.total_requests,
            created_at=row.created_at.isoformat() if row.created_at else "",
        )
        for row in rows
    ]
    return PaginatedUserApiKeyResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total > 0 else 1,
    )


@router.post("/api-keys", response_model=UserApiKeyCreateResponse, status_code=201)
async def create_my_api_key(
    body: UserApiKeyCreateRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> UserApiKeyCreateResponse:
    """Create a new personal API key. The full key is returned only once."""
    raw_key = _generate_api_key()
    key_prefix = raw_key[:8]
    key_hash = _hash_key(raw_key)

    api_key = ApiKey(
        name=body.name,
        user_id=current_user.id,
        key_prefix=key_prefix,
        key_hash=key_hash,
        scopes=body.scopes,
        expires_at=body.expires_at,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    return UserApiKeyCreateResponse(
        id=api_key.id,
        name=api_key.name,
        key=raw_key,
        key_prefix=key_prefix,
        scopes=api_key.scopes,
        expires_at=api_key.expires_at,
        created_at=api_key.created_at.isoformat() if api_key.created_at else "",
    )


@router.patch("/api-keys/{key_id}/active", response_model=UserApiKeyInfo)
async def toggle_my_api_key_active(
    key_id: str,
    body: UserApiKeyToggleRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> UserApiKeyInfo:
    """Enable or disable one of the user's own API keys."""
    result = await db.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == current_user.id)
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise AppError("api_key_not_found", status_code=404)

    api_key.is_active = body.is_active
    await db.commit()
    await db.refresh(api_key)

    return UserApiKeyInfo(
        id=api_key.id,
        name=api_key.name,
        key_prefix=api_key.key_prefix,
        scopes=api_key.scopes,
        is_active=api_key.is_active,
        expires_at=api_key.expires_at,
        last_used_at=api_key.last_used_at,
        total_requests=api_key.total_requests,
        created_at=api_key.created_at.isoformat() if api_key.created_at else "",
    )


@router.delete("/api-keys/{key_id}", status_code=204)
async def delete_my_api_key(
    key_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """Permanently delete one of the user's own API keys."""
    result = await db.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == current_user.id)
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise AppError("api_key_not_found", status_code=404)

    await db.delete(api_key)
    await db.commit()


# ---------------------------------------------------------------------------
# B) Sessions
# ---------------------------------------------------------------------------


@router.get("/sessions", response_model=SessionListResponse)
async def list_my_sessions(
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> SessionListResponse:
    """List the user's login history (most recent first, limit 50)."""
    query = (
        select(LoginHistory)
        .where(LoginHistory.user_id == current_user.id)
        .order_by(LoginHistory.created_at.desc())
        .limit(50)
    )
    result = await db.execute(query)
    rows = result.scalars().all()

    items = [
        SessionInfo(
            id=row.id,
            ip_address=row.ip_address,
            user_agent=row.user_agent,
            success=row.success,
            failure_reason=row.failure_reason,
            created_at=row.created_at.isoformat() if row.created_at else "",
        )
        for row in rows
    ]
    return SessionListResponse(items=items, total=len(items))


@router.post("/sessions/revoke-all")
async def revoke_all_sessions(
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, str]:
    """Invalidate all refresh tokens by setting tokens_invalidated_at = now()."""
    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one()
    user.tokens_invalidated_at = datetime.now(UTC)
    await db.commit()
    return {"message": "All sessions revoked"}


# ---------------------------------------------------------------------------
# C) My Credentials (read-only aggregation)
# ---------------------------------------------------------------------------


@router.get("/credentials", response_model=CredentialsResponse)
async def list_my_credentials(
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> CredentialsResponse:
    """List the user's connector and MCP server credentials (no secrets)."""
    # Join with Connector to get the connector name
    cc_result = await db.execute(
        select(ConnectorCredential, Connector.name)
        .outerjoin(Connector, ConnectorCredential.connector_id == Connector.id)
        .where(ConnectorCredential.user_id == current_user.id)
    )
    cc_rows = cc_result.all()

    # Join with MCPServer to get the server name
    mcp_result = await db.execute(
        select(MCPServerCredential, MCPServer.name)
        .outerjoin(MCPServer, MCPServerCredential.server_id == MCPServer.id)
        .where(MCPServerCredential.user_id == current_user.id)
    )
    mcp_rows = mcp_result.all()

    return CredentialsResponse(
        connector_credentials=[
            ConnectorCredentialInfo(
                id=c.id,
                name=name or "Unknown Connector",
                resource_id=c.connector_id,
                created_at=c.created_at.isoformat() if c.created_at else None,
                updated_at=c.updated_at.isoformat() if c.updated_at else None,
            )
            for c, name in cc_rows
        ],
        mcp_credentials=[
            McpCredentialInfo(
                id=m.id,
                name=name or "Unknown MCP Server",
                resource_id=m.server_id,
                created_at=m.created_at.isoformat() if m.created_at else None,
                updated_at=m.updated_at.isoformat() if m.updated_at else None,
            )
            for m, name in mcp_rows
        ],
    )


# ---------------------------------------------------------------------------
# D) Usage
# ---------------------------------------------------------------------------


def _parse_period(period: str) -> int:
    """Convert period string to number of days."""
    mapping = {"7d": 7, "30d": 30, "90d": 90}
    return mapping.get(period, 7)


@router.get("/usage", response_model=UsageResponse)
async def get_my_usage(
    period: str = Query("7d", pattern=r"^(7d|30d|90d|month)$"),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> UsageResponse:
    """Get token usage statistics for the authenticated user."""
    if period == "month":
        now = datetime.now(UTC)
        since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        days = _parse_period(period)
        since = datetime.now(UTC) - timedelta(days=days)

    # Total tokens in period
    total_q = (
        select(func.coalesce(func.sum(Conversation.total_tokens), 0))
        .where(Conversation.user_id == current_user.id, Conversation.created_at >= since)
    )
    total_tokens = (await db.execute(total_q)).scalar() or 0

    # Daily breakdown — use func.date() for cross-DB date extraction
    daily_q = (
        select(
            func.date(Conversation.created_at).label("day"),
            func.coalesce(func.sum(Conversation.total_tokens), 0).label("tokens"),
        )
        .where(Conversation.user_id == current_user.id, Conversation.created_at >= since)
        .group_by(func.date(Conversation.created_at))
        .order_by(func.date(Conversation.created_at))
    )
    daily_rows = (await db.execute(daily_q)).all()
    daily = [DailyUsage(date=str(r.day), tokens=int(r.tokens)) for r in daily_rows]

    # By agent breakdown
    agent_name_expr = case(
        (Agent.name.isnot(None), Agent.name),
        else_="Direct Chat",
    )
    agent_q = (
        select(
            Conversation.agent_id,
            agent_name_expr.label("agent_name"),
            func.coalesce(func.sum(Conversation.total_tokens), 0).label("tokens"),
        )
        .outerjoin(Agent, Conversation.agent_id == Agent.id)
        .where(Conversation.user_id == current_user.id, Conversation.created_at >= since)
        .group_by(Conversation.agent_id, agent_name_expr)
        .order_by(func.sum(Conversation.total_tokens).desc())
    )
    agent_rows = (await db.execute(agent_q)).all()
    by_agent = [
        AgentUsage(
            agent_id=r.agent_id,
            agent_name=r.agent_name or "Direct Chat",
            tokens=int(r.tokens),
        )
        for r in agent_rows
    ]

    # Quota info
    result = await db.execute(select(User.token_quota).where(User.id == current_user.id))
    quota = result.scalar()
    quota_used_pct = None
    if quota is not None and quota > 0:
        quota_used_pct = round(total_tokens / quota * 100, 2)

    return UsageResponse(
        total_tokens=int(total_tokens),
        quota=quota,
        quota_used_pct=quota_used_pct,
        daily=daily,
        by_agent=by_agent,
    )


# ---------------------------------------------------------------------------
# E) Notification Preferences
# ---------------------------------------------------------------------------


@router.get("/notifications", response_model=NotificationPrefListResponse)
async def list_my_notification_prefs(
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> NotificationPrefListResponse:
    """List all notification preferences for the authenticated user."""
    result = await db.execute(
        select(NotificationPreference).where(
            NotificationPreference.user_id == current_user.id
        )
    )
    rows = result.scalars().all()
    return NotificationPrefListResponse(
        items=[
            NotificationPrefInfo(
                id=r.id,
                event_type=r.event_type,
                channel=r.channel,
                enabled=r.enabled,
                config=r.config,
            )
            for r in rows
        ]
    )


@router.put("/notifications", response_model=NotificationPrefListResponse)
async def upsert_my_notification_prefs(
    body: NotificationPrefBulkRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> NotificationPrefListResponse:
    """Bulk upsert notification preferences."""
    # Load existing
    result = await db.execute(
        select(NotificationPreference).where(
            NotificationPreference.user_id == current_user.id
        )
    )
    existing = {(r.event_type, r.channel): r for r in result.scalars().all()}

    for item in body.preferences:
        key = (item.event_type, item.channel)
        if key in existing:
            pref = existing[key]
            pref.enabled = item.enabled
            pref.config = item.config
        else:
            pref = NotificationPreference(
                user_id=current_user.id,
                event_type=item.event_type,
                channel=item.channel,
                enabled=item.enabled,
                config=item.config,
            )
            db.add(pref)

    await db.commit()

    # Reload
    result = await db.execute(
        select(NotificationPreference).where(
            NotificationPreference.user_id == current_user.id
        )
    )
    rows = result.scalars().all()
    return NotificationPrefListResponse(
        items=[
            NotificationPrefInfo(
                id=r.id,
                event_type=r.event_type,
                channel=r.channel,
                enabled=r.enabled,
                config=r.config,
            )
            for r in rows
        ]
    )


# ---------------------------------------------------------------------------
# F) Subscriptions
# ---------------------------------------------------------------------------


@router.get("/subscriptions", response_model=SubscriptionListResponse)
async def list_my_subscriptions(
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> SubscriptionListResponse:
    """List the user's Market resource subscriptions."""
    result = await db.execute(
        select(ResourceSubscription)
        .where(ResourceSubscription.user_id == current_user.id)
        .order_by(ResourceSubscription.created_at.desc())
    )
    rows = result.scalars().all()

    items: list[SubscriptionInfo] = []
    for row in rows:
        # Try to resolve the resource name based on type
        resource_name = await _resolve_resource_name(db, row.resource_type, row.resource_id)
        items.append(
            SubscriptionInfo(
                id=row.id,
                resource_type=row.resource_type,
                resource_id=row.resource_id,
                resource_name=resource_name,
                org_id=row.org_id,
                subscribed_at=row.created_at.isoformat() if row.created_at else "",
            )
        )

    return SubscriptionListResponse(items=items, total=len(items))


async def _resolve_resource_name(
    db: AsyncSession, resource_type: str, resource_id: str
) -> str | None:
    """Best-effort lookup of a subscription's resource name."""
    from typing import Any

    from fim_one.web.models.connector import Connector
    from fim_one.web.models.knowledge_base import KnowledgeBase
    from fim_one.web.models.mcp_server import MCPServer

    model_map: dict[str, Any] = {
        "agent": Agent,
        "connector": Connector,
        "knowledge_base": KnowledgeBase,
        "mcp_server": MCPServer,
    }
    model = model_map.get(resource_type)
    if model is None:
        return None
    result = await db.execute(select(model.name).where(model.id == resource_id))
    return result.scalar_one_or_none()

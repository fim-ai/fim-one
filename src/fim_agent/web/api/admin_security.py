"""Admin-only API endpoints for login security and IP rules management."""

from __future__ import annotations

import ipaddress
import math
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_agent.db import get_session
from fim_agent.web.auth import get_current_admin
from fim_agent.web.exceptions import AppError
from fim_agent.web.models import IpRule, LoginHistory, User

from fim_agent.web.api.admin_utils import write_audit  # noqa: E402

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LoginHistoryItem(BaseModel):
    id: str
    user_id: str | None = None
    username: str | None = None
    email: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    success: bool
    failure_reason: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class LoginHistoryPaginatedResponse(BaseModel):
    items: list[LoginHistoryItem]
    total: int
    page: int
    size: int
    pages: int


class LoginStatsResponse(BaseModel):
    total_attempts: int
    successful: int
    failed: int
    unique_ips: int
    unique_users: int
    recent_failures: int


class IpRuleResponse(BaseModel):
    id: str
    ip_address: str
    rule_type: str
    note: str | None = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class IpRuleCreateRequest(BaseModel):
    ip_address: str = Field(..., max_length=45)
    rule_type: str = Field(..., pattern=r"^(allow|deny)$")
    note: str | None = Field(None, max_length=255)

    @field_validator("ip_address")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("IP address cannot be empty")
        # Support CIDR notation (e.g. 192.168.1.0/24) and plain IP addresses
        try:
            if "/" in v:
                ipaddress.ip_network(v, strict=False)
            else:
                ipaddress.ip_address(v)
        except ValueError:
            raise ValueError(f"Invalid IP address or CIDR: {v}")
        return v


class IpRuleToggleRequest(BaseModel):
    is_active: bool


class ActiveSessionItem(BaseModel):
    user_id: str
    username: str | None = None
    email: str
    is_admin: bool
    refresh_token_expires_at: datetime | None = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Login History endpoints
# ---------------------------------------------------------------------------


@router.get("/login-history", response_model=LoginHistoryPaginatedResponse)
async def list_login_history(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    user_id: str | None = Query(None),
    success: bool | None = Query(None),
    date_from: date | None = Query(None, description="Filter from date (YYYY-MM-DD)"),
    date_to: date | None = Query(None, description="Filter to date (YYYY-MM-DD)"),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """List login attempts with pagination and optional filters."""
    conditions = []
    if user_id is not None:
        conditions.append(LoginHistory.user_id == user_id)
    if success is not None:
        conditions.append(LoginHistory.success == success)
    if date_from is not None:
        conditions.append(
            LoginHistory.created_at >= datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc)
        )
    if date_to is not None:
        # Include the entire day by using the start of the next day
        next_day = date_to + timedelta(days=1)
        conditions.append(
            LoginHistory.created_at < datetime(next_day.year, next_day.month, next_day.day, tzinfo=timezone.utc)
        )

    where_clause = and_(*conditions) if conditions else True  # type: ignore[arg-type]

    # Total count
    count_q = select(func.count()).select_from(LoginHistory).where(where_clause)
    total = (await db.execute(count_q)).scalar_one()

    # Paginated items
    offset = (page - 1) * size
    items_q = (
        select(LoginHistory)
        .where(where_clause)
        .order_by(LoginHistory.created_at.desc())
        .offset(offset)
        .limit(size)
    )
    result = await db.execute(items_q)
    rows = result.scalars().all()

    return LoginHistoryPaginatedResponse(
        items=[LoginHistoryItem.model_validate(r) for r in rows],
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total > 0 else 0,
    )


@router.get("/login-history/stats", response_model=LoginStatsResponse)
async def login_stats(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """Return aggregate login statistics."""
    total_q = select(func.count()).select_from(LoginHistory)
    total_attempts = (await db.execute(total_q)).scalar_one()

    success_q = select(func.count()).select_from(LoginHistory).where(LoginHistory.success == True)  # noqa: E712
    successful = (await db.execute(success_q)).scalar_one()

    failed = total_attempts - successful

    unique_ips_q = select(func.count(func.distinct(LoginHistory.ip_address))).select_from(LoginHistory)
    unique_ips = (await db.execute(unique_ips_q)).scalar_one()

    unique_users_q = select(func.count(func.distinct(LoginHistory.user_id))).select_from(LoginHistory)
    unique_users = (await db.execute(unique_users_q)).scalar_one()

    # Recent failures in the last 24 hours
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    recent_q = (
        select(func.count())
        .select_from(LoginHistory)
        .where(and_(LoginHistory.success == False, LoginHistory.created_at >= cutoff))  # noqa: E712
    )
    recent_failures = (await db.execute(recent_q)).scalar_one()

    return LoginStatsResponse(
        total_attempts=total_attempts,
        successful=successful,
        failed=failed,
        unique_ips=unique_ips,
        unique_users=unique_users,
        recent_failures=recent_failures,
    )


# ---------------------------------------------------------------------------
# IP Rules endpoints
# ---------------------------------------------------------------------------


@router.get("/ip-rules", response_model=list[IpRuleResponse])
async def list_ip_rules(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """List all IP rules ordered by creation time."""
    result = await db.execute(
        select(IpRule).order_by(IpRule.created_at.desc())
    )
    rows = result.scalars().all()
    return [IpRuleResponse.model_validate(r) for r in rows]


@router.post("/ip-rules", response_model=IpRuleResponse, status_code=201)
async def create_ip_rule(
    body: IpRuleCreateRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """Create a new IP allow/deny rule."""
    # Check for duplicate ip_address + rule_type
    dup_q = select(IpRule).where(
        and_(IpRule.ip_address == body.ip_address, IpRule.rule_type == body.rule_type)
    )
    existing = (await db.execute(dup_q)).scalar_one_or_none()
    if existing is not None:
        raise AppError(
            "ip_rule_duplicate",
            status_code=409,
            detail=f"An IP rule for {body.ip_address} ({body.rule_type}) already exists",
        )

    rule = IpRule(
        ip_address=body.ip_address,
        rule_type=body.rule_type,
        note=body.note,
        is_active=True,
        created_by_id=current_user.id,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)

    await write_audit(
        db,
        current_user,
        "ip_rule.create",
        target_type="ip_rule",
        target_id=rule.id,
        detail=f"{body.rule_type} {body.ip_address}",
    )

    return IpRuleResponse.model_validate(rule)


@router.patch("/ip-rules/{rule_id}/active")
async def toggle_ip_rule(
    rule_id: str,
    body: IpRuleToggleRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """Enable or disable an IP rule."""
    result = await db.execute(select(IpRule).where(IpRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if rule is None:
        raise AppError("ip_rule_not_found", status_code=404)

    rule.is_active = body.is_active
    await db.commit()

    action = "ip_rule.enable" if body.is_active else "ip_rule.disable"
    await write_audit(
        db,
        current_user,
        action,
        target_type="ip_rule",
        target_id=rule_id,
        detail=f"{rule.rule_type} {rule.ip_address}",
    )

    return IpRuleResponse.model_validate(rule)


@router.delete("/ip-rules/{rule_id}", status_code=204)
async def delete_ip_rule(
    rule_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """Delete an IP rule permanently."""
    result = await db.execute(select(IpRule).where(IpRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if rule is None:
        raise AppError("ip_rule_not_found", status_code=404)

    detail = f"{rule.rule_type} {rule.ip_address}"
    await db.delete(rule)
    await db.commit()

    await write_audit(
        db,
        current_user,
        "ip_rule.delete",
        target_type="ip_rule",
        target_id=rule_id,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Active Sessions endpoint
# ---------------------------------------------------------------------------


@router.get("/sessions", response_model=list[ActiveSessionItem])
async def list_active_sessions(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """List users with active refresh tokens (i.e. active sessions)."""
    now = datetime.now(timezone.utc)
    q = (
        select(User)
        .where(
            and_(
                User.refresh_token.isnot(None),
                User.refresh_token_expires_at > now,
            )
        )
        .order_by(User.refresh_token_expires_at.desc())
    )
    result = await db.execute(q)
    users = result.scalars().all()

    return [
        ActiveSessionItem(
            user_id=u.id,
            username=u.username,
            email=u.email,
            is_admin=u.is_admin,
            refresh_token_expires_at=u.refresh_token_expires_at,
        )
        for u in users
    ]

"""Admin API endpoints for enhanced analytics, data export, and announcements."""

from __future__ import annotations

import csv
import io
import json
import os
import re
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_agent.db import get_session
from fim_agent.web.auth import get_current_admin
from fim_agent.web.exceptions import AppError
from fim_agent.web.models import (
    Announcement,
    Conversation,
    Message,
    SystemSetting,
    User,
)

from fim_agent.web.api.admin_utils import write_audit

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class UserTokenUsage(BaseModel):
    user_id: str
    username: str | None = None
    email: str | None = None
    total_tokens: int
    conversation_count: int
    token_quota: int | None = None


class DailyTrend(BaseModel):
    date: str
    total_tokens: int
    conversation_count: int
    active_users: int


class ModelCost(BaseModel):
    model: str
    tokens: int
    cost: float


class CostBreakdown(BaseModel):
    total_tokens: int
    estimated_cost: float
    by_model: list[ModelCost]


class AnnouncementInfo(BaseModel):
    id: str
    title: str
    content: str
    level: str
    is_active: bool
    starts_at: str | None = None
    ends_at: str | None = None
    target_group: str | None = None
    created_at: str


class AnnouncementCreate(BaseModel):
    title: str = Field(..., max_length=200)
    content: str
    level: str = Field(default="info", max_length=20)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    target_group: str | None = Field(default=None, max_length=50)


class AnnouncementUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    content: str | None = None
    level: str | None = Field(default=None, max_length=20)
    is_active: bool | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    target_group: str | None = Field(default=None, max_length=50)


class LogEntry(BaseModel):
    timestamp: str | None = None
    level: str | None = None
    logger: str | None = None
    message: str


class SystemLogsResponse(BaseModel):
    entries: list[LogEntry]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Standard log line pattern: "2024-01-15 10:30:45,123 - logger.name - INFO - message"
_LOG_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[,.\d]*)"  # timestamp
    r"\s+-\s+"
    r"([\w.]+)"  # logger
    r"\s+-\s+"
    r"(\w+)"  # level
    r"\s+-\s+"
    r"(.*)$",  # message
)


def _dt_to_iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _announcement_to_info(ann: Announcement) -> AnnouncementInfo:
    return AnnouncementInfo(
        id=ann.id,
        title=ann.title,
        content=ann.content,
        level=ann.level,
        is_active=ann.is_active,
        starts_at=_dt_to_iso(ann.starts_at),
        ends_at=_dt_to_iso(ann.ends_at),
        target_group=ann.target_group,
        created_at=ann.created_at.isoformat() if ann.created_at else "",
    )


def _period_cutoff(period: str) -> datetime | None:
    """Return the UTC cutoff datetime for a given period, or None for 'all'."""
    now = datetime.now(timezone.utc)
    if period == "week":
        return now - timedelta(days=7)
    if period == "month":
        return now - timedelta(days=30)
    return None


# ---------------------------------------------------------------------------
# Enhanced Usage Analytics
# ---------------------------------------------------------------------------


@router.get("/analytics/usage", response_model=list[UserTokenUsage])
async def analytics_usage(
    period: str = Query("month", pattern="^(week|month|all)$"),
    top_n: int = Query(20, ge=1, le=200),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[UserTokenUsage]:
    """Per-user token usage breakdown, sorted by total_tokens descending."""
    cutoff = _period_cutoff(period)

    # Build subquery for conversation aggregates per user
    conv_q = (
        select(
            Conversation.user_id,
            func.coalesce(func.sum(Conversation.total_tokens), 0).label("total_tokens"),
            func.count().label("conversation_count"),
        )
        .group_by(Conversation.user_id)
    )
    if cutoff is not None:
        conv_q = conv_q.where(Conversation.created_at >= cutoff)
    conv_sub = conv_q.subquery()

    # Join with User to get username, email, token_quota
    query = (
        select(
            User.id,
            User.username,
            User.email,
            func.coalesce(conv_sub.c.total_tokens, 0).label("total_tokens"),
            func.coalesce(conv_sub.c.conversation_count, 0).label("conversation_count"),
            User.token_quota,
        )
        .outerjoin(conv_sub, User.id == conv_sub.c.user_id)
        .order_by(func.coalesce(conv_sub.c.total_tokens, 0).desc())
        .limit(top_n)
    )

    result = await db.execute(query)
    return [
        UserTokenUsage(
            user_id=row.id,
            username=row.username,
            email=row.email,
            total_tokens=row.total_tokens,
            conversation_count=row.conversation_count,
            token_quota=row.token_quota,
        )
        for row in result.all()
    ]


@router.get("/analytics/trends", response_model=list[DailyTrend])
async def analytics_trends(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[DailyTrend]:
    """Daily token usage trend for the last 30 days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    query = (
        select(
            func.date(Conversation.created_at).label("day"),
            func.coalesce(func.sum(Conversation.total_tokens), 0).label("total_tokens"),
            func.count().label("conversation_count"),
            func.count(distinct(Conversation.user_id)).label("active_users"),
        )
        .where(Conversation.created_at >= cutoff)
        .group_by(func.date(Conversation.created_at))
        .order_by(func.date(Conversation.created_at))
    )

    result = await db.execute(query)
    return [
        DailyTrend(
            date=str(row.day),
            total_tokens=row.total_tokens,
            conversation_count=row.conversation_count,
            active_users=row.active_users,
        )
        for row in result.all()
    ]


@router.get("/analytics/cost", response_model=CostBreakdown)
async def analytics_cost(
    price_per_1k_tokens: float = Query(0.01, ge=0),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> CostBreakdown:
    """Estimated cost breakdown by model."""
    # Total tokens across all conversations
    total_result = await db.execute(
        select(func.coalesce(func.sum(Conversation.total_tokens), 0))
    )
    total_tokens: int = total_result.scalar_one()

    # Per-model breakdown
    model_query = (
        select(
            func.coalesce(Conversation.model_name, "Unknown").label("model"),
            func.coalesce(func.sum(Conversation.total_tokens), 0).label("tokens"),
        )
        .group_by(func.coalesce(Conversation.model_name, "Unknown"))
        .order_by(func.sum(Conversation.total_tokens).desc())
    )
    model_result = await db.execute(model_query)

    by_model = [
        ModelCost(
            model=row.model,
            tokens=row.tokens,
            cost=round(row.tokens / 1000 * price_per_1k_tokens, 4),
        )
        for row in model_result.all()
    ]

    estimated_cost = round(total_tokens / 1000 * price_per_1k_tokens, 4)
    return CostBreakdown(
        total_tokens=total_tokens,
        estimated_cost=estimated_cost,
        by_model=by_model,
    )


# ---------------------------------------------------------------------------
# Data Export
# ---------------------------------------------------------------------------


@router.get("/export/users")
async def export_users(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> StreamingResponse:
    """Export all users as CSV."""
    # Compute monthly tokens per user (last 30 days)
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    monthly_sub = (
        select(
            Conversation.user_id,
            func.coalesce(func.sum(Conversation.total_tokens), 0).label("monthly_tokens"),
        )
        .where(Conversation.created_at >= cutoff)
        .group_by(Conversation.user_id)
        .subquery()
    )

    query = (
        select(
            User,
            func.coalesce(monthly_sub.c.monthly_tokens, 0).label("monthly_tokens"),
        )
        .outerjoin(monthly_sub, User.id == monthly_sub.c.user_id)
        .order_by(User.created_at)
    )
    result = await db.execute(query)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "username", "email", "display_name", "is_admin",
        "is_active", "created_at", "monthly_tokens", "token_quota",
    ])
    for row in result.all():
        user = row[0]
        monthly_tokens = row.monthly_tokens
        writer.writerow([
            user.id,
            user.username or "",
            user.email or "",
            user.display_name or "",
            user.is_admin,
            user.is_active,
            user.created_at.isoformat() if user.created_at else "",
            monthly_tokens,
            user.token_quota if user.token_quota is not None else "",
        ])

    buf.seek(0)
    today = date.today().isoformat()

    await write_audit(db, current_user, "export.users")

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="users-{today}.csv"'},
    )


@router.get("/export/conversations")
async def export_conversations(
    user_id: str | None = None,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> StreamingResponse:
    """Export conversations as CSV, optionally filtered by user_id."""
    # Subquery for message count
    msg_sub = (
        select(
            Message.conversation_id,
            func.count().label("message_count"),
        )
        .group_by(Message.conversation_id)
        .subquery()
    )

    query = (
        select(
            Conversation,
            User.username,
            func.coalesce(msg_sub.c.message_count, 0).label("message_count"),
        )
        .join(User, Conversation.user_id == User.id)
        .outerjoin(msg_sub, Conversation.id == msg_sub.c.conversation_id)
        .order_by(Conversation.created_at.desc())
    )
    if user_id:
        query = query.where(Conversation.user_id == user_id)

    result = await db.execute(query)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "user_id", "username", "title", "mode",
        "model_name", "total_tokens", "message_count", "created_at",
    ])
    for row in result.all():
        conv = row[0]
        username = row.username
        message_count = row.message_count
        writer.writerow([
            conv.id,
            conv.user_id,
            username or "",
            conv.title or "",
            conv.mode or "",
            conv.model_name or "",
            conv.total_tokens,
            message_count,
            conv.created_at.isoformat() if conv.created_at else "",
        ])

    buf.seek(0)
    today = date.today().isoformat()

    await write_audit(db, current_user, "export.conversations")

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="conversations-{today}.csv"'
        },
    )


@router.get("/export/full-backup")
async def export_full_backup(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> StreamingResponse:
    """Export full system data as JSON (excludes passwords and sensitive fields)."""
    # Users (exclude password_hash, refresh_token, tokens_invalidated_at)
    user_result = await db.execute(select(User).order_by(User.created_at))
    users_data = [
        {
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "display_name": u.display_name,
            "is_admin": u.is_admin,
            "is_active": u.is_active,
            "token_quota": u.token_quota,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in user_result.scalars().all()
    ]

    # Conversations with message counts
    msg_sub = (
        select(
            Message.conversation_id,
            func.count().label("message_count"),
        )
        .group_by(Message.conversation_id)
        .subquery()
    )
    conv_result = await db.execute(
        select(
            Conversation,
            func.coalesce(msg_sub.c.message_count, 0).label("message_count"),
        )
        .outerjoin(msg_sub, Conversation.id == msg_sub.c.conversation_id)
        .order_by(Conversation.created_at.desc())
    )
    conversations_data = [
        {
            "id": row[0].id,
            "user_id": row[0].user_id,
            "title": row[0].title,
            "mode": row[0].mode,
            "model_name": row[0].model_name,
            "total_tokens": row[0].total_tokens,
            "message_count": row.message_count,
            "created_at": row[0].created_at.isoformat() if row[0].created_at else None,
        }
        for row in conv_result.all()
    ]

    # System settings
    settings_result = await db.execute(select(SystemSetting))
    settings_data = {
        s.key: s.value for s in settings_result.scalars().all()
    }

    # Announcement count
    ann_count_result = await db.execute(
        select(func.count()).select_from(Announcement)
    )
    announcement_count: int = ann_count_result.scalar_one()

    backup = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "users": users_data,
        "conversations": conversations_data,
        "system_settings": settings_data,
        "announcement_count": announcement_count,
    }

    content = json.dumps(backup, ensure_ascii=False, indent=2)
    today = date.today().isoformat()

    await write_audit(db, current_user, "export.full_backup")

    return StreamingResponse(
        iter([content]),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="full-backup-{today}.json"'
        },
    )


# ---------------------------------------------------------------------------
# Announcement Management
# ---------------------------------------------------------------------------


@router.get("/announcements", response_model=list[AnnouncementInfo])
async def list_announcements(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[AnnouncementInfo]:
    """List all announcements ordered by created_at descending."""
    result = await db.execute(
        select(Announcement).order_by(Announcement.created_at.desc())
    )
    return [_announcement_to_info(a) for a in result.scalars().all()]


@router.post("/announcements", response_model=AnnouncementInfo, status_code=201)
async def create_announcement(
    body: AnnouncementCreate,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> AnnouncementInfo:
    """Create a new announcement."""
    if body.level not in ("info", "warning", "error"):
        raise AppError(
            "invalid_announcement_level",
            status_code=422,
            detail=f"Invalid level: {body.level}. Must be info, warning, or error.",
        )

    ann = Announcement(
        title=body.title,
        content=body.content,
        level=body.level,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        target_group=body.target_group,
        created_by_id=current_user.id,
    )
    db.add(ann)
    await db.commit()
    await db.refresh(ann)

    await write_audit(
        db,
        current_user,
        "announcement.create",
        target_type="announcement",
        target_id=ann.id,
        target_label=ann.title,
    )

    return _announcement_to_info(ann)


@router.put("/announcements/{ann_id}", response_model=AnnouncementInfo)
async def update_announcement(
    ann_id: str,
    body: AnnouncementUpdate,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> AnnouncementInfo:
    """Update an existing announcement."""
    result = await db.execute(
        select(Announcement).where(Announcement.id == ann_id)
    )
    ann = result.scalar_one_or_none()
    if ann is None:
        raise AppError("announcement_not_found", status_code=404)

    if body.level is not None and body.level not in ("info", "warning", "error"):
        raise AppError(
            "invalid_announcement_level",
            status_code=422,
            detail=f"Invalid level: {body.level}. Must be info, warning, or error.",
        )

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(ann, field, value)

    await db.commit()
    await db.refresh(ann)

    await write_audit(
        db,
        current_user,
        "announcement.update",
        target_type="announcement",
        target_id=ann.id,
        target_label=ann.title,
    )

    return _announcement_to_info(ann)


@router.delete("/announcements/{ann_id}", status_code=204)
async def delete_announcement(
    ann_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> Response:
    """Delete an announcement."""
    result = await db.execute(
        select(Announcement).where(Announcement.id == ann_id)
    )
    ann = result.scalar_one_or_none()
    if ann is None:
        raise AppError("announcement_not_found", status_code=404)

    title = ann.title
    await db.delete(ann)
    await db.commit()

    await write_audit(
        db,
        current_user,
        "announcement.delete",
        target_type="announcement",
        target_id=ann_id,
        target_label=title,
    )

    return Response(status_code=204)


# ---------------------------------------------------------------------------
# System Log Viewer
# ---------------------------------------------------------------------------


@router.get("/system-logs", response_model=SystemLogsResponse)
async def system_logs(
    lines: int = Query(100, ge=1, le=500),
    level: str | None = Query(None, pattern="^(INFO|WARNING|ERROR|DEBUG|CRITICAL)$"),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> SystemLogsResponse:
    """Read recent application logs from the log file."""
    log_path = os.path.join(os.getcwd(), "logs", "fim_agent.log")
    if not os.path.isfile(log_path):
        return SystemLogsResponse(entries=[])

    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
    except OSError:
        return SystemLogsResponse(entries=[])

    # Parse lines from the end (most recent)
    entries: list[LogEntry] = []
    for raw_line in reversed(all_lines):
        raw_line = raw_line.rstrip("\n")
        if not raw_line:
            continue

        m = _LOG_PATTERN.match(raw_line)
        if m:
            timestamp, logger_name, log_level, message = m.groups()
            # Filter by level if specified
            if level and log_level != level:
                continue
            entries.append(
                LogEntry(
                    timestamp=timestamp,
                    level=log_level,
                    logger=logger_name,
                    message=message,
                )
            )
        else:
            # Non-matching lines (e.g. tracebacks) — include as message-only
            if level:
                continue  # skip non-parseable lines when filtering by level
            entries.append(LogEntry(message=raw_line))

        if len(entries) >= lines:
            break

    # Reverse back to chronological order
    entries.reverse()
    return SystemLogsResponse(entries=entries)

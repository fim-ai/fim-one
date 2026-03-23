"""Admin endpoints for notification center — system-wide notification config and events."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.auth import get_current_admin
from fim_one.web.models import (
    AuditLog,
    ConnectorCallLog,
    LoginHistory,
    SystemSetting,
    User,
    WorkflowRun,
)

from fim_one.web.api.admin_utils import get_setting, set_setting, write_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

SETTING_NOTIFICATION_CONFIG = "admin_notification_config"

# Default notification config
_DEFAULT_CONFIG = {
    "enabled": False,
    "new_user_registration": True,
    "quota_hit": True,
    "quota_threshold_percent": 80,
    "connector_failure": True,
    "connector_failure_threshold": 5,
    "schedule_failure": True,
    "login_anomaly": True,
    "login_anomaly_threshold": 10,
    "smtp_configured": False,
    "channels": [],
}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class NotificationConfig(BaseModel):
    enabled: bool = False  # Master switch
    new_user_registration: bool = True
    quota_hit: bool = True
    quota_threshold_percent: int = 80
    connector_failure: bool = True
    connector_failure_threshold: int = 5
    schedule_failure: bool = True
    login_anomaly: bool = True
    login_anomaly_threshold: int = 10
    smtp_configured: bool = False  # Runtime-computed, not persisted
    channels: list[str] = Field(default_factory=list)


class SystemEvent(BaseModel):
    event_type: str
    description: str
    severity: str = "info"  # info, warning, error
    created_at: str
    details: dict[str, object] | None = None


class SystemEventsResponse(BaseModel):
    events: list[SystemEvent] = Field(default_factory=list)
    total: int = 0


class TestNotificationResponse(BaseModel):
    success: bool = True
    message: str = ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/notifications/config", response_model=NotificationConfig)
async def get_notification_config(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> NotificationConfig:
    """Get system-wide notification settings."""
    raw = await get_setting(db, SETTING_NOTIFICATION_CONFIG, default="")
    if raw:
        try:
            data = json.loads(raw)
            config = NotificationConfig.model_validate(data)
        except (json.JSONDecodeError, TypeError):
            config = NotificationConfig.model_validate(_DEFAULT_CONFIG)
    else:
        config = NotificationConfig.model_validate(_DEFAULT_CONFIG)

    # Always compute SMTP status at runtime — never trust DB value
    from fim_one.web.email import _smtp_configured
    config.smtp_configured = _smtp_configured()
    return config


@router.put("/notifications/config", response_model=NotificationConfig)
async def update_notification_config(
    body: NotificationConfig,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> NotificationConfig:
    """Update system-wide notification config. Stored in SystemSetting as JSON."""
    data = body.model_dump()
    data.pop("smtp_configured", None)  # Runtime-only, don't persist
    config_json = json.dumps(data)
    await set_setting(db, SETTING_NOTIFICATION_CONFIG, config_json)

    await write_audit(
        db,
        current_user,
        "notifications.config_update",
        detail="Updated notification config",
    )

    # Return with runtime SMTP status
    from fim_one.web.email import _smtp_configured
    body.smtp_configured = _smtp_configured()
    return body


@router.get("/notifications/events", response_model=SystemEventsResponse)
async def list_system_events(
    hours: int = Query(24, ge=1, le=168),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> SystemEventsResponse:
    """Recent system events from various tables. Requires admin privileges.

    Collects events from: AuditLog (critical actions), ConnectorCallLog (failures),
    LoginHistory (anomalies), WorkflowRun (failures).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    events: list[SystemEvent] = []

    # 1. Failed workflow runs
    wf_failures = await db.execute(
        select(WorkflowRun)
        .where(
            WorkflowRun.status == "failed",
            WorkflowRun.created_at >= cutoff,
        )
        .order_by(WorkflowRun.created_at.desc())
        .limit(limit // 4)
    )
    for run in wf_failures.scalars().all():
        events.append(
            SystemEvent(
                event_type="workflow_failure",
                description=f"Workflow run {run.id[:8]}... failed",
                severity="error",
                created_at=run.created_at.isoformat() if run.created_at else "",
                details={
                    "workflow_id": run.workflow_id,
                    "run_id": run.id,
                    "error": (run.error or "")[:200],
                },
            )
        )

    # 2. Connector call failures (grouped by connector)
    cc_failures = await db.execute(
        select(
            ConnectorCallLog.connector_id,
            ConnectorCallLog.connector_name,
            func.count(ConnectorCallLog.id).label("failure_count"),
        )
        .where(
            ConnectorCallLog.success == False,  # noqa: E712
            ConnectorCallLog.created_at >= cutoff,
        )
        .group_by(ConnectorCallLog.connector_id, ConnectorCallLog.connector_name)
        .having(func.count(ConnectorCallLog.id) >= 3)
        .order_by(func.count(ConnectorCallLog.id).desc())
        .limit(limit // 4)
    )
    for row in cc_failures.all():
        events.append(
            SystemEvent(
                event_type="connector_failures",
                description=f"Connector '{row.connector_name}' had {row.failure_count} failures",
                severity="warning",
                created_at=datetime.now(timezone.utc).isoformat(),
                details={
                    "connector_id": row.connector_id,
                    "connector_name": row.connector_name,
                    "failure_count": row.failure_count,
                },
            )
        )

    # 3. Failed login attempts (grouped by IP)
    login_failures = await db.execute(
        select(
            LoginHistory.ip_address,
            func.count(LoginHistory.id).label("failure_count"),
        )
        .where(
            LoginHistory.success == False,  # noqa: E712
            LoginHistory.created_at >= cutoff,
        )
        .group_by(LoginHistory.ip_address)
        .having(func.count(LoginHistory.id) >= 5)
        .order_by(func.count(LoginHistory.id).desc())
        .limit(limit // 4)
    )
    for login_row in login_failures.all():
        events.append(
            SystemEvent(
                event_type="login_anomaly",
                description=f"IP {login_row.ip_address} had {login_row.failure_count} failed login attempts",
                severity="warning",
                created_at=datetime.now(timezone.utc).isoformat(),
                details={
                    "ip_address": login_row.ip_address,
                    "failure_count": login_row.failure_count,
                },
            )
        )

    # 4. Critical audit log entries (deletes, security changes)
    critical_actions = await db.execute(
        select(AuditLog)
        .where(
            AuditLog.created_at >= cutoff,
            or_(
                AuditLog.action.like("%delete%"),
                AuditLog.action.like("%security%"),
                AuditLog.action.like("%batch%"),
            ),
        )
        .order_by(AuditLog.created_at.desc())
        .limit(limit // 4)
    )
    for log in critical_actions.scalars().all():
        events.append(
            SystemEvent(
                event_type="admin_action",
                description=f"Admin {log.admin_username}: {log.action}",
                severity="info",
                created_at=log.created_at.isoformat() if log.created_at else "",
                details={
                    "action": log.action,
                    "target_type": log.target_type,
                    "target_id": log.target_id,
                    "detail": log.detail,
                },
            )
        )

    # Sort all events by created_at descending
    events.sort(key=lambda e: e.created_at, reverse=True)
    events = events[:limit]

    return SystemEventsResponse(events=events, total=len(events))


@router.post("/notifications/test", response_model=TestNotificationResponse)
async def test_notification(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> TestNotificationResponse:
    """Send a real test notification to all admins."""
    import asyncio
    import os

    from fim_one.web.email import _smtp_configured

    if not _smtp_configured():
        return TestNotificationResponse(
            success=False,
            message="SMTP is not configured. Please set SMTP_HOST, SMTP_USER, and SMTP_PASS environment variables.",
        )

    from fim_one.web.admin_notify import _build_admin_email_html, _get_admin_emails
    from fim_one.web.email import _send_email

    admin_emails = await _get_admin_emails()
    if not admin_emails:
        return TestNotificationResponse(
            success=False,
            message="No active admin users found.",
        )

    app_name = os.getenv("APP_NAME", "FIM One")
    subject = f"[{app_name}] Test Notification"
    body_html = _build_admin_email_html(
        "Test Notification",
        [
            f"Triggered by: {current_user.email}",
            "This is a test notification to verify your admin email alert configuration is working correctly.",
        ],
    )

    errors = []
    for email in admin_emails:
        try:
            await asyncio.to_thread(_send_email, email, subject, body_html)
        except Exception as e:
            errors.append(f"{email}: {e}")

    await write_audit(
        db,
        current_user,
        "notifications.test",
        detail=f"Test notification sent to {len(admin_emails)} admin(s)"
        + (f", {len(errors)} failed" if errors else ""),
    )

    if errors:
        return TestNotificationResponse(
            success=False,
            message=f"Sent to {len(admin_emails) - len(errors)} admin(s), {len(errors)} failed: {'; '.join(errors)}",
        )
    return TestNotificationResponse(
        success=True,
        message=f"Test notification sent to {len(admin_emails)} admin(s): {', '.join(admin_emails)}",
    )

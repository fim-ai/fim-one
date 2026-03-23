"""Admin email notification dispatcher.

Sends email alerts to all admin users when system events occur.
Only active when SMTP is properly configured and the event type
is enabled in the notification configuration.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime

from sqlalchemy import select

from fim_one.db.engine import create_session
from fim_one.web.email import _send_email, _smtp_configured
from fim_one.web.models import SystemSetting, User

logger = logging.getLogger(__name__)

SETTING_NOTIFICATION_CONFIG = "admin_notification_config"


async def _load_notification_config() -> dict[str, bool]:
    """Load notification config from DB, returning defaults if not set."""
    defaults = {
        "enabled": False,
        "new_user_registration": True,
        "quota_hit": True,
        "connector_failure": True,
        "schedule_failure": True,
        "login_anomaly": True,
    }
    try:
        async with create_session() as db:
            result = await db.execute(
                select(SystemSetting.value).where(
                    SystemSetting.key == SETTING_NOTIFICATION_CONFIG
                )
            )
            raw = result.scalar_one_or_none()
            if raw:
                data = json.loads(raw)
                defaults.update(data)
    except Exception:
        logger.warning("Failed to load notification config, using defaults", exc_info=True)
    return defaults


async def _get_admin_emails() -> list[str]:
    """Get email addresses of all active admin users."""
    try:
        async with create_session() as db:
            result = await db.execute(
                select(User.email).where(
                    User.is_admin == True,  # noqa: E712
                    User.is_active == True,  # noqa: E712
                )
            )
            return [row[0] for row in result.all()]
    except Exception:
        logger.warning("Failed to get admin emails", exc_info=True)
        return []


def _build_admin_email_html(title: str, body_lines: list[str]) -> str:
    """Build a styled HTML email for admin notifications."""
    app_name = os.getenv("APP_NAME", "FIM One")
    copyright_text = f"&copy; {datetime.now().year} {app_name}"

    rows_html = ""
    for line in body_lines:
        if ": " in line:
            label, value = line.split(": ", 1)
            rows_html += f"""
            <tr>
              <td style="padding:6px 12px 6px 0;color:#9c9488;font-size:14px;white-space:nowrap;vertical-align:top;">{label}</td>
              <td style="padding:6px 0;color:#1a1714;font-size:14px;">{value}</td>
            </tr>"""
        else:
            rows_html += f"""
            <tr>
              <td colspan="2" style="padding:6px 0;color:#4b4540;font-size:14px;">{line}</td>
            </tr>"""

    return f"""\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
</head>
<body style="margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#e8e4dd;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="max-width:480px;margin:40px auto;">
    <tr>
      <td>
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="border-radius:16px;overflow:hidden;box-shadow:0 8px 32px rgba(30,27,24,0.12);">
          <tr>
            <td style="background:#1a1714;padding:24px 32px;text-align:center;">
              <h1 style="margin:0 0 4px;color:#ffffff;font-size:18px;font-weight:700;">{app_name}</h1>
              <p style="margin:0;color:#c49520;font-size:11px;text-transform:uppercase;letter-spacing:2.5px;font-weight:500;">Admin Notification</p>
            </td>
          </tr>
          <tr>
            <td style="background:#ffffff;padding:32px;">
              <h2 style="margin:0 0 20px;color:#1a1714;font-size:16px;font-weight:600;">{title}</h2>
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                {rows_html}
              </table>
            </td>
          </tr>
          <tr>
            <td style="background:#f5f2eb;padding:16px 32px;text-align:center;">
              <p style="margin:0;color:#9c9488;font-size:11px;">{copyright_text}</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


async def notify_admins(
    event_type: str,
    title: str,
    body_lines: list[str],
) -> None:
    """Send an admin notification email. Safe for fire-and-forget via create_task.

    Args:
        event_type: Config key, e.g. "new_user_registration".
        title: Email subject suffix and heading.
        body_lines: List of "Label: Value" lines for the email body.
    """
    if not _smtp_configured():
        return

    try:
        config = await _load_notification_config()
        if not config.get("enabled", False):
            logger.debug("Admin notifications master switch is off, skipping")
            return
        if not config.get(event_type, False):
            logger.debug("Admin notification '%s' is disabled, skipping", event_type)
            return

        admin_emails = await _get_admin_emails()
        if not admin_emails:
            logger.debug("No admin emails found, skipping notification")
            return

        app_name = os.getenv("APP_NAME", "FIM One")
        subject = f"[{app_name}] {title}"
        body_html = _build_admin_email_html(title, body_lines)

        for email in admin_emails:
            try:
                await asyncio.to_thread(_send_email, email, subject, body_html)
                logger.info("Admin notification sent to %s: %s", email, event_type)
            except Exception:
                logger.warning("Failed to send admin notification to %s", email, exc_info=True)

    except Exception:
        logger.warning("Admin notification failed for event '%s'", event_type, exc_info=True)

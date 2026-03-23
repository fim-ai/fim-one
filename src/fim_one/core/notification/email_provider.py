"""Email notification provider using the stdlib smtplib (sync, run in thread).

This provider re-uses the same SMTP env vars as the existing ``email_send``
builtin tool: ``SMTP_HOST``, ``SMTP_PORT``, ``SMTP_USER``, ``SMTP_PASS``,
``SMTP_FROM``, ``SMTP_FROM_NAME``, ``SMTP_SSL``.

No additional dependency (like ``aiosmtplib``) is required — the blocking
SMTP call is offloaded to a thread via ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from typing import Any

from .base import NotificationMessage, NotificationProvider

logger = logging.getLogger(__name__)


class EmailNotificationProvider(NotificationProvider):
    """Send email notifications via SMTP."""

    @property
    def name(self) -> str:
        return "email"

    @property
    def display_name(self) -> str:
        return "Email (SMTP)"

    @property
    def description(self) -> str:
        return "Send email notifications via SMTP. Requires SMTP_HOST, SMTP_USER, SMTP_PASS."

    def validate_config(self) -> bool:
        return bool(
            os.getenv("SMTP_HOST")
            and os.getenv("SMTP_USER")
            and os.getenv("SMTP_PASS")
        )

    async def send(self, message: NotificationMessage) -> dict[str, Any]:
        recipient = message.channel
        if not recipient:
            return {"ok": False, "error": "Email provider requires 'channel' (recipient address)."}
        try:
            await asyncio.to_thread(
                self._send_sync,
                to=recipient,
                subject=message.title,
                body=message.body,
            )
            return {"ok": True, "provider": "email", "to": recipient}
        except Exception as exc:
            logger.exception("Email notification failed")
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # ------------------------------------------------------------------
    # Sync helper (runs in a thread)
    # ------------------------------------------------------------------

    @staticmethod
    def _send_sync(*, to: str, subject: str, body: str) -> None:
        host = os.environ["SMTP_HOST"]
        port = int(os.getenv("SMTP_PORT", "465"))
        ssl_mode = os.getenv("SMTP_SSL", "ssl").lower()
        user = os.environ["SMTP_USER"]
        password = os.environ["SMTP_PASS"]
        from_addr = os.getenv("SMTP_FROM") or user
        from_name = os.getenv("SMTP_FROM_NAME", "")

        msg = MIMEMultipart("alternative")
        msg["From"] = f"{from_name} <{from_addr}>" if from_name else from_addr
        msg["To"] = to
        msg["Subject"] = subject
        reply_to = os.getenv("SMTP_REPLY_TO")
        if reply_to:
            msg["Reply-To"] = reply_to
        # Send as HTML so providers that support rich formatting get it.
        msg.attach(MIMEText(body, "html", "utf-8"))

        recipients = [a.strip() for a in to.split(",") if a.strip()]

        if ssl_mode == "ssl":
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=ctx) as server:
                server.login(user, password)
                server.sendmail(from_addr, recipients, msg.as_string())
        elif ssl_mode == "tls":
            ctx = ssl.create_default_context()
            with smtplib.SMTP(host, port) as server:
                server.starttls(context=ctx)
                server.login(user, password)
                server.sendmail(from_addr, recipients, msg.as_string())
        else:
            with smtplib.SMTP(host, port) as server:
                server.login(user, password)
                server.sendmail(from_addr, recipients, msg.as_string())

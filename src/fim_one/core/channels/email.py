"""Email channel: a fixed list of recipients, sent through the instance SMTP account.

Uses the same SMTP env vars as the ``email_send`` builtin tool and the
system emails: ``SMTP_HOST``, ``SMTP_PORT``, ``SMTP_USER``, ``SMTP_PASS``,
``SMTP_FROM``, ``SMTP_FROM_NAME``, ``SMTP_SSL``. The blocking SMTP call is
offloaded to a thread via ``asyncio.to_thread``.
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

from .base import BaseChannel, ChannelSendResult, CompletionSummary, completion_notice
from .smtp import resolve_ssl_mode, warn_plaintext

__all__ = ["EmailChannel", "smtp_configured"]

logger = logging.getLogger(__name__)

EMAIL_RECIPIENTS_REQUIRED = "channel_email_recipients_required"


def smtp_configured() -> bool:
    """Whether the instance SMTP account is set up."""
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASS"))


class EmailChannel(BaseChannel):
    """Email to the addresses in ``config["to"]`` (comma-separated)."""

    type = "email"

    def recipients(self) -> str:
        return str(self.config.get("to") or "").strip()

    @classmethod
    def config_error(cls, config: dict[str, Any]) -> str | None:
        addresses = [a.strip() for a in str(config.get("to") or "").split(",") if a.strip()]
        if not addresses or any("@" not in a for a in addresses):
            return EMAIL_RECIPIENTS_REQUIRED
        return None

    async def send_message(self, payload: dict[str, Any]) -> ChannelSendResult:
        """Send ``payload["subject"]`` / ``payload["body"]`` (HTML) to the recipients."""
        return await self.send_notice(
            str(payload.get("subject") or ""), str(payload.get("body") or "")
        )

    async def send_notice(self, title: str, body: str) -> ChannelSendResult:
        recipient = self.recipients()
        if not recipient:
            return ChannelSendResult(
                ok=False, error="Email provider requires 'channel' (recipient address)."
            )
        try:
            await asyncio.to_thread(self._send_sync, to=recipient, subject=title, body=body)
        except Exception as exc:
            logger.exception("Email notification failed")
            return ChannelSendResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        return ChannelSendResult(ok=True, raw={"to": recipient})

    async def send_completion(self, summary: CompletionSummary) -> ChannelSendResult:
        title, body = completion_notice(summary)
        return await self.send_notice(title, body.replace("\n", "<br>\n"))

    # -- Inbound: none --

    async def verify_signature(self, body: bytes, headers: dict[str, str]) -> bool:
        """An email channel receives nothing, so no callback is authentic."""
        return False

    async def handle_callback(
        self, body: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        return {
            "response": {},
            "event": {
                "kind": "unknown",
                "action": None,
                "confirmation_id": None,
                "open_id": None,
            },
        }

    # ------------------------------------------------------------------
    # Sync helper (runs in a thread)
    # ------------------------------------------------------------------

    @staticmethod
    def _send_sync(*, to: str, subject: str, body: str) -> None:
        host = os.environ["SMTP_HOST"]
        port = int(os.getenv("SMTP_PORT", "465"))
        ssl_mode = resolve_ssl_mode()
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
            warn_plaintext(host, port)
            with smtplib.SMTP(host, port) as server:
                server.login(user, password)
                server.sendmail(from_addr, recipients, msg.as_string())

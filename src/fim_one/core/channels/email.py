"""Email channel: a fixed list of recipients, sent through the instance SMTP account.

Uses the same SMTP env vars as the ``email_send`` builtin tool and the
system emails: ``SMTP_HOST``, ``SMTP_PORT``, ``SMTP_USER``, ``SMTP_PASS``,
``SMTP_FROM``, ``SMTP_FROM_NAME``, ``SMTP_SSL``. The blocking SMTP call is
offloaded to a thread via ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
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
EMAIL_RECIPIENTS_INVALID = "channel_email_recipients_invalid"
EMAIL_NOT_PERMITTED = "channel_email_not_permitted"


_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")


def _notice_html(markdown: str) -> str:
    """A notice body as the HTML an email carries.

    The body is text someone else wrote (a completion notice quotes the
    user's message and the agent's answer, ``send_notification`` posts what
    a model wrote), so it is escaped first: none of it is markup. Only the
    ``**bold**`` markers and line breaks are then rendered.
    """
    escaped = html.escape(markdown, quote=True)
    return _BOLD_RE.sub(r"<b>\1</b>", escaped).replace("\n", "<br>\n")


def smtp_configured() -> bool:
    """Whether the instance SMTP account is set up."""
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASS"))


class EmailChannel(BaseChannel):
    """Email to the addresses in ``config["to"]`` (comma-separated, or a list)."""

    type = "email"

    def recipients(self) -> str:
        """The configured recipients as one comma-separated string.

        ``config["to"]`` is a comma-separated string or a list of strings.
        Anything else, or a list holding something that is not a string, has
        no reading as recipients and yields ``""``.
        """
        to = self.config.get("to")
        if isinstance(to, list):
            return ", ".join(a.strip() for a in to) if all(isinstance(a, str) for a in to) else ""
        return to.strip() if isinstance(to, str) else ""

    @classmethod
    def policy_error(cls, recipients: object) -> str | None:
        """Why the instance does not let this channel mail *recipients*, if it does not.

        The mail leaves from the operator's SMTP account, so a channel is
        held to what the operator allows agents (``email_send``): an
        allowlist of recipients, or an explicit opt-in.
        """
        from fim_one.core.tool.builtin.email_send import EmailSendTool, agent_email_permitted

        if not smtp_configured():
            return "SMTP is not configured on this instance."
        if not agent_email_permitted():
            return (
                "Email from agents is disabled on this instance. "
                "Configure SMTP_ALLOWED_DOMAINS / SMTP_ALLOWED_ADDRESSES "
                "or set EMAIL_TOOL_ENABLED=true."
            )
        blocked = EmailSendTool()._check_recipients(recipients)
        return blocked.removeprefix("[Error] ") if blocked else None

    @classmethod
    def config_error(cls, config: dict[str, Any]) -> str | None:
        from fim_one.core.tool.builtin.email_send import parse_recipients

        to = config.get("to")
        addresses, malformed = parse_recipients(to)
        if malformed:
            return EMAIL_RECIPIENTS_INVALID
        if not addresses:
            return EMAIL_RECIPIENTS_REQUIRED
        if cls.policy_error(to) is not None:
            return EMAIL_NOT_PERMITTED
        return None

    async def send_message(self, payload: dict[str, Any]) -> ChannelSendResult:
        """Send ``payload["subject"]`` / ``payload["body"]`` to the recipients.

        The body is HTML and is sent as it is: the markup is the caller's.
        Text that is not the caller's own (a model's, a user's) goes through
        :meth:`send_notice`, which escapes it.
        """
        return await self._send_html(
            str(payload.get("subject") or ""), str(payload.get("body") or "")
        )

    async def send_notice(self, title: str, body: str) -> ChannelSendResult:
        """Send *body* as text: escaped, with ``**bold**`` and line breaks kept.

        The title becomes the subject, a header and not HTML, on one line.
        """
        return await self._send_html(title, _notice_html(body))

    async def _send_html(self, title: str, body: str) -> ChannelSendResult:
        from fim_one.core.tool.builtin.email_send import parse_recipients, single_line

        # Checked on every send, whoever the caller is: a row saved before
        # recipients were validated may hold text the SMTP layer would read
        # as a different address than the allowlist check did.
        addresses, malformed = parse_recipients(self.config.get("to"))
        if malformed:
            return ChannelSendResult(
                ok=False,
                error=(
                    f"Not a valid email address: {', '.join(malformed)}. Give each "
                    "recipient as a plain address like name@example.com, separated by commas."
                ),
            )
        recipient = ", ".join(addresses)
        if not recipient:
            return ChannelSendResult(
                ok=False, error="Email provider requires 'channel' (recipient address)."
            )
        # Checked on every send: the operator may have narrowed the allowlist
        # since the channel was saved.
        refused = self.policy_error(recipient)
        if refused is not None:
            return ChannelSendResult(ok=False, error=refused)
        try:
            await asyncio.to_thread(
                self._send_sync, to=recipient, subject=single_line(title), body=body
            )
        except Exception as exc:
            logger.exception("Email notification failed")
            return ChannelSendResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        return ChannelSendResult(ok=True, raw={"to": recipient})

    async def send_completion(self, summary: CompletionSummary) -> ChannelSendResult:
        title, body = completion_notice(summary)
        return await self.send_notice(title, body)

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

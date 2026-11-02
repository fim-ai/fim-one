"""Built-in tool for sending email via SMTP."""

from __future__ import annotations

import asyncio
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from ..base import BaseTool


def agent_email_permitted() -> bool:
    """Whether agents may send mail from the instance SMTP account.

    The account is the operator's, often the same one that sends login codes.
    Without a recipient allowlist any user on a shared instance could mail any
    address from it, so agents get it only when an allowlist is configured or
    the operator opts in with ``EMAIL_TOOL_ENABLED``.
    """
    if os.getenv("SMTP_ALLOWED_DOMAINS", "").strip():
        return True
    if os.getenv("SMTP_ALLOWED_ADDRESSES", "").strip():
        return True
    return os.getenv("EMAIL_TOOL_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


class EmailSendTool(BaseTool):
    """Send email via SMTP. Requires SMTP_HOST, SMTP_USER, SMTP_PASS env vars."""

    @property
    def name(self) -> str:
        return "email_send"

    @property
    def display_name(self) -> str:
        return "Email Send"

    @property
    def category(self) -> str:
        return "general"

    @property
    def description(self) -> str:
        return (
            "Send an email via SMTP. "
            "Parameters: to (recipient address or comma-separated list), "
            "subject (email subject line), body (plain text or HTML content), "
            "html (set true if body is HTML, default false), "
            "cc (optional CC addresses, comma-separated), "
            "bcc (optional BCC addresses, comma-separated)."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Recipient email address or comma-separated list.",
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject line.",
                },
                "body": {
                    "type": "string",
                    "description": "Email body — plain text or HTML.",
                },
                "html": {
                    "type": "boolean",
                    "description": "Set to true if body is HTML. Default: false.",
                },
                "cc": {
                    "type": "string",
                    "description": "CC recipients, comma-separated. Optional.",
                },
                "bcc": {
                    "type": "string",
                    "description": "BCC recipients, comma-separated. Optional.",
                },
            },
            "required": ["to", "subject", "body"],
        }

    # ---------------------------------------------------------------------------
    # Allowlist helpers
    # ---------------------------------------------------------------------------

    @staticmethod
    def _allowed_domains() -> list[str] | None:
        """Return lower-cased allowed domains from env, or None if unrestricted."""
        raw = os.getenv("SMTP_ALLOWED_DOMAINS", "").strip()
        if not raw:
            return None
        return [d.strip().lower() for d in raw.split(",") if d.strip()]

    @staticmethod
    def _allowed_addresses() -> list[str] | None:
        """Return lower-cased allowed exact addresses from env, or None if unrestricted."""
        raw = os.getenv("SMTP_ALLOWED_ADDRESSES", "").strip()
        if not raw:
            return None
        return [a.strip().lower() for a in raw.split(",") if a.strip()]

    def _check_recipients(self, *addr_strings: str) -> str | None:
        """Return an error message if any address violates the allowlist, else None."""
        domains = self._allowed_domains()
        addresses = self._allowed_addresses()
        if domains is None and addresses is None:
            return None  # no restrictions configured

        all_addrs: list[str] = []
        for s in addr_strings:
            if s:
                all_addrs.extend(a.strip().lower() for a in s.split(",") if a.strip())

        blocked: list[str] = []
        for addr in all_addrs:
            addr_domain = addr.split("@")[-1] if "@" in addr else ""
            ok_domain = domains is not None and addr_domain in domains
            ok_address = addresses is not None and addr in addresses
            if not (ok_domain or ok_address):
                blocked.append(addr)

        if blocked:
            return (
                f"[Error] Recipient(s) not in allowlist: {', '.join(blocked)}. "
                "Configure SMTP_ALLOWED_DOMAINS or SMTP_ALLOWED_ADDRESSES to permit them."
            )
        return None

    async def run(self, **kwargs: Any) -> str:
        to: str = kwargs.get("to", "").strip()
        subject: str = kwargs.get("subject", "").strip()
        body: str = kwargs.get("body", "")
        is_html: bool = bool(kwargs.get("html", False))
        cc: str = kwargs.get("cc", "").strip()
        bcc: str = kwargs.get("bcc", "").strip()

        if not to:
            return "[Error] 'to' is required."
        if not subject:
            return "[Error] 'subject' is required."
        if not body:
            return "[Error] 'body' is required."

        err = self._check_recipients(to, cc, bcc)
        if err:
            return err

        return await asyncio.to_thread(self._send, to, subject, body, is_html, cc, bcc)

    def _send(
        self,
        to: str,
        subject: str,
        body: str,
        is_html: bool,
        cc: str,
        bcc: str,
    ) -> str:
        host = os.environ["SMTP_HOST"]
        port = int(os.getenv("SMTP_PORT", "465"))
        ssl_mode = os.getenv("SMTP_SSL", "ssl").lower()  # ssl | tls | "" (none)
        user = os.environ["SMTP_USER"]
        password = os.environ["SMTP_PASS"]
        from_addr = os.getenv("SMTP_FROM") or user
        from_name = os.getenv("SMTP_FROM_NAME", "")

        msg = MIMEMultipart("alternative" if is_html else "mixed")
        msg["From"] = f"{from_name} <{from_addr}>" if from_name else from_addr
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
        if bcc:
            msg["Bcc"] = bcc
        msg.attach(MIMEText(body, "html" if is_html else "plain", "utf-8"))

        recipients = [a.strip() for a in to.split(",")]
        if cc:
            recipients += [a.strip() for a in cc.split(",")]
        if bcc:
            recipients += [a.strip() for a in bcc.split(",")]
        recipients = [r for r in recipients if r]

        try:
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
        except Exception as exc:
            return f"[Error] {type(exc).__name__}: {exc}"

        summary = f"Email sent to {to}"
        if cc:
            summary += f", cc: {cc}"
        return summary + "."

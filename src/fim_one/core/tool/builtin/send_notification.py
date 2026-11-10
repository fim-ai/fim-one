"""Built-in tool for sending push notifications via configured providers."""

from __future__ import annotations

from typing import Any

from ..base import BaseTool


class SendNotificationTool(BaseTool):
    """Send a push notification via Email, Slack, Lark, or WeCom.

    This tool sends through the instance-wide channels the operator configured
    with environment variables (:mod:`fim_one.core.channels.instance`).  They
    are shared by the whole instance, so the tool is registered only when the operator
    sets ``NOTIFICATION_TOOL_ENABLED``.  Email recipients go through the same
    ``SMTP_ALLOWED_*`` allowlist as ``email_send``.
    """

    @property
    def name(self) -> str:
        return "send_notification"

    @property
    def display_name(self) -> str:
        return "Send Notification"

    @property
    def category(self) -> str:
        return "general"

    @property
    def description(self) -> str:
        return (
            "Send a push notification to a messaging channel. "
            "Parameters: provider (email/slack/lark/wecom), "
            "title (notification title), body (notification body), "
            "channel (optional — required for email as recipient address, "
            "ignored for webhook-based providers like Slack/Lark/WeCom)."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": "Notification provider: email, slack, lark, or wecom.",
                    "enum": ["email", "slack", "lark", "wecom"],
                },
                "title": {
                    "type": "string",
                    "description": "Notification title / subject.",
                },
                "body": {
                    "type": "string",
                    "description": "Notification body (plain text or markdown).",
                },
                "channel": {
                    "type": "string",
                    "description": (
                        "Target channel or recipient. Required for email "
                        "(the recipient email address). Ignored for webhook providers."
                    ),
                },
            },
            "required": ["provider", "title", "body"],
        }

    def availability(self) -> tuple[bool, str | None]:
        """Available when at least one notification provider is configured."""
        from fim_one.core.channels.instance import available_instance_providers

        if available_instance_providers():
            return True, None
        return (
            False,
            "No notification providers configured. Set SLACK_WEBHOOK_URL, "
            "LARK_WEBHOOK_URL, WECOM_WEBHOOK_URL, or SMTP env vars.",
        )

    async def run(self, **kwargs: Any) -> str:
        from fim_one.core.channels.instance import (
            available_instance_providers,
            instance_channel,
        )

        provider_name: str = kwargs.get("provider", "").strip()
        title: str = kwargs.get("title", "").strip()
        body: str = kwargs.get("body", "").strip()
        channel: str | None = kwargs.get("channel", "").strip() or None

        if not provider_name:
            return "[Error] 'provider' is required."
        if not title:
            return "[Error] 'title' is required."
        if not body:
            return "[Error] 'body' is required."

        if provider_name == "email":
            from .email_send import EmailSendTool, agent_email_permitted

            if not agent_email_permitted():
                return (
                    "[Error] Email from agents is disabled on this instance. "
                    "Configure SMTP_ALLOWED_DOMAINS / SMTP_ALLOWED_ADDRESSES "
                    "or set EMAIL_TOOL_ENABLED=true."
                )
            err = EmailSendTool()._check_recipients(channel or "")
            if err:
                return err

        target = instance_channel(provider_name, recipient=channel)
        if target is None:
            return (
                f"[Error] Notification provider '{provider_name}' is not configured. "
                f"Available: {', '.join(available_instance_providers()) or '(none)'}"
            )

        result = await target.send_notice(title, body)
        if result.ok:
            return f"Notification sent via {provider_name}."
        return f"[Error] {result.error or 'Unknown error'}"

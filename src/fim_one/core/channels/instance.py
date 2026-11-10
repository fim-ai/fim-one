"""Instance-wide channels the operator configured through environment variables.

Deprecated: channels belong to an organization and are managed by its
admins (``channels`` table). The three webhook variables below, and the
instance SMTP account as a notification target, are kept for one release
so an existing deployment keeps sending while its operator recreates them
as organization channels; this module and the variables go away after that.

It is the only place that reads ``SLACK_WEBHOOK_URL``, ``LARK_WEBHOOK_URL``
and ``WECOM_WEBHOOK_URL``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .base import BaseChannel
from .email import EmailChannel, smtp_configured
from .webhook import (
    LarkWebhookChannel,
    SlackWebhookChannel,
    WebhookChannel,
    WeComWebhookChannel,
)

__all__ = [
    "InstanceProvider",
    "available_instance_providers",
    "instance_channel",
    "instance_providers",
    "instance_webhooks",
]

# provider name -> (environment variable, channel type)
_WEBHOOKS: dict[str, tuple[str, type[WebhookChannel]]] = {
    "slack": ("SLACK_WEBHOOK_URL", SlackWebhookChannel),
    "lark": ("LARK_WEBHOOK_URL", LarkWebhookChannel),
    "wecom": ("WECOM_WEBHOOK_URL", WeComWebhookChannel),
}


@dataclass(frozen=True)
class InstanceProvider:
    """One instance-wide notification target and whether it is set up."""

    name: str
    display_name: str
    description: str
    configured: bool


class _OperatorEmailChannel(EmailChannel):
    """Mail to an address the caller names, from the instance SMTP account.

    The callers decide who may send where: the admin endpoints are
    admin-only, and the agent tool applies the recipient allowlist itself.
    """

    @classmethod
    def policy_error(cls, recipients: object) -> str | None:
        return None


def _webhook(name: str) -> WebhookChannel | None:
    env_var, channel_type = _WEBHOOKS[name]
    url = os.getenv(env_var, "").strip()
    return channel_type({"webhook_url": url}) if channel_type.url_allowed(url) else None


def instance_providers() -> list[InstanceProvider]:
    """The four instance-wide providers, configured or not."""
    return [
        InstanceProvider(
            name="email",
            display_name="Email (SMTP)",
            description=(
                "Send email notifications via SMTP. Requires SMTP_HOST, SMTP_USER, SMTP_PASS."
            ),
            configured=smtp_configured(),
        ),
        InstanceProvider(
            name="slack",
            display_name="Slack",
            description=(
                "Send notifications to Slack via incoming webhook. Requires SLACK_WEBHOOK_URL."
            ),
            configured=_webhook("slack") is not None,
        ),
        InstanceProvider(
            name="lark",
            display_name="Lark (Feishu)",
            description=(
                "Send notifications to Lark/Feishu via incoming webhook. "
                "Requires LARK_WEBHOOK_URL."
            ),
            configured=_webhook("lark") is not None,
        ),
        InstanceProvider(
            name="wecom",
            display_name="WeCom (Enterprise WeChat)",
            description=(
                "Send notifications to WeCom via incoming webhook. Requires WECOM_WEBHOOK_URL."
            ),
            configured=_webhook("wecom") is not None,
        ),
    ]


def available_instance_providers() -> list[str]:
    """Names of the providers that are configured."""
    return [p.name for p in instance_providers() if p.configured]


def instance_channel(name: str, *, recipient: str | None = None) -> BaseChannel | None:
    """The channel of provider *name*; ``None`` when unknown or not configured.

    *recipient* is the address an ``email`` notification goes to.
    """
    if name == "email":
        return _OperatorEmailChannel({"to": recipient or ""}) if smtp_configured() else None
    if name in _WEBHOOKS:
        return _webhook(name)
    return None


def instance_webhooks() -> dict[str, WebhookChannel]:
    """The configured instance-wide webhooks, by provider name."""
    channels = {name: _webhook(name) for name in _WEBHOOKS}
    return {name: channel for name, channel in channels.items() if channel is not None}

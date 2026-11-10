"""Incoming-webhook channels: Lark / Feishu, Slack and WeCom group bots.

Each posts to one webhook URL (``config["webhook_url"]``) that the platform
issued for one chat. They are outbound only: nothing comes back, so they
carry notices and completion summaries but not approvals.
"""

from __future__ import annotations

import abc
import logging
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx

from .base import (
    BaseChannel,
    ChannelSendResult,
    CompletionSummary,
    completion_notice,
    neutralise_lark_mentions,
)

__all__ = [
    "LarkWebhookChannel",
    "SlackWebhookChannel",
    "WeComWebhookChannel",
    "WebhookChannel",
]

logger = logging.getLogger(__name__)

WEBHOOK_URL_INVALID = "channel_webhook_url_invalid"


class WebhookChannel(BaseChannel):
    """A channel that delivers by POSTing JSON to a platform webhook URL."""

    # Shown in error messages ("Slack API returned ...").
    label: ClassVar[str] = "Webhook"
    # The only hosts a URL of this type may point at. The server makes the
    # request, so anything else would let a channel reach internal services.
    allowed_hosts: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def url_allowed(cls, url: str) -> bool:
        """Whether *url* is an https URL on one of this platform's hosts."""
        try:
            parts = urlsplit(url.strip())
            port = parts.port
        except ValueError:
            return False
        return (
            parts.scheme == "https"
            and parts.hostname in cls.allowed_hosts
            and port in (None, 443)
            # No userinfo at all: ``https://:pw@host/`` has an empty user
            # name and still carries a password.
            and "@" not in parts.netloc
            and bool(parts.path.strip("/"))
        )

    @classmethod
    def config_error(cls, config: dict[str, Any]) -> str | None:
        if not cls.url_allowed(str(config.get("webhook_url") or "")):
            return WEBHOOK_URL_INVALID
        return None

    def webhook_url(self) -> str | None:
        """The configured URL, or ``None`` when it is missing or not allowed."""
        url = str(self.config.get("webhook_url") or "").strip()
        return url if self.url_allowed(url) else None

    @abc.abstractmethod
    def build_payload(self, title: str, body: str) -> dict[str, Any]:
        """The platform's JSON body for a titled message."""

    @abc.abstractmethod
    def delivery_error(self, response: httpx.Response) -> str | None:
        """``None`` when the platform accepted the message, else what it said."""

    async def send_message(self, payload: dict[str, Any]) -> ChannelSendResult:
        """POST *payload*, a body already in the platform's format."""
        url = self.webhook_url()
        if url is None:
            return ChannelSendResult(
                ok=False, error=f"{self.label} webhook URL is not configured."
            )
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(url, json=payload)
            error = self.delivery_error(response)
        except Exception as exc:
            logger.exception("%s notification failed", self.label)
            return ChannelSendResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        if error is not None:
            return ChannelSendResult(ok=False, error=error)
        return ChannelSendResult(ok=True)

    async def send_notice(self, title: str, body: str) -> ChannelSendResult:
        return await self.send_message(self.build_payload(title, body))

    async def send_completion(self, summary: CompletionSummary) -> ChannelSendResult:
        title, body = completion_notice(summary)
        return await self.send_notice(title, body)

    # -- Inbound: none --

    async def verify_signature(self, body: bytes, headers: dict[str, str]) -> bool:
        """A webhook channel receives nothing, so no callback is authentic."""
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


class LarkWebhookChannel(WebhookChannel):
    """A Lark / Feishu custom-bot webhook; messages are interactive cards."""

    type = "lark_webhook"
    label = "Lark"
    allowed_hosts = ("open.feishu.cn", "open.larksuite.com")

    def build_payload(self, title: str, body: str) -> dict[str, Any]:
        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title[:100]},
                    "template": "blue",
                },
                # The body carries text a user, a model or a tool wrote: an
                # ``<at id=all></at>`` in it must not notify the group.
                "elements": [
                    {"tag": "markdown", "content": neutralise_lark_mentions(body)[:4000]}
                ],
            },
        }

    def delivery_error(self, response: httpx.Response) -> str | None:
        data = response.json()
        if data.get("code") == 0 or data.get("StatusCode") == 0:
            return None
        return f"Lark API error: {data.get('msg', response.text[:200])}"


class SlackWebhookChannel(WebhookChannel):
    """A Slack incoming webhook; messages use Block Kit (header + section)."""

    type = "slack_webhook"
    label = "Slack"
    allowed_hosts = ("hooks.slack.com",)

    def build_payload(self, title: str, body: str) -> dict[str, Any]:
        return {
            "blocks": [
                {
                    "type": "header",
                    # Slack header limit
                    "text": {"type": "plain_text", "text": title[:150], "emoji": True},
                },
                {
                    "type": "section",
                    # Slack section text limit
                    "text": {"type": "mrkdwn", "text": self.escape(body)[:3000]},
                },
            ]
        }

    @staticmethod
    def escape(text: str) -> str:
        """*text* with Slack's three control characters escaped.

        In ``mrkdwn`` everything between ``<`` and ``>`` is markup:
        ``<!channel>``, ``<!here>``, ``<@U…>`` and ``<!subteam^…>`` notify
        people, ``<url|label>`` hides where a link goes. The body carries
        text a user, a model or a tool wrote, so it is escaped the way Slack
        documents for text that is not markup.
        """
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def delivery_error(self, response: httpx.Response) -> str | None:
        if response.status_code == 200 and response.text == "ok":
            return None
        return f"Slack API returned {response.status_code}: {response.text[:200]}"


class WeComWebhookChannel(WebhookChannel):
    """A WeCom (Enterprise WeChat) group-bot webhook; messages are markdown."""

    type = "wecom_webhook"
    label = "WeCom"
    allowed_hosts = ("qyapi.weixin.qq.com",)

    def build_payload(self, title: str, body: str) -> dict[str, Any]:
        # WeCom markdown supports: bold, links, quotes, colored text
        # ``<@userid>`` in WeCom markdown notifies that member.
        content = self.neutralise_mentions(f"**{title}**\n\n{body}")
        return {
            "msgtype": "markdown",
            "markdown": {"content": content[:4096]},  # WeCom limit
        }

    @staticmethod
    def neutralise_mentions(text: str) -> str:
        """*text* with ``<@userid>`` mentions made inert (kept readable)."""
        return text.replace("<@", "<\u200b@")

    def delivery_error(self, response: httpx.Response) -> str | None:
        data = response.json()
        if data.get("errcode") == 0:
            return None
        return f"WeCom API error: {data.get('errmsg', response.text[:200])}"

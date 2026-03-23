"""Slack notification provider using incoming webhook."""

from __future__ import annotations

import logging
import os

import httpx

from typing import Any

from .base import NotificationMessage, NotificationProvider

logger = logging.getLogger(__name__)


class SlackNotificationProvider(NotificationProvider):
    """Send notifications to Slack via an incoming webhook URL.

    Configure via env var ``SLACK_WEBHOOK_URL``.  The message is formatted
    using Slack Block Kit for rich display (header + section body).
    """

    @property
    def name(self) -> str:
        return "slack"

    @property
    def display_name(self) -> str:
        return "Slack"

    @property
    def description(self) -> str:
        return "Send notifications to Slack via incoming webhook. Requires SLACK_WEBHOOK_URL."

    def validate_config(self) -> bool:
        url = os.getenv("SLACK_WEBHOOK_URL", "").strip()
        return url.startswith("https://hooks.slack.com/")

    async def send(self, message: NotificationMessage) -> dict[str, Any]:
        webhook_url = os.getenv("SLACK_WEBHOOK_URL", "").strip()
        if not webhook_url:
            return {"ok": False, "error": "SLACK_WEBHOOK_URL is not configured."}

        payload = self._build_payload(message)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(webhook_url, json=payload)
            if resp.status_code == 200 and resp.text == "ok":
                return {"ok": True, "provider": "slack"}
            return {
                "ok": False,
                "error": f"Slack API returned {resp.status_code}: {resp.text[:200]}",
            }
        except Exception as exc:
            logger.exception("Slack notification failed")
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # ------------------------------------------------------------------
    # Payload builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_payload(message: NotificationMessage) -> dict[str, Any]:
        """Build a Slack Block Kit payload."""
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": message.title[:150],  # Slack header limit
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": message.body[:3000],  # Slack section text limit
                },
            },
        ]
        return {"blocks": blocks}

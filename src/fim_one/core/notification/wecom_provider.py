"""WeCom (Enterprise WeChat) notification provider using incoming webhook."""

from __future__ import annotations

import logging
import os

import httpx

from typing import Any

from .base import NotificationMessage, NotificationProvider

logger = logging.getLogger(__name__)


class WeComNotificationProvider(NotificationProvider):
    """Send notifications to WeCom via an incoming webhook URL.

    Configure via env var ``WECOM_WEBHOOK_URL``.  The message is formatted
    as a WeCom markdown message.
    """

    @property
    def name(self) -> str:
        return "wecom"

    @property
    def display_name(self) -> str:
        return "WeCom (Enterprise WeChat)"

    @property
    def description(self) -> str:
        return "Send notifications to WeCom via incoming webhook. Requires WECOM_WEBHOOK_URL."

    def validate_config(self) -> bool:
        url = os.getenv("WECOM_WEBHOOK_URL", "").strip()
        return url.startswith("https://qyapi.weixin.qq.com/")

    async def send(self, message: NotificationMessage) -> dict[str, Any]:
        webhook_url = os.getenv("WECOM_WEBHOOK_URL", "").strip()
        if not webhook_url:
            return {"ok": False, "error": "WECOM_WEBHOOK_URL is not configured."}

        payload = self._build_payload(message)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(webhook_url, json=payload)
            data = resp.json()
            if data.get("errcode") == 0:
                return {"ok": True, "provider": "wecom"}
            return {
                "ok": False,
                "error": f"WeCom API error: {data.get('errmsg', resp.text[:200])}",
            }
        except Exception as exc:
            logger.exception("WeCom notification failed")
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # ------------------------------------------------------------------
    # Payload builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_payload(message: NotificationMessage) -> dict[str, Any]:
        """Build a WeCom markdown message payload."""
        # WeCom markdown supports: bold, links, quotes, colored text
        markdown_content = f"**{message.title}**\n\n{message.body}"
        return {
            "msgtype": "markdown",
            "markdown": {
                "content": markdown_content[:4096],  # WeCom limit
            },
        }

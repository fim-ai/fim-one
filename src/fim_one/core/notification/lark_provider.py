"""Lark (Feishu) notification provider using incoming webhook."""

from __future__ import annotations

import logging
import os

import httpx

from typing import Any

from .base import NotificationMessage, NotificationProvider

logger = logging.getLogger(__name__)


class LarkNotificationProvider(NotificationProvider):
    """Send notifications to Lark/Feishu via an incoming webhook URL.

    Configure via env var ``LARK_WEBHOOK_URL``.  The message is formatted
    as a Lark interactive card for rich display.
    """

    @property
    def name(self) -> str:
        return "lark"

    @property
    def display_name(self) -> str:
        return "Lark (Feishu)"

    @property
    def description(self) -> str:
        return "Send notifications to Lark/Feishu via incoming webhook. Requires LARK_WEBHOOK_URL."

    def validate_config(self) -> bool:
        url = os.getenv("LARK_WEBHOOK_URL", "").strip()
        return url.startswith("https://open.feishu.cn/") or url.startswith("https://open.larksuite.com/")

    async def send(self, message: NotificationMessage) -> dict[str, Any]:
        webhook_url = os.getenv("LARK_WEBHOOK_URL", "").strip()
        if not webhook_url:
            return {"ok": False, "error": "LARK_WEBHOOK_URL is not configured."}

        payload = self._build_payload(message)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(webhook_url, json=payload)
            data = resp.json()
            if data.get("code") == 0 or data.get("StatusCode") == 0:
                return {"ok": True, "provider": "lark"}
            return {
                "ok": False,
                "error": f"Lark API error: {data.get('msg', resp.text[:200])}",
            }
        except Exception as exc:
            logger.exception("Lark notification failed")
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # ------------------------------------------------------------------
    # Payload builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_payload(message: NotificationMessage) -> dict[str, Any]:
        """Build a Lark interactive card message."""
        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": message.title[:100],
                    },
                    "template": "blue",
                },
                "elements": [
                    {
                        "tag": "markdown",
                        "content": message.body[:4000],
                    },
                ],
            },
        }

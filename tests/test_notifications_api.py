"""Tests for the admin ``/api/notifications`` endpoints (instance-wide channels)."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.core.channels import ChannelSendResult
from fim_one.web.api import notifications as api
from fim_one.web.schemas.notification import NotificationSendRequest, NotificationTestRequest

_NO_PROVIDERS = {
    "SMTP_HOST": "",
    "SLACK_WEBHOOK_URL": "",
    "LARK_WEBHOOK_URL": "",
    "WECOM_WEBHOOK_URL": "",
}
_ADMIN: Any = SimpleNamespace(email="root@x.io", username="root")


class TestProviders:
    @pytest.mark.asyncio
    async def test_all_four_listed_with_their_state(self) -> None:
        env = {**_NO_PROVIDERS, "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T/B/x"}
        with patch.dict(os.environ, env):
            providers = await api.list_providers(_ADMIN)
        assert {p.name: p.configured for p in providers} == {
            "email": False,
            "slack": True,
            "lark": False,
            "wecom": False,
        }


class TestSend:
    @pytest.mark.asyncio
    async def test_unconfigured_provider_is_an_error_body(self) -> None:
        with patch.dict(os.environ, _NO_PROVIDERS):
            sent = await api.send_notification(
                NotificationSendRequest(provider="slack", title="t", body="b"), _ADMIN
            )
            tested = await api.test_notification(NotificationTestRequest(provider="slack"), _ADMIN)
            unknown = await api.test_notification(NotificationTestRequest(provider="pager"), _ADMIN)
        assert sent["ok"] is False and "not configured" in str(sent["error"])
        assert tested["ok"] is False and "not configured" in str(tested["error"])
        assert unknown == {"ok": False, "error": "Unknown provider: 'pager'."}

    @pytest.mark.asyncio
    async def test_send_goes_through_the_instance_channel(self) -> None:
        channel = MagicMock()
        channel.send_notice = AsyncMock(return_value=ChannelSendResult(ok=True))
        with patch.object(api, "instance_channel", return_value=channel) as lookup:
            result = await api.send_notification(
                NotificationSendRequest(
                    provider="email", title="Hi", body="Hello", channel="ops@x.io"
                ),
                _ADMIN,
            )
        lookup.assert_called_once_with("email", recipient="ops@x.io")
        channel.send_notice.assert_awaited_once_with("Hi", "Hello")
        assert result == {"ok": True, "provider": "email"}

    @pytest.mark.asyncio
    async def test_failed_send_reports_the_error(self) -> None:
        channel = MagicMock()
        channel.send_notice = AsyncMock(
            return_value=ChannelSendResult(ok=False, error="Slack API returned 404: no_service")
        )
        with patch.object(api, "instance_channel", return_value=channel):
            result = await api.test_notification(NotificationTestRequest(provider="slack"), _ADMIN)
        assert result == {"ok": False, "error": "Slack API returned 404: no_service"}
        title, body = channel.send_notice.await_args.args
        assert title == "FIM One Test Notification"
        assert "root@x.io" in body and "Slack" in body

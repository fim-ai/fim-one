"""Tests for the ``send_notification`` built-in tool and who is offered it."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.core.channels import ChannelSendResult

# ---------------------------------------------------------------------------
# SendNotificationTool tests
# ---------------------------------------------------------------------------


class TestSendNotificationTool:
    def test_tool_properties(self):
        from fim_one.core.tool.builtin.send_notification import SendNotificationTool

        tool = SendNotificationTool()
        assert tool.name == "send_notification"
        assert tool.category == "general"
        schema = tool.parameters_schema
        assert "provider" in schema["properties"]
        assert "title" in schema["properties"]
        assert "body" in schema["properties"]
        assert "channel" in schema["properties"]

    @pytest.mark.asyncio
    async def test_run_missing_provider(self):
        from fim_one.core.tool.builtin.send_notification import SendNotificationTool

        tool = SendNotificationTool()
        result = await tool.run(provider="", title="Hi", body="Hello")
        assert "[Error]" in result

    @pytest.mark.asyncio
    async def test_run_missing_title(self):
        from fim_one.core.tool.builtin.send_notification import SendNotificationTool

        tool = SendNotificationTool()
        result = await tool.run(provider="slack", title="", body="Hello")
        assert "[Error]" in result

    @pytest.mark.asyncio
    async def test_run_unknown_provider(self):
        from fim_one.core.tool.builtin.send_notification import SendNotificationTool

        tool = SendNotificationTool()
        # Ensure empty registry
        with patch.dict(os.environ, {
            "SLACK_WEBHOOK_URL": "",
            "LARK_WEBHOOK_URL": "",
            "WECOM_WEBHOOK_URL": "",
            "SMTP_HOST": "",
        }):
            result = await tool.run(provider="slack", title="Test", body="Hello")
        assert "[Error]" in result

    @pytest.mark.asyncio
    async def test_run_success(self):
        from fim_one.core.tool.builtin.send_notification import SendNotificationTool

        tool = SendNotificationTool()

        target = MagicMock()
        target.send_notice = AsyncMock(return_value=ChannelSendResult(ok=True))
        with patch(
            "fim_one.core.channels.instance.instance_channel", return_value=target
        ) as lookup:
            result = await tool.run(provider="slack", title="Test", body="Hello")
        lookup.assert_called_once_with("slack", recipient=None)
        target.send_notice.assert_awaited_once_with("Test", "Hello")
        assert "sent via slack" in result.lower()

    def test_availability_no_providers(self):
        from fim_one.core.tool.builtin.send_notification import SendNotificationTool

        tool = SendNotificationTool()
        with patch.dict(os.environ, {
            "SLACK_WEBHOOK_URL": "",
            "LARK_WEBHOOK_URL": "",
            "WECOM_WEBHOOK_URL": "",
            "SMTP_HOST": "",
        }):
            available, reason = tool.availability()
        assert available is False
        assert reason is not None

    def test_availability_with_provider(self):
        from fim_one.core.tool.builtin.send_notification import SendNotificationTool

        tool = SendNotificationTool()
        with patch.dict(os.environ, {
            "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T/B/x",
        }):
            available, reason = tool.availability()
        assert available is True
        assert reason is None


# ---------------------------------------------------------------------------
# Exposure: the providers are instance-wide channels owned by the operator
# ---------------------------------------------------------------------------


class TestNotificationExposure:
    def _discovered_names(self) -> set[str]:
        from fim_one.core.tool.builtin import discover_builtin_tools

        return {t.name for t in discover_builtin_tools()}

    def test_tool_not_offered_by_default(self):
        with patch.dict(os.environ, {"LARK_WEBHOOK_URL": "https://open.feishu.cn/x"}):
            os.environ.pop("NOTIFICATION_TOOL_ENABLED", None)
            assert "send_notification" not in self._discovered_names()

    def test_tool_offered_when_operator_opts_in(self):
        with patch.dict(os.environ, {"NOTIFICATION_TOOL_ENABLED": "true"}):
            assert "send_notification" in self._discovered_names()

    def test_test_endpoint_requires_admin(self):
        import inspect

        from fim_one.web.api.notifications import test_notification
        from fim_one.web.auth import get_current_admin

        dep = inspect.signature(test_notification).parameters["user"].default
        assert dep.dependency is get_current_admin


_SMTP_ENV = {"SMTP_HOST": "smtp.x.io", "SMTP_USER": "u@x.io", "SMTP_PASS": "p"}


class TestEmailToolExposure:

    def _discovered_names(self, env: dict[str, str]) -> set[str]:
        from fim_one.core.tool.builtin import discover_builtin_tools

        with patch.dict(os.environ, env):
            for k in ("SMTP_ALLOWED_DOMAINS", "SMTP_ALLOWED_ADDRESSES", "EMAIL_TOOL_ENABLED"):
                if k not in env:
                    os.environ.pop(k, None)
            return {t.name for t in discover_builtin_tools()}

    def test_not_offered_without_allowlist(self):
        assert "email_send" not in self._discovered_names(dict(_SMTP_ENV))

    def test_offered_with_domain_allowlist(self):
        env = {**_SMTP_ENV, "SMTP_ALLOWED_DOMAINS": "corp.internal"}
        assert "email_send" in self._discovered_names(env)

    def test_offered_with_address_allowlist(self):
        env = {**_SMTP_ENV, "SMTP_ALLOWED_ADDRESSES": "ops@corp.internal"}
        assert "email_send" in self._discovered_names(env)

    def test_offered_when_operator_opts_in(self):
        env = {**_SMTP_ENV, "EMAIL_TOOL_ENABLED": "true"}
        assert "email_send" in self._discovered_names(env)


class TestSendNotificationEmailGate:
    @pytest.mark.asyncio
    async def test_email_refused_without_allowlist_or_opt_in(self):
        from fim_one.core.tool.builtin.send_notification import SendNotificationTool

        with patch.dict(os.environ, dict(_SMTP_ENV)):
            for k in ("SMTP_ALLOWED_DOMAINS", "SMTP_ALLOWED_ADDRESSES", "EMAIL_TOOL_ENABLED"):
                os.environ.pop(k, None)
            with patch("fim_one.core.channels.instance.instance_channel") as get:
                result = await SendNotificationTool().run(
                    provider="email", title="t", body="b", channel="anyone@outside.io"
                )
                get.assert_not_called()
        assert "[Error]" in result

    @pytest.mark.asyncio
    async def test_email_recipient_outside_allowlist_refused(self):
        from fim_one.core.tool.builtin.send_notification import SendNotificationTool

        env = {**_SMTP_ENV, "SMTP_ALLOWED_DOMAINS": "corp.internal"}
        with (
            patch.dict(os.environ, env),
            patch("fim_one.core.channels.instance.instance_channel") as get,
        ):
            result = await SendNotificationTool().run(
                provider="email", title="t", body="b", channel="anyone@outside.io"
            )
            get.assert_not_called()
        assert "not in allowlist" in result

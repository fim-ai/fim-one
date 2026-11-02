"""Tests for the notification system.

Covers:
- Registry auto-discovery from env vars
- Each provider's message formatting (mock httpx / smtp)
- Missing config handling (graceful non-registration)
- Invalid provider name error
- SendNotificationTool integration
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.core.notification.base import NotificationMessage
from fim_one.core.notification.email_provider import EmailNotificationProvider
from fim_one.core.notification.lark_provider import LarkNotificationProvider
from fim_one.core.notification.registry import (
    NotificationRegistry,
    reset_notification_registry,
)
from fim_one.core.notification.slack_provider import SlackNotificationProvider
from fim_one.core.notification.wecom_provider import WeComNotificationProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registry():
    """Reset the module-level singleton after every test."""
    reset_notification_registry()
    yield
    reset_notification_registry()


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


class TestNotificationRegistry:
    def test_register_and_get(self):
        registry = NotificationRegistry()
        provider = SlackNotificationProvider()
        registry.register("slack", provider)
        assert registry.get("slack") is provider
        assert registry.get("nonexistent") is None

    def test_list_available(self):
        registry = NotificationRegistry()
        assert registry.list_available() == []

        registry.register("slack", SlackNotificationProvider())
        registry.register("lark", LarkNotificationProvider())
        assert sorted(registry.list_available()) == ["lark", "slack"]

    def test_provider_info(self):
        registry = NotificationRegistry()
        registry.register("slack", SlackNotificationProvider())
        info = registry.provider_info()
        assert len(info) == 1
        assert info[0]["name"] == "slack"
        assert info[0]["configured"] is True

    @pytest.mark.asyncio
    async def test_send_unknown_provider_raises(self):
        registry = NotificationRegistry()
        msg = NotificationMessage(title="Hi", body="Hello")
        with pytest.raises(KeyError, match="not configured"):
            await registry.send("nonexistent", msg)

    def test_from_env_no_config(self):
        """With no env vars, no providers should be registered."""
        env_overrides = {
            "SMTP_HOST": "",
            "SMTP_USER": "",
            "SMTP_PASS": "",
            "SLACK_WEBHOOK_URL": "",
            "LARK_WEBHOOK_URL": "",
            "WECOM_WEBHOOK_URL": "",
        }
        with patch.dict(os.environ, env_overrides, clear=False):
            registry = NotificationRegistry.from_env()
        assert registry.list_available() == []

    def test_from_env_slack_configured(self):
        env = {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T/B/xxx"}
        with patch.dict(os.environ, env, clear=False):
            registry = NotificationRegistry.from_env()
        assert "slack" in registry.list_available()

    def test_from_env_lark_configured(self):
        env = {"LARK_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"}
        with patch.dict(os.environ, env, clear=False):
            registry = NotificationRegistry.from_env()
        assert "lark" in registry.list_available()

    def test_from_env_wecom_configured(self):
        env = {"WECOM_WEBHOOK_URL": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"}
        with patch.dict(os.environ, env, clear=False):
            registry = NotificationRegistry.from_env()
        assert "wecom" in registry.list_available()

    def test_from_env_email_configured(self):
        env = {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_USER": "user@example.com",
            "SMTP_PASS": "secret",
        }
        with patch.dict(os.environ, env, clear=False):
            registry = NotificationRegistry.from_env()
        assert "email" in registry.list_available()

    def test_from_env_multiple_providers(self):
        env = {
            "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T/B/xxx",
            "LARK_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
            "WECOM_WEBHOOK_URL": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx",
            "SMTP_HOST": "smtp.example.com",
            "SMTP_USER": "user@example.com",
            "SMTP_PASS": "secret",
        }
        with patch.dict(os.environ, env, clear=False):
            registry = NotificationRegistry.from_env()
        assert sorted(registry.list_available()) == ["email", "lark", "slack", "wecom"]


# ---------------------------------------------------------------------------
# Provider validate_config tests
# ---------------------------------------------------------------------------


class TestProviderValidation:
    def test_email_requires_all_three(self):
        provider = EmailNotificationProvider()
        with patch.dict(os.environ, {"SMTP_HOST": "", "SMTP_USER": "", "SMTP_PASS": ""}, clear=False):
            assert provider.validate_config() is False
        with patch.dict(os.environ, {"SMTP_HOST": "x", "SMTP_USER": "y", "SMTP_PASS": "z"}, clear=False):
            assert provider.validate_config() is True

    def test_slack_requires_valid_url(self):
        provider = SlackNotificationProvider()
        with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": ""}, clear=False):
            assert provider.validate_config() is False
        with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://example.com"}, clear=False):
            assert provider.validate_config() is False
        with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T/B/x"}, clear=False):
            assert provider.validate_config() is True

    def test_lark_requires_valid_url(self):
        provider = LarkNotificationProvider()
        with patch.dict(os.environ, {"LARK_WEBHOOK_URL": ""}, clear=False):
            assert provider.validate_config() is False
        with patch.dict(os.environ, {"LARK_WEBHOOK_URL": "https://open.feishu.cn/hook"}, clear=False):
            assert provider.validate_config() is True
        with patch.dict(os.environ, {"LARK_WEBHOOK_URL": "https://open.larksuite.com/hook"}, clear=False):
            assert provider.validate_config() is True

    def test_wecom_requires_valid_url(self):
        provider = WeComNotificationProvider()
        with patch.dict(os.environ, {"WECOM_WEBHOOK_URL": ""}, clear=False):
            assert provider.validate_config() is False
        with patch.dict(os.environ, {"WECOM_WEBHOOK_URL": "https://qyapi.weixin.qq.com/send"}, clear=False):
            assert provider.validate_config() is True


# ---------------------------------------------------------------------------
# Provider message formatting tests
# ---------------------------------------------------------------------------


class TestSlackFormatting:
    def test_build_payload(self):
        msg = NotificationMessage(title="Alert", body="Server is down")
        payload = SlackNotificationProvider._build_payload(msg)
        assert "blocks" in payload
        assert payload["blocks"][0]["type"] == "header"
        assert payload["blocks"][0]["text"]["text"] == "Alert"
        assert payload["blocks"][1]["type"] == "section"
        assert payload["blocks"][1]["text"]["text"] == "Server is down"

    def test_title_truncation(self):
        msg = NotificationMessage(title="A" * 200, body="body")
        payload = SlackNotificationProvider._build_payload(msg)
        assert len(payload["blocks"][0]["text"]["text"]) == 150


class TestLarkFormatting:
    def test_build_payload(self):
        msg = NotificationMessage(title="Deploy", body="v1.0 released")
        payload = LarkNotificationProvider._build_payload(msg)
        assert payload["msg_type"] == "interactive"
        assert payload["card"]["header"]["title"]["content"] == "Deploy"
        assert payload["card"]["elements"][0]["content"] == "v1.0 released"


class TestWeComFormatting:
    def test_build_payload(self):
        msg = NotificationMessage(title="Alert", body="Check logs")
        payload = WeComNotificationProvider._build_payload(msg)
        assert payload["msgtype"] == "markdown"
        assert "**Alert**" in payload["markdown"]["content"]
        assert "Check logs" in payload["markdown"]["content"]


# ---------------------------------------------------------------------------
# Provider send tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestSlackSend:
    @pytest.mark.asyncio
    async def test_send_success(self):
        provider = SlackNotificationProvider()
        msg = NotificationMessage(title="Test", body="Hello Slack")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "ok"

        with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T/B/x"}):
            with patch("fim_one.core.notification.slack_provider.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_resp)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client

                result = await provider.send(msg)
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_send_missing_url(self):
        provider = SlackNotificationProvider()
        msg = NotificationMessage(title="Test", body="Hello")
        with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": ""}):
            result = await provider.send(msg)
        assert result["ok"] is False
        assert "not configured" in result["error"]


class TestLarkSend:
    @pytest.mark.asyncio
    async def test_send_success(self):
        provider = LarkNotificationProvider()
        msg = NotificationMessage(title="Test", body="Hello Lark")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": 0, "msg": "success"}

        with patch.dict(os.environ, {"LARK_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"}):
            with patch("fim_one.core.notification.lark_provider.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_resp)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client

                result = await provider.send(msg)
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_send_api_error(self):
        provider = LarkNotificationProvider()
        msg = NotificationMessage(title="Test", body="Hello")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": 9499, "msg": "token invalid"}
        mock_resp.text = '{"code": 9499}'

        with patch.dict(os.environ, {"LARK_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"}):
            with patch("fim_one.core.notification.lark_provider.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_resp)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client

                result = await provider.send(msg)
        assert result["ok"] is False
        assert "token invalid" in result["error"]


class TestWeComSend:
    @pytest.mark.asyncio
    async def test_send_success(self):
        provider = WeComNotificationProvider()
        msg = NotificationMessage(title="Test", body="Hello WeCom")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"errcode": 0, "errmsg": "ok"}

        with patch.dict(os.environ, {"WECOM_WEBHOOK_URL": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"}):
            with patch("fim_one.core.notification.wecom_provider.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_resp)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client

                result = await provider.send(msg)
        assert result["ok"] is True


class TestEmailSend:
    @pytest.mark.asyncio
    async def test_send_missing_channel(self):
        provider = EmailNotificationProvider()
        msg = NotificationMessage(title="Test", body="Hello")
        result = await provider.send(msg)
        assert result["ok"] is False
        assert "channel" in result["error"]

    @pytest.mark.asyncio
    async def test_send_success(self):
        provider = EmailNotificationProvider()
        msg = NotificationMessage(title="Test", body="Hello", channel="user@example.com")

        with patch.dict(os.environ, {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "465",
            "SMTP_SSL": "ssl",
            "SMTP_USER": "sender@example.com",
            "SMTP_PASS": "secret",
        }):
            with patch("fim_one.core.notification.email_provider.smtplib.SMTP_SSL") as mock_smtp:
                mock_server = MagicMock()
                mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
                mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

                result = await provider.send(msg)
        assert result["ok"] is True
        assert result["to"] == "user@example.com"


# ---------------------------------------------------------------------------
# SendNotificationTool tests
# ---------------------------------------------------------------------------


class TestSendNotificationTool:
    def test_tool_properties(self):
        from fim_one.core.notification.registry import reset_notification_registry
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
            reset_notification_registry()
            result = await tool.run(provider="slack", title="Test", body="Hello")
        assert "[Error]" in result

    @pytest.mark.asyncio
    async def test_run_success(self):
        from fim_one.core.tool.builtin.send_notification import SendNotificationTool

        tool = SendNotificationTool()

        # Mock the registry to return success
        mock_result = {"ok": True, "provider": "slack"}
        with patch("fim_one.core.notification.get_notification_registry") as mock_get:
            mock_registry = MagicMock()
            mock_registry.send = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_registry

            result = await tool.run(provider="slack", title="Test", body="Hello")
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
            reset_notification_registry()
            available, reason = tool.availability()
        assert available is False
        assert reason is not None

    def test_availability_with_provider(self):
        from fim_one.core.tool.builtin.send_notification import SendNotificationTool

        tool = SendNotificationTool()
        with patch.dict(os.environ, {
            "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T/B/x",
        }):
            reset_notification_registry()
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
            with patch("fim_one.core.notification.get_notification_registry") as get:
                result = await SendNotificationTool().run(
                    provider="email", title="t", body="b", channel="anyone@outside.io"
                )
                get.return_value.send.assert_not_called()
        assert "[Error]" in result

    @pytest.mark.asyncio
    async def test_email_recipient_outside_allowlist_refused(self):
        from fim_one.core.tool.builtin.send_notification import SendNotificationTool

        env = {**_SMTP_ENV, "SMTP_ALLOWED_DOMAINS": "corp.internal"}
        with (
            patch.dict(os.environ, env),
            patch("fim_one.core.notification.get_notification_registry") as get,
        ):
            result = await SendNotificationTool().run(
                provider="email", title="t", body="b", channel="anyone@outside.io"
            )
            get.return_value.send.assert_not_called()
        assert "not in allowlist" in result

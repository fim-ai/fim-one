"""Tests for the webhook and email channel types and the instance-wide channels.

Covers:
- which URLs each webhook type accepts
- each type's message format and how it reads the platform's answer
- the instance-wide channels built from environment variables
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.core.channels import CompletionSummary, build_channel
from fim_one.core.channels.email import EmailChannel
from fim_one.core.channels.instance import (
    available_instance_providers,
    instance_channel,
    instance_providers,
)
from fim_one.core.channels.webhook import (
    LarkWebhookChannel,
    SlackWebhookChannel,
    WebhookChannel,
    WeComWebhookChannel,
)

SLACK_URL = "https://hooks.slack.com/services/T/B/xxx"
LARK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
WECOM_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"

_SMTP = {
    "SMTP_HOST": "smtp.example.com",
    "SMTP_PORT": "465",
    "SMTP_SSL": "ssl",
    "SMTP_USER": "sender@example.com",
    "SMTP_PASS": "secret",
}

_NO_PROVIDERS = {
    "SMTP_HOST": "",
    "SMTP_USER": "",
    "SMTP_PASS": "",
    "SLACK_WEBHOOK_URL": "",
    "LARK_WEBHOOK_URL": "",
    "WECOM_WEBHOOK_URL": "",
}


def _http(response: MagicMock) -> tuple[Any, AsyncMock]:
    """A stand-in for ``httpx.AsyncClient`` whose ``post`` returns *response*."""
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return patch("fim_one.core.channels.webhook.httpx.AsyncClient", return_value=client), client


def _response(*, status: int = 200, text: str = "", data: dict[str, Any] | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.text = text
    response.json.return_value = data or {}
    return response


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestChannelTypes:
    @pytest.mark.parametrize(
        ("channel_type", "cls"),
        [
            ("lark_webhook", LarkWebhookChannel),
            ("slack_webhook", SlackWebhookChannel),
            ("wecom_webhook", WeComWebhookChannel),
            ("email", EmailChannel),
        ],
    )
    def test_types_are_registered(self, channel_type: str, cls: type) -> None:
        assert isinstance(build_channel(channel_type, {}), cls)


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------


class TestWebhookUrl:
    @pytest.mark.parametrize(
        ("cls", "url"),
        [
            (SlackWebhookChannel, SLACK_URL),
            (LarkWebhookChannel, LARK_URL),
            (LarkWebhookChannel, "https://open.larksuite.com/hook"),
            (WeComWebhookChannel, WECOM_URL),
        ],
    )
    def test_platform_url_accepted(self, cls: type[WebhookChannel], url: str) -> None:
        assert cls.url_allowed(url)
        assert cls.config_error({"webhook_url": url}) is None

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "https://example.com",
            "http://hooks.slack.com/services/T/B/x",
            "https://hooks.slack.com.evil.io/services/T/B/x",
            "https://hooks.slack.com@127.0.0.1/services/T/B/x",
            "https://hooks.slack.com:8443/services/T/B/x",
            "https://open.feishu.cn/hook",
            "http://169.254.169.254/latest/meta-data",
            "https://hooks.slack.com:notaport/x",
        ],
    )
    def test_other_url_refused(self, url: str) -> None:
        assert not SlackWebhookChannel.url_allowed(url)
        assert SlackWebhookChannel.config_error({"webhook_url": url}) == (
            "channel_webhook_url_invalid"
        )

    @pytest.mark.asyncio
    async def test_send_never_posts_to_a_url_that_is_not_allowed(self) -> None:
        channel = SlackWebhookChannel({"webhook_url": "http://127.0.0.1:8000/internal"})
        patcher, client = _http(_response(text="ok"))
        with patcher:
            result = await channel.send_notice("t", "b")
        client.post.assert_not_called()
        assert result.ok is False
        assert "not configured" in (result.error or "")


# ---------------------------------------------------------------------------
# Message formats
# ---------------------------------------------------------------------------


class TestSlackFormatting:
    def test_build_payload(self) -> None:
        payload = SlackWebhookChannel({}).build_payload("Alert", "Server is down")
        assert "blocks" in payload
        assert payload["blocks"][0]["type"] == "header"
        assert payload["blocks"][0]["text"]["text"] == "Alert"
        assert payload["blocks"][1]["type"] == "section"
        assert payload["blocks"][1]["text"]["text"] == "Server is down"

    def test_title_truncation(self) -> None:
        payload = SlackWebhookChannel({}).build_payload("A" * 200, "body")
        assert len(payload["blocks"][0]["text"]["text"]) == 150


class TestLarkFormatting:
    def test_build_payload(self) -> None:
        payload = LarkWebhookChannel({}).build_payload("Deploy", "v1.0 released")
        assert payload["msg_type"] == "interactive"
        assert payload["card"]["header"]["title"]["content"] == "Deploy"
        assert payload["card"]["elements"][0]["content"] == "v1.0 released"


class TestWeComFormatting:
    def test_build_payload(self) -> None:
        payload = WeComWebhookChannel({}).build_payload("Alert", "Check logs")
        assert payload["msgtype"] == "markdown"
        assert "**Alert**" in payload["markdown"]["content"]
        assert "Check logs" in payload["markdown"]["content"]


# ---------------------------------------------------------------------------
# Sending (mocked HTTP)
# ---------------------------------------------------------------------------


class TestSlackSend:
    @pytest.mark.asyncio
    async def test_send_success(self) -> None:
        channel = SlackWebhookChannel({"webhook_url": SLACK_URL})
        patcher, client = _http(_response(text="ok"))
        with patcher:
            result = await channel.send_notice("Test", "Hello Slack")
        assert result.ok is True
        assert client.post.await_args.args[0] == SLACK_URL
        assert client.post.await_args.kwargs["json"]["blocks"][0]["text"]["text"] == "Test"

    @pytest.mark.asyncio
    async def test_send_rejected(self) -> None:
        channel = SlackWebhookChannel({"webhook_url": SLACK_URL})
        patcher, _ = _http(_response(status=404, text="no_service"))
        with patcher:
            result = await channel.send_notice("Test", "Hello")
        assert result.ok is False
        assert "404" in (result.error or "")

    @pytest.mark.asyncio
    async def test_send_missing_url(self) -> None:
        result = await SlackWebhookChannel({}).send_notice("Test", "Hello")
        assert result.ok is False
        assert "not configured" in (result.error or "")

    @pytest.mark.asyncio
    async def test_network_failure_is_reported_not_raised(self) -> None:
        channel = SlackWebhookChannel({"webhook_url": SLACK_URL})
        patcher, client = _http(_response())
        client.post.side_effect = OSError("unreachable")
        with patcher:
            result = await channel.send_notice("Test", "Hello")
        assert result.ok is False
        assert "OSError" in (result.error or "")


class TestLarkSend:
    @pytest.mark.asyncio
    async def test_send_success(self) -> None:
        channel = LarkWebhookChannel({"webhook_url": LARK_URL})
        patcher, _ = _http(_response(data={"code": 0, "msg": "success"}))
        with patcher:
            result = await channel.send_notice("Test", "Hello Lark")
        assert result.ok is True

    @pytest.mark.asyncio
    async def test_send_api_error(self) -> None:
        channel = LarkWebhookChannel({"webhook_url": LARK_URL})
        patcher, _ = _http(
            _response(data={"code": 9499, "msg": "token invalid"}, text='{"code": 9499}')
        )
        with patcher:
            result = await channel.send_notice("Test", "Hello")
        assert result.ok is False
        assert "token invalid" in (result.error or "")


class TestWeComSend:
    @pytest.mark.asyncio
    async def test_send_success(self) -> None:
        channel = WeComWebhookChannel({"webhook_url": WECOM_URL})
        patcher, _ = _http(_response(data={"errcode": 0, "errmsg": "ok"}))
        with patcher:
            result = await channel.send_notice("Test", "Hello WeCom")
        assert result.ok is True


class TestEmailSend:
    @pytest.mark.asyncio
    async def test_send_without_recipient(self) -> None:
        result = await EmailChannel({}).send_notice("Test", "Hello")
        assert result.ok is False
        assert "channel" in (result.error or "")

    @pytest.mark.asyncio
    async def test_send_success(self) -> None:
        channel = EmailChannel({"to": "user@example.com"})
        with patch.dict(os.environ, {**_SMTP, "EMAIL_TOOL_ENABLED": "true"}):
            with patch("fim_one.core.channels.email.smtplib.SMTP_SSL") as mock_smtp:
                server = MagicMock()
                mock_smtp.return_value.__enter__ = MagicMock(return_value=server)
                mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
                result = await channel.send_notice("Test", "Hello")
        assert result.ok is True
        assert result.raw["to"] == "user@example.com"
        assert server.sendmail.call_args.args[1] == ["user@example.com"]

    def test_recipients_are_required(self) -> None:
        with patch.dict(os.environ, {**_SMTP, "EMAIL_TOOL_ENABLED": "true"}):
            assert EmailChannel.config_error({}) == "channel_email_recipients_required"
            assert EmailChannel.config_error({"to": ""}) == "channel_email_recipients_required"
            assert EmailChannel.config_error({"to": []}) == "channel_email_recipients_required"
            assert EmailChannel.config_error({"to": "a@x.io, b@y.io"}) is None
            assert EmailChannel.config_error({"to": ["a@x.io", "b@y.io"]}) is None

    @pytest.mark.parametrize(
        "to",
        [
            "not-an-address",
            "<victim@evil.com> a@corp.internal",
            "evil@evil.com@corp.internal",
            "evil@evil.com;a@corp.internal",
            "evil@evil.com a@corp.internal",
            "Ops <a@corp.internal>",
            "a@corp.internal\nBcc: x@evil.com",
            ["a@corp.internal, b@corp.internal"],
            ["a@corp.internal", 7],
            {"a": "a@corp.internal"},
            7,
        ],
    )
    def test_each_recipient_is_exactly_one_address(self, to: Any) -> None:
        """Text whose tail is on the allowlist is not a recipient on it."""
        env = {**_SMTP, "SMTP_ALLOWED_DOMAINS": "corp.internal", "EMAIL_TOOL_ENABLED": ""}
        with patch.dict(os.environ, env):
            assert EmailChannel.config_error({"to": to}) == "channel_email_recipients_invalid"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "to",
        ["<victim@evil.com> a@corp.internal", "evil@evil.com@corp.internal", ["a@corp.internal", 7]],
    )
    async def test_a_stored_row_with_such_text_sends_nothing(self, to: Any) -> None:
        env = {**_SMTP, "SMTP_ALLOWED_DOMAINS": "corp.internal", "EMAIL_TOOL_ENABLED": ""}
        with (
            patch.dict(os.environ, env),
            patch("fim_one.core.channels.email.smtplib.SMTP_SSL") as secure,
            patch("fim_one.core.channels.email.smtplib.SMTP") as plain,
        ):
            result = await EmailChannel({"to": to}).send_notice("t", "b")
            # The admin endpoints name their own recipient; the form still holds.
            operator = instance_channel("email", recipient="Ops <a@corp.internal>")
            assert operator is not None
            operator_result = await operator.send_notice("t", "b")
        secure.assert_not_called()
        plain.assert_not_called()
        for outcome in (result, operator_result):
            assert outcome.ok is False
            assert "Not a valid email address" in (outcome.error or "")

    @pytest.mark.asyncio
    async def test_a_list_of_recipients_is_sent_to_each(self) -> None:
        channel = EmailChannel({"to": ["a@x.io", "b@y.io"]})
        with patch.dict(os.environ, {**_SMTP, "EMAIL_TOOL_ENABLED": "true"}):
            with patch("fim_one.core.channels.email.smtplib.SMTP_SSL") as mock_smtp:
                server = MagicMock()
                mock_smtp.return_value.__enter__ = MagicMock(return_value=server)
                mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
                result = await channel.send_notice("Test", "Hello")
        assert result.ok is True
        assert server.sendmail.call_args.args[1] == ["a@x.io", "b@y.io"]


class TestEmailPolicy:
    """An organization's email channel mails only whom the operator lets agents mail."""

    def _env(self, **extra: str) -> Any:
        env = {
            **_SMTP,
            "SMTP_ALLOWED_DOMAINS": "",
            "SMTP_ALLOWED_ADDRESSES": "",
            "EMAIL_TOOL_ENABLED": "",
            **extra,
        }
        return patch.dict(os.environ, env)

    def test_refused_until_the_operator_opens_email_to_agents(self) -> None:
        with self._env():
            assert EmailChannel.config_error({"to": "a@x.io"}) == "channel_email_not_permitted"
        with self._env(SMTP_HOST=""):
            assert EmailChannel.config_error({"to": "a@x.io"}) == "channel_email_not_permitted"
        with self._env(EMAIL_TOOL_ENABLED="true"):
            assert EmailChannel.config_error({"to": "a@x.io"}) is None

    def test_recipients_must_be_on_the_allowlist(self) -> None:
        with self._env(SMTP_ALLOWED_DOMAINS="corp.internal"):
            assert EmailChannel.config_error({"to": "ops@corp.internal"}) is None
            assert EmailChannel.config_error({"to": "ops@corp.internal, x@outside.io"}) == (
                "channel_email_not_permitted"
            )

    @pytest.mark.asyncio
    async def test_allowlist_is_checked_again_when_sending(self) -> None:
        """A channel saved under a wider allowlist stops when it is narrowed."""
        channel = EmailChannel({"to": "x@outside.io"})
        with self._env(SMTP_ALLOWED_DOMAINS="corp.internal"):
            with (
                patch("fim_one.core.channels.email.smtplib.SMTP_SSL") as secure,
                patch("fim_one.core.channels.email.smtplib.SMTP") as plain,
            ):
                result = await channel.send_notice("t", "b")
        secure.assert_not_called()
        plain.assert_not_called()
        assert result.ok is False
        assert "not in allowlist" in (result.error or "")

    @pytest.mark.asyncio
    async def test_operator_channel_is_not_held_to_the_agent_allowlist(self) -> None:
        """The admin endpoints name their own recipient."""
        with self._env():
            channel = instance_channel("email", recipient="anyone@outside.io")
            assert channel is not None
            with patch("fim_one.core.channels.email.smtplib.SMTP_SSL") as secure:
                secure.return_value.__enter__ = MagicMock(return_value=MagicMock())
                secure.return_value.__exit__ = MagicMock(return_value=False)
                result = await channel.send_notice("t", "b")
        assert result.ok is True


class TestCompletion:
    """A webhook channel renders a finished run as a titled markdown message."""

    @pytest.mark.asyncio
    async def test_completion_summary_is_posted(self) -> None:
        channel = WeComWebhookChannel({"webhook_url": WECOM_URL})
        summary = CompletionSummary(
            agent_name="Summariser",
            duration_seconds=107,
            tools_used=["web_search", "web_search", "python_exec"],
            user_message="Run the pipeline",
            final_answer="Done, 42 items processed.",
            conversation_id="conv-12345678",
            conversation_url="https://fim.example/?c=conv-12345678",
        )
        patcher, client = _http(_response(data={"errcode": 0}))
        with patcher:
            result = await channel.send_completion(summary)
        assert result.ok is True
        content = client.post.await_args.kwargs["json"]["markdown"]["content"]
        assert "Summariser" in content
        assert "1m 47s" in content
        assert "web_search, python_exec" in content
        assert "Run the pipeline" in content
        assert "Done, 42 items processed." in content
        assert "https://fim.example/?c=conv-12345678" in content


class TestNoInbound:
    """Outbound-only channels accept no callback."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "channel",
        [SlackWebhookChannel({"webhook_url": SLACK_URL}), EmailChannel({"to": "a@x.io"})],
    )
    async def test_callback_signature_never_verifies(self, channel: Any) -> None:
        assert await channel.verify_signature(b"{}", {}) is False


# ---------------------------------------------------------------------------
# Instance-wide channels (environment variables)
# ---------------------------------------------------------------------------


class TestInstanceChannels:
    def test_nothing_configured(self) -> None:
        with patch.dict(os.environ, _NO_PROVIDERS, clear=False):
            assert available_instance_providers() == []
            assert [p.name for p in instance_providers()] == ["email", "slack", "lark", "wecom"]
            assert instance_channel("slack") is None

    @pytest.mark.parametrize(
        ("name", "env", "cls"),
        [
            ("slack", {"SLACK_WEBHOOK_URL": SLACK_URL}, SlackWebhookChannel),
            ("lark", {"LARK_WEBHOOK_URL": LARK_URL}, LarkWebhookChannel),
            ("wecom", {"WECOM_WEBHOOK_URL": WECOM_URL}, WeComWebhookChannel),
        ],
    )
    def test_webhook_from_env(self, name: str, env: dict[str, str], cls: type) -> None:
        with patch.dict(os.environ, {**_NO_PROVIDERS, **env}, clear=False):
            assert available_instance_providers() == [name]
            channel = instance_channel(name)
        assert isinstance(channel, cls)
        assert channel.config["webhook_url"] == next(iter(env.values()))

    def test_webhook_url_on_another_host_is_not_configured(self) -> None:
        env = {**_NO_PROVIDERS, "SLACK_WEBHOOK_URL": "https://example.com"}
        with patch.dict(os.environ, env, clear=False):
            assert available_instance_providers() == []
            assert instance_channel("slack") is None

    def test_email_requires_all_three(self) -> None:
        with patch.dict(os.environ, _NO_PROVIDERS, clear=False):
            assert instance_channel("email", recipient="a@x.io") is None
        env = {**_NO_PROVIDERS, "SMTP_HOST": "x", "SMTP_USER": "y", "SMTP_PASS": "z"}
        with patch.dict(os.environ, env, clear=False):
            channel = instance_channel("email", recipient="a@x.io")
            assert available_instance_providers() == ["email"]
        assert isinstance(channel, EmailChannel)
        assert channel.recipients() == "a@x.io"

    def test_all_configured(self) -> None:
        env = {
            "SLACK_WEBHOOK_URL": SLACK_URL,
            "LARK_WEBHOOK_URL": LARK_URL,
            "WECOM_WEBHOOK_URL": WECOM_URL,
            "SMTP_HOST": "smtp.example.com",
            "SMTP_USER": "user@example.com",
            "SMTP_PASS": "secret",
        }
        with patch.dict(os.environ, env, clear=False):
            assert sorted(available_instance_providers()) == ["email", "lark", "slack", "wecom"]

    def test_unknown_provider(self) -> None:
        assert instance_channel("pager") is None

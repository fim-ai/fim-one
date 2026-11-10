"""Tests for the webhook and email channel types and the instance-wide channels.

Covers:
- which URLs each webhook type accepts
- each type's message format and how it reads the platform's answer
- the instance-wide channels built from environment variables
"""

from __future__ import annotations

import os
import re
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
            # userinfo, in every form it can take
            "https://user:pw@hooks.slack.com/services/T/B/x",
            "https://user@hooks.slack.com/services/T/B/x",
            "https://:pw@hooks.slack.com/services/T/B/x",
            "https://@hooks.slack.com/services/T/B/x",
            "https://:@hooks.slack.com/services/T/B/x",
            "https://user:pw@hooks.slack.com:443/services/T/B/x",
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

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("title", "subject"),
        [
            ("Line one\nLine two", "Line one Line two"),
            ("Weekly report\r\nBcc: x@evil.example", "Weekly report Bcc: x@evil.example"),
            ("  tabs\tand \x0b\x0c  spaces\u2028here ", "tabs and spaces here"),
            ("nul\x00byte", "nul byte"),
        ],
    )
    async def test_a_title_with_line_breaks_is_sent_as_a_one_line_subject(
        self, title: str, subject: str
    ) -> None:
        """A line break in the title made the mail library refuse the message."""
        import email as email_lib

        channel = EmailChannel({"to": "user@example.com"})
        with patch.dict(os.environ, {**_SMTP, "EMAIL_TOOL_ENABLED": "true"}):
            with patch("fim_one.core.channels.email.smtplib.SMTP_SSL") as mock_smtp:
                server = MagicMock()
                mock_smtp.return_value.__enter__ = MagicMock(return_value=server)
                mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
                result = await channel.send_notice(title, "Hello")
        assert result.ok is True, result.error
        message = email_lib.message_from_string(server.sendmail.call_args.args[2])
        assert message["Subject"] == subject
        assert message["Bcc"] is None
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


MENTIONS = "<!channel> <!here> <!everyone> <@U012AB3CD> <!subteam^S123> <at id=all></at>"


def _texts(node: Any) -> list[str]:
    """Every string in a JSON-like payload."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [text for value in node.values() for text in _texts(value)]
    if isinstance(node, list):
        return [text for value in node for text in _texts(value)]
    return []


class TestMentionsAreInert:
    """Text a user, a model or a tool wrote cannot notify the members of a chat."""

    def test_slack_markup_is_escaped(self) -> None:
        payload = SlackWebhookChannel({"webhook_url": SLACK_URL}).build_payload(
            "Title", f"Hi {MENTIONS} see <https://evil.example|our wiki> & more"
        )
        text = payload["blocks"][1]["text"]["text"]
        assert "<" not in text and ">" not in text
        assert "&lt;!channel&gt; &lt;!here&gt; &lt;!everyone&gt; &lt;@U012AB3CD&gt;" in text
        assert "&lt;https://evil.example|our wiki&gt; &amp; more" in text

    def test_slack_text_that_is_already_escaped_stays_text(self) -> None:
        escape = SlackWebhookChannel.escape
        assert escape("a &lt;!here&gt; b") == "a &amp;lt;!here&amp;gt; b"

    @pytest.mark.parametrize(
        "tag",
        [
            "<at id=all></at>",
            '<at user_id="all">everyone</at>',
            "<at id=ou_123></at>",
            "<AT ID=all></AT>",
            "< at id=all></at>",
            "<at\tid=all></ at>",
            "<at email=a@b.io></at>",
        ],
    )
    def test_lark_at_tags_are_inert(self, tag: str) -> None:
        payload = LarkWebhookChannel({"webhook_url": LARK_URL}).build_payload("T", f"Hi {tag}!")
        content = payload["card"]["elements"][0]["content"]
        assert not re.search(r"<\s*/?\s*at\b", content, re.IGNORECASE), content
        # Still readable: only an invisible character was added.
        assert content.replace("\u200b", "") == f"Hi {tag}!"

    def test_lark_other_text_is_untouched(self) -> None:
        body = "**Duration**: 2s\n> quote\n1 < 2, <attachment>, a<b, [link](https://x.io)"
        payload = LarkWebhookChannel({"webhook_url": LARK_URL}).build_payload("T", body)
        assert payload["card"]["elements"][0]["content"] == body

    def test_wecom_member_mentions_are_inert(self) -> None:
        payload = WeComWebhookChannel({"webhook_url": WECOM_URL}).build_payload(
            "<@boss> Title", "Hi <@zhangsan> and <@all>"
        )
        content = payload["markdown"]["content"]
        assert "<@" not in content
        assert content.replace("\u200b", "") == "**<@boss> Title**\n\nHi <@zhangsan> and <@all>"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("cls", "url", "response"),
        [
            (SlackWebhookChannel, SLACK_URL, {"status": 200, "text": "ok"}),
            (LarkWebhookChannel, LARK_URL, {"data": {"code": 0}}),
            (WeComWebhookChannel, WECOM_URL, {"data": {"errcode": 0}}),
        ],
    )
    async def test_a_completion_summary_cannot_ping(
        self, cls: type[WebhookChannel], url: str, response: dict[str, Any]
    ) -> None:
        summary = CompletionSummary(
            agent_name="Summariser",
            duration_seconds=2,
            tools_used=[],
            user_message=f"please {MENTIONS} <@zhangsan>",
            final_answer=f"done {MENTIONS} <@zhangsan>",
            conversation_id=None,
            conversation_url=None,
        )
        patcher, client = _http(_response(**response))
        with patcher:
            result = await cls({"webhook_url": url}).send_completion(summary)
        assert result.ok is True
        sent = " ".join(_texts(client.post.await_args.kwargs["json"]))
        for live in ("<!channel>", "<!here>", "<@U012AB3CD>", "<at id=all>", "<@zhangsan>"):
            if cls is LarkWebhookChannel and not live.startswith("<at"):
                continue  # not markup on Lark
            if cls is WeComWebhookChannel and not live.startswith("<@"):
                continue  # not markup on WeCom
            assert live not in sent, (cls.__name__, live)


class TestEmailCompletion:
    """The completion mail is HTML; what the user and the agent wrote is text in it."""

    @pytest.mark.asyncio
    async def test_user_text_is_escaped_and_labels_are_bold(self) -> None:
        summary = CompletionSummary(
            agent_name="Summariser",
            duration_seconds=2,
            tools_used=["web_search"],
            user_message='<img src=x onerror=alert(1)> & <a href="https://evil.example">click</a>',
            final_answer="</b><script>alert(2)</script> **done** in 2 < 3 steps",
            conversation_id="conv-12345678",
            conversation_url="https://fim.example/?c=conv-12345678&x=1",
        )
        channel = EmailChannel({"to": "user@example.com"})
        with patch.dict(os.environ, {**_SMTP, "EMAIL_TOOL_ENABLED": "true"}):
            with patch("fim_one.core.channels.email.smtplib.SMTP_SSL") as mock_smtp:
                server = MagicMock()
                mock_smtp.return_value.__enter__ = MagicMock(return_value=server)
                mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
                result = await channel.send_completion(summary)
        assert result.ok is True

        import email as email_lib

        message = email_lib.message_from_string(server.sendmail.call_args.args[2])
        (part,) = [p for p in message.walk() if p.get_content_type() == "text/html"]
        payload = part.get_payload(decode=True)
        assert isinstance(payload, bytes)
        body = payload.decode("utf-8")

        # Nothing the user or the agent wrote arrives as a tag.
        for tag in ("<img", "<a ", "<script", "</script", "</b><script"):
            assert tag not in body, tag
        assert "&lt;img src=x onerror=alert(1)&gt; &amp; &lt;a href=" in body
        assert "&lt;/b&gt;&lt;script&gt;alert(2)&lt;/script&gt;" in body
        assert "2 &lt; 3 steps" in body
        # The labels, and bold the agent wrote, are rendered; lines are kept.
        assert "<b>Duration</b>: 2.0s<br>" in body
        assert "<b>User message</b><br>" in body
        assert "<b>done</b>" in body
        assert "**" not in body
        assert "https://fim.example/?c=conv-12345678&amp;x=1" in body


class TestEmailNotice:
    """A notice mail is HTML too; its body is text someone else wrote.

    ``send_notice`` carries what a model passed to ``send_notification``,
    the channel test message and the admin notification endpoints.
    """

    @staticmethod
    async def _sent(title: str, body: str) -> Any:
        import email as email_lib

        channel = EmailChannel({"to": "user@example.com"})
        with patch.dict(os.environ, {**_SMTP, "EMAIL_TOOL_ENABLED": "true"}):
            with patch("fim_one.core.channels.email.smtplib.SMTP_SSL") as mock_smtp:
                server = MagicMock()
                mock_smtp.return_value.__enter__ = MagicMock(return_value=server)
                mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
                result = await channel.send_notice(title, body)
        assert result.ok is True
        return email_lib.message_from_string(server.sendmail.call_args.args[2])

    @staticmethod
    def _html(message: Any) -> str:
        (part,) = [p for p in message.walk() if p.get_content_type() == "text/html"]
        payload = part.get_payload(decode=True)
        assert isinstance(payload, bytes)
        return payload.decode("utf-8")

    @pytest.mark.asyncio
    async def test_the_body_is_escaped_and_keeps_its_lines(self) -> None:
        message = await self._sent(
            "Build <b>failed</b>",
            'Step 1 done\nStep 2: <img src=x onerror=alert(1)> & '
            '<a href="https://evil.example">click</a>\n\n**Total**: 2 < 3',
        )
        body = self._html(message)

        for tag in ("<img", "<a ", "</a>"):
            assert tag not in body, tag
        assert "&lt;img src=x onerror=alert(1)&gt; &amp; &lt;a href=" in body
        assert "Step 1 done<br>" in body
        assert "<b>Total</b>: 2 &lt; 3" in body
        # The subject is a header, not HTML: it arrives as written.
        assert message["Subject"] == "Build <b>failed</b>"

    @pytest.mark.asyncio
    async def test_the_send_notification_tool_cannot_put_markup_in_a_mail(self) -> None:
        from fim_one.core.tool.builtin.send_notification import SendNotificationTool, _Target

        channel = EmailChannel({"to": "user@example.com"})
        sent: list[str] = []

        def _record(*, to: str, subject: str, body: str) -> None:
            sent.append(body)

        target = _Target(key="ch-1", name="ops-mail", kind="email", channel=channel)
        tool = SendNotificationTool()
        with (
            patch.dict(os.environ, {**_SMTP, "EMAIL_TOOL_ENABLED": "true"}),
            patch.object(EmailChannel, "_send_sync", staticmethod(_record)),
            patch.object(SendNotificationTool, "_targets", AsyncMock(return_value=[target])),
        ):
            out = await tool.run(
                channel="ops-mail",
                title="Report",
                body='<form action="https://evil.example"><input name=pw></form>',
            )

        assert "sent" in out, out
        (body,) = sent
        assert "<form" not in body and "<input" not in body
        assert "&lt;form action=&quot;https://evil.example&quot;&gt;" in body


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

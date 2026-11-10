"""The ``email_send`` tool: who it may mail, and what counts as a recipient."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from fim_one.core.tool.builtin.email_send import EmailSendTool, parse_recipients

_SMTP = {
    "SMTP_HOST": "smtp.example.com",
    "SMTP_PORT": "465",
    "SMTP_USER": "bot@example.com",
    "SMTP_PASS": "secret",
    "SMTP_SSL": "ssl",
    "SMTP_ALLOWED_DOMAINS": "allowed.com",
    "SMTP_ALLOWED_ADDRESSES": "",
    "EMAIL_TOOL_ENABLED": "",
}

# Text that is not one address but reads, to a check that looks at what
# follows the last "@", as an address on the allowed domain. The SMTP layer
# would deliver each to evil.com.
SMUGGLED = [
    "<victim@evil.com> a@allowed.com",
    "victim@evil.com <a@allowed.com>",
    "evil@evil.com@allowed.com",
    "evil@evil.com;a@allowed.com",
    "evil@evil.com a@allowed.com",
    "evil@evil.com\ta@allowed.com",
    "evil@evil.com\nBcc: x@allowed.com",
    "evil@evil.com\r\nx@allowed.com",
    "evil@evil.com(a@allowed.com",
    "(evil@evil.com)a@allowed.com",
    '"evil@evil.com"@allowed.com',
    "evil@evil.com\x00@allowed.com",
    "<a@allowed.com>",
    "A Name <a@allowed.com>",
    "a@allowed.com>",
    "a@@allowed.com",
    "@allowed.com",
    "a@",
    "a@allowed..com",
    "a@-allowed.com",
    ".a@allowed.com",
    "a..b@allowed.com",
    "ä@allowed.com",
    "a@allowed.com" + "​",
]


class TestParseRecipients:
    @pytest.mark.parametrize(
        ("value", "addresses"),
        [
            ("a@allowed.com", ["a@allowed.com"]),
            (" a@allowed.com , b.c+tag@sub.allowed.com ", ["a@allowed.com", "b.c+tag@sub.allowed.com"]),
            ("a@allowed.com,", ["a@allowed.com"]),
            (["a@allowed.com", "B@Allowed.com"], ["a@allowed.com", "B@Allowed.com"]),
            ("o'brien@corp-1.internal", ["o'brien@corp-1.internal"]),
            ("ops@localhost", ["ops@localhost"]),
            (None, []),
            ("", []),
            ([], []),
        ],
    )
    def test_plain_addresses(self, value: Any, addresses: list[str]) -> None:
        assert parse_recipients(value) == (addresses, [])

    @pytest.mark.parametrize("value", SMUGGLED)
    def test_anything_else_is_malformed(self, value: str) -> None:
        addresses, malformed = parse_recipients(value)
        assert malformed, value
        # ... and inside a list of otherwise good addresses too.
        assert parse_recipients(["a@allowed.com", value])[1]

    @pytest.mark.parametrize(
        "value",
        [
            ["a@allowed.com, b@allowed.com"],  # a list item is one address
            ["a@allowed.com", 7],
            ["a@allowed.com", None],
            [["a@allowed.com"]],
            {"to": "a@allowed.com"},
            7,
            True,
            b"a@allowed.com",
            ",",
            " , ",
            "x" * 250 + "@allowed.com",
        ],
    )
    def test_other_shapes_are_malformed(self, value: Any) -> None:
        assert parse_recipients(value)[1]

    def test_what_is_echoed_is_short_and_escaped(self) -> None:
        _, malformed = parse_recipients("evil@evil.com\r\nBcc: " + "x" * 500 + "@allowed.com")
        assert len(malformed) == 1
        assert len(malformed[0]) <= 80
        assert "\r" not in malformed[0] and "\n" not in malformed[0]


class TestAllowlist:
    def _run(self, **kwargs: Any) -> tuple[str, MagicMock, MagicMock]:
        import asyncio

        server = MagicMock()
        with (
            patch.dict(os.environ, _SMTP),
            patch("fim_one.core.tool.builtin.email_send.smtplib.SMTP_SSL") as secure,
            patch("fim_one.core.tool.builtin.email_send.smtplib.SMTP") as plain,
        ):
            secure.return_value.__enter__ = MagicMock(return_value=server)
            secure.return_value.__exit__ = MagicMock(return_value=False)
            result = asyncio.run(
                EmailSendTool().run(**{"subject": "s", "body": "b", **kwargs})
            )
        plain.assert_not_called()
        return result, secure, server

    def test_an_allowed_address_is_sent(self) -> None:
        result, _, server = self._run(to="a@allowed.com, B@Allowed.com", cc="c@allowed.com")
        assert result == "Email sent to a@allowed.com, B@Allowed.com, cc: c@allowed.com."
        assert server.sendmail.call_args.args[1] == [
            "a@allowed.com",
            "B@Allowed.com",
            "c@allowed.com",
        ]

    def test_another_domain_is_refused(self) -> None:
        result, secure, _ = self._run(to="a@allowed.com, x@evil.com")
        assert result.startswith("[Error] Recipient(s) not in allowlist: x@evil.com")
        secure.assert_not_called()

    @pytest.mark.parametrize("value", SMUGGLED)
    @pytest.mark.parametrize("field", ["to", "cc", "bcc"])
    def test_text_that_is_not_one_address_is_never_sent(self, field: str, value: str) -> None:
        """The allowlist is not passed by text whose tail looks allowed."""
        kwargs = {"to": "a@allowed.com", field: value}
        result, secure, _ = self._run(**kwargs)
        assert result.startswith("[Error] Not a valid email address"), result
        secure.assert_not_called()

    def test_to_is_a_string_or_a_list_of_strings(self) -> None:
        result, _, server = self._run(to=["a@allowed.com", "b@allowed.com"])
        assert result == "Email sent to a@allowed.com, b@allowed.com."
        assert server.sendmail.call_args.args[1] == ["a@allowed.com", "b@allowed.com"]
        for bad in (7, {"a": "a@allowed.com"}, ["a@allowed.com", 7], ["a@allowed.com,b@allowed.com"]):
            result, secure, _ = self._run(to=bad)
            assert result.startswith("[Error] Not a valid email address"), bad
            secure.assert_not_called()

    def test_recipients_are_required(self) -> None:
        empties: list[Any] = ["", "   ", None, []]
        for empty in empties:
            result, secure, _ = self._run(to=empty)
            assert result == "[Error] 'to' is required."
            secure.assert_not_called()

    def test_the_form_is_checked_without_an_allowlist_too(self) -> None:
        import asyncio

        env = {**_SMTP, "SMTP_ALLOWED_DOMAINS": "", "EMAIL_TOOL_ENABLED": "true"}
        with (
            patch.dict(os.environ, env),
            patch("fim_one.core.tool.builtin.email_send.smtplib.SMTP_SSL") as secure,
        ):
            result = asyncio.run(
                EmailSendTool().run(to="a@x.io\nBcc: b@y.io", subject="s", body="b")
            )
        assert result.startswith("[Error] Not a valid email address")
        secure.assert_not_called()


class TestSubject:
    def test_a_subject_with_line_breaks_is_sent_on_one_line(self) -> None:
        """A line break in the subject made the send fail with a header error."""
        import asyncio
        import email as email_lib

        server = MagicMock()
        with (
            patch.dict(os.environ, _SMTP),
            patch("fim_one.core.tool.builtin.email_send.smtplib.SMTP_SSL") as secure,
        ):
            secure.return_value.__enter__ = MagicMock(return_value=server)
            secure.return_value.__exit__ = MagicMock(return_value=False)
            result = asyncio.run(
                EmailSendTool().run(
                    to="a@allowed.com", subject="Weekly\r\nBcc: x@evil.com\n", body="b"
                )
            )
        assert result == "Email sent to a@allowed.com."
        message = email_lib.message_from_string(server.sendmail.call_args.args[2])
        assert message["Subject"] == "Weekly Bcc: x@evil.com"
        assert message["Bcc"] is None
        assert server.sendmail.call_args.args[1] == ["a@allowed.com"]

    def test_a_subject_of_only_line_breaks_is_missing(self) -> None:
        import asyncio

        with patch.dict(os.environ, _SMTP):
            result = asyncio.run(
                EmailSendTool().run(to="a@allowed.com", subject="\r\n \n", body="b")
            )
        assert result == "[Error] 'subject' is required."

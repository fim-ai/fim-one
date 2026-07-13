"""SMTP transport-mode resolution: an unrecognized SMTP_SSL must fail closed.

Covers both SMTP call sites — the system-email path (`web.email`) and the
notification provider (`core.notification.email_provider`) — since both
authenticate with `server.login()` and would leak credentials in cleartext if a
typo in SMTP_SSL silently selected the unencrypted transport.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from fim_one.core.notification.email_provider import EmailNotificationProvider
from fim_one.core.notification.smtp import resolve_ssl_mode
from fim_one.web.email import _send_email


@pytest.fixture(autouse=True)
def smtp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASS", "hunter2")


class TestResolveSslMode:
    def test_defaults_to_ssl_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SMTP_SSL", raising=False)
        assert resolve_ssl_mode() == "ssl"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("ssl", "ssl"),
            ("SSL", "ssl"),
            ("tls", "tls"),
            (" tls ", "tls"),
            ("none", "none"),
            ("", "none"),
        ],
    )
    def test_accepts_known_modes(
        self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: str
    ) -> None:
        monkeypatch.setenv("SMTP_SSL", raw)
        assert resolve_ssl_mode() == expected

    @pytest.mark.parametrize("raw", ["true", "1", "yes", "on", "starttls", "sslv3"])
    def test_rejects_unknown_mode_instead_of_downgrading(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        """A typo must raise, not silently drop to the cleartext-login path."""
        monkeypatch.setenv("SMTP_SSL", raw)
        with pytest.raises(ValueError, match="SMTP_SSL must be one of"):
            resolve_ssl_mode()


class TestSystemEmailTransport:
    def test_typo_never_opens_a_plaintext_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SMTP_SSL", "true")
        with patch("smtplib.SMTP") as plain, patch("smtplib.SMTP_SSL") as secure:
            with pytest.raises(ValueError):
                _send_email("user@example.com", "hi", "<p>hi</p>")
        plain.assert_not_called()
        secure.assert_not_called()

    def test_explicit_none_still_sends_but_warns(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("SMTP_SSL", "none")
        with patch("smtplib.SMTP") as plain:
            plain.return_value.__enter__.return_value = MagicMock()
            with caplog.at_level(logging.WARNING, logger="fim_one.core.notification.smtp"):
                _send_email("user@example.com", "hi", "<p>hi</p>")
        plain.assert_called_once()
        assert "cleartext" in caplog.text

    def test_default_uses_smtp_over_ssl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SMTP_SSL", raising=False)
        with patch("smtplib.SMTP_SSL") as secure, patch("smtplib.SMTP") as plain:
            secure.return_value.__enter__.return_value = MagicMock()
            _send_email("user@example.com", "hi", "<p>hi</p>")
        secure.assert_called_once()
        plain.assert_not_called()


class TestNotificationProviderTransport:
    """The notification provider shares the same login()-over-SMTP hazard."""

    def test_typo_never_opens_a_plaintext_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SMTP_SSL", "true")
        with patch("smtplib.SMTP") as plain, patch("smtplib.SMTP_SSL") as secure:
            with pytest.raises(ValueError):
                EmailNotificationProvider._send_sync(
                    to="user@example.com", subject="hi", body="<p>hi</p>"
                )
        plain.assert_not_called()
        secure.assert_not_called()

    def test_explicit_none_still_sends_but_warns(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("SMTP_SSL", "none")
        with patch("smtplib.SMTP") as plain:
            plain.return_value.__enter__.return_value = MagicMock()
            with caplog.at_level(logging.WARNING, logger="fim_one.core.notification.smtp"):
                EmailNotificationProvider._send_sync(
                    to="user@example.com", subject="hi", body="<p>hi</p>"
                )
        plain.assert_called_once()
        assert "cleartext" in caplog.text

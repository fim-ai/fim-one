"""Refusals of internal targets name the host the user gave and nothing else.

The rule: a message that reaches the user never contains the address a
name resolved to, nor an entry of the operator's allow-list.  The details
go to the server log.
"""

from __future__ import annotations

import logging
import socket
from typing import Any

import pytest

from fim_one.core.security import ssrf
from fim_one.core.security.db_target import DatabaseTargetError, authorize_database_target

_RESOLVED = "10.20.30.40"
_ALLOW_ENTRY = "10.99.0.0/16"
_HOST = "intranet.example"


@pytest.fixture(autouse=True)
def _resolves_internally(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ssrf.FAKE_IP_ENV, raising=False)

    def _getaddrinfo(host: Any, port: Any, *a: Any, **kw: Any) -> Any:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_RESOLVED, port or 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)


def _assert_hidden(message: str) -> None:
    assert _RESOLVED not in message, message
    assert "10.99" not in message, message


def test_validate_url(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING), pytest.raises(ValueError) as exc:
        ssrf.validate_url(f"https://{_HOST}/v1")
    _assert_hidden(str(exc.value))
    assert _HOST in str(exc.value)
    assert _RESOLVED in caplog.text


def test_transport_pinning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING), pytest.raises(ValueError) as exc:
        ssrf._resolve_and_pin(_HOST)
    _assert_hidden(str(exc.value))
    assert _RESOLVED in caplog.text


def test_inline_model_endpoint() -> None:
    from fim_one.web.deps import InlineModelConfigError, check_inline_model_config

    with pytest.raises(InlineModelConfigError) as exc:
        check_inline_model_config(
            {"base_url": f"https://{_HOST}/v1", "api_key": "k"}, resolve=True
        )
    _assert_hidden(str(exc.value))


async def test_document_download() -> None:
    from fim_one.core.tool.base import ToolResult
    from fim_one.core.tool.builtin.markitdown_tool import MarkItDownTool

    result = await MarkItDownTool(user_id="u").run(uri=f"https://{_HOST}/a.pdf")
    text = str(result.content) if isinstance(result, ToolResult) else str(result)
    assert text.startswith("[Error]")
    _assert_hidden(text)


def test_database_target(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    for env in ("DATABASE_URL", "REDIS_URL"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("DB_CONNECTOR_ALLOWED_HOSTS", _ALLOW_ENTRY)
    with caplog.at_level(logging.WARNING), pytest.raises(DatabaseTargetError) as exc:
        authorize_database_target(
            {"host": _HOST, "port": 5432, "database": "d", "username": "u", "password": "p"}
        )
    _assert_hidden(str(exc.value))
    assert _RESOLVED in caplog.text

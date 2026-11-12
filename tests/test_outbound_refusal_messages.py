"""What a refused outbound request tells the caller.

The address check refuses hosts on internal networks. The message returned
names neither the address the host resolved to nor any allow-list entry;
those details are written to the server log.
"""

from __future__ import annotations

import socket
from typing import Any

import httpx
import pytest

from fim_one.core.security import db_target, ssrf
from fim_one.web.deps import InlineModelConfigError, check_inline_model_config

_RESOLVED = "10.23.45.67"
_ALLOWED_ENTRY = "172.31.99.0/24"


@pytest.fixture(autouse=True)
def _internal_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    def _getaddrinfo(host: Any, port: Any, *a: Any, **kw: Any) -> Any:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_RESOLVED, port or 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)
    monkeypatch.delenv("SSRF_ALLOW_FAKE_IP_RANGE", raising=False)
    for env in ("DATABASE_URL", "REDIS_URL"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("DB_CONNECTOR_ALLOWED_HOSTS", _ALLOWED_ENTRY)


def _assert_generic(message: str) -> None:
    assert _RESOLVED not in message
    assert _ALLOWED_ENTRY not in message


def test_url_validation(caplog: pytest.LogCaptureFixture) -> None:
    with pytest.raises(ValueError) as excinfo:
        ssrf.validate_url("https://service.example.test/v1")
    _assert_generic(str(excinfo.value))
    assert _RESOLVED in caplog.text


def test_resolve_and_check() -> None:
    with pytest.raises(ValueError) as excinfo:
        ssrf.resolve_and_check("service.example.test")
    _assert_generic(str(excinfo.value))


async def test_http_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _never(self: Any, request: httpx.Request) -> httpx.Response:
        raise AssertionError("a connection was attempted")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _never)
    async with ssrf.get_safe_async_client() as client:
        with pytest.raises(ValueError) as excinfo:
            await client.get("https://service.example.test/v1")
    _assert_generic(str(excinfo.value))


def test_custom_model_endpoint() -> None:
    config = {"model_name": "m-1", "base_url": "https://service.example.test/v1", "api_key": "k-1"}
    with pytest.raises(InlineModelConfigError) as excinfo:
        check_inline_model_config(config, resolve=True)
    _assert_generic(str(excinfo.value))


def test_database_target() -> None:
    config = {"host": "db.example.test", "port": 5432, "database": "d", "username": "u",
              "password": "p"}
    with pytest.raises(db_target.DatabaseTargetError) as excinfo:
        db_target.authorize_database_target(config)
    _assert_generic(str(excinfo.value))


async def test_agent_model_endpoint_refusal() -> None:
    from fim_one.web.api.agents import _validate_model_endpoint
    from fim_one.web.exceptions import AppError

    config = {"model_name": "m-1", "base_url": "https://service.example.test/v1", "api_key": "k-1"}
    with pytest.raises(AppError) as excinfo:
        await _validate_model_endpoint(config)
    _assert_generic(str(excinfo.value.detail))
    _assert_generic(str(excinfo.value.detail_args))


@pytest.mark.parametrize("case", ["internal", "own-host", "unresolved"])
def test_database_refusals_share_one_message(case: str, monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"host": "db.example.test", "port": 5432, "database": "d", "username": "u",
              "password": "p"}
    if case == "own-host":
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.example.test:5432/app")
    if case == "unresolved":
        def _fail(*a: Any, **kw: Any) -> Any:
            raise socket.gaierror("no such host")

        monkeypatch.setattr(socket, "getaddrinfo", _fail)
    with pytest.raises(db_target.DatabaseTargetError) as excinfo:
        db_target.authorize_database_target(config)
    assert str(excinfo.value) == db_target.REFUSED_HOST_MESSAGE
    _assert_generic(str(excinfo.value))

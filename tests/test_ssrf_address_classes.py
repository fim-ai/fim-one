"""Which addresses count as internal for outbound requests.

Every form that ends at an internal IPv4 or IPv6 host is internal, however
the address is written or wrapped; every caller of the check (HTTP client,
URL validation, database connectors) refuses it.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from fim_one.core.security import ssrf
from fim_one.core.security.db_target import DatabaseTargetError, authorize_database_target
from fim_one.core.security.ssrf import is_private_ip

_INTERNAL_V4 = ["127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254",
                "100.100.100.200", "0.0.0.0", "192.0.0.1"]
_PUBLIC_V4 = ["8.8.8.8", "93.184.216.34", "1.1.1.1"]


@pytest.fixture(autouse=True)
def _default_address_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rules as shipped; a test that needs the opt-in sets it itself."""
    monkeypatch.delenv("SSRF_ALLOW_FAKE_IP_RANGE", raising=False)


def _wrap(v4: str) -> dict[str, str]:
    a, b, c, d = (int(x) for x in v4.split("."))
    hi, lo = f"{a:02x}{b:02x}", f"{c:02x}{d:02x}"
    return {
        "mapped": f"::ffff:{v4}",
        "compatible": f"::{v4}",
        "nat64": f"64:ff9b::{hi}:{lo}",
        "nat64_local": f"64:ff9b:1:{hi}:{c:02x}:{d:02x}00::",
        "6to4": f"2002:{hi}:{lo}::1",
    }


_INTERNAL = (
    _INTERNAL_V4
    + [form for v4 in _INTERNAL_V4 for form in _wrap(v4).values()]
    + ["::", "::1", "fc00::1", "fd12:3456::1", "fe80::1", "fe80::1%eth0", "fec0::1", "ff02::1",
       "2001:0:4136:e378:8000:63bf:3fff:fdd2",  # Teredo
       "::8.8.8.8",                              # IPv4-compatible is refused as a class
       "198.18.0.1", "198.19.255.254", "::ffff:198.18.0.1", "64:ff9b::c612:1",
       "not-an-address", "", "1.2.3", "999.1.1.1"]
)
_PUBLIC = _PUBLIC_V4 + [
    _wrap(v4)[kind] for v4 in _PUBLIC_V4 for kind in ("mapped", "nat64", "nat64_local", "6to4")
] + ["2606:4700:4700::1111", "2a00:1450:4001:81b::200e"]


class TestAddressClasses:
    @pytest.mark.parametrize("address", _INTERNAL)
    def test_internal(self, address: str) -> None:
        assert is_private_ip(address) is True

    @pytest.mark.parametrize("address", _PUBLIC)
    def test_public(self, address: str) -> None:
        assert is_private_ip(address) is False

    def test_benchmark_range_is_internal_unless_the_operator_opts_in(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        forms = ["198.18.0.1", "198.19.1.1", "::ffff:198.18.0.1", "64:ff9b::c612:1", "2002:c612:1::"]
        for value in ("", "false", "0", "no"):
            monkeypatch.setenv("SSRF_ALLOW_FAKE_IP_RANGE", value)
            assert all(is_private_ip(a) for a in forms)
        monkeypatch.setenv("SSRF_ALLOW_FAKE_IP_RANGE", "true")
        assert not any(is_private_ip(a) for a in forms)
        # The opt-in opens that range and nothing else.
        assert is_private_ip("198.17.255.255") is False  # public either way
        assert all(is_private_ip(a) for a in _INTERNAL_V4)


_LITERALS = [a for a in _INTERNAL if ":" in a and "%" not in a] + ["198.18.0.1", "127.0.0.1"]


class TestEveryCallerRefuses:
    @pytest.mark.parametrize("address", _LITERALS)
    def test_url_validation(self, address: str) -> None:
        host = f"[{address}]" if ":" in address else address
        with pytest.raises(ValueError):
            ssrf.validate_url(f"http://{host}:8080/")

    @pytest.mark.parametrize("address", _LITERALS)
    async def test_http_transport(self, address: str, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _never(self: Any, request: httpx.Request) -> httpx.Response:
            raise AssertionError("a connection was attempted")

        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _never)
        host = f"[{address}]" if ":" in address else address
        async with ssrf.get_safe_async_client() as client:
            with pytest.raises(ValueError):
                await client.get(f"http://{host}:8080/")

    @pytest.mark.parametrize("address", _LITERALS)
    def test_database_connector(self, address: str, monkeypatch: pytest.MonkeyPatch) -> None:
        for env in ("DB_CONNECTOR_ALLOWED_HOSTS", "DATABASE_URL", "REDIS_URL"):
            monkeypatch.delenv(env, raising=False)
        with pytest.raises(DatabaseTargetError):
            authorize_database_target(
                {"host": address, "port": 5432, "database": "d", "username": "u", "password": "p"}
            )

    async def test_a_name_resolving_to_a_wrapped_address_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import socket

        def _getaddrinfo(host: Any, port: Any, *a: Any, **kw: Any) -> Any:
            return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("64:ff9b::7f00:1", port or 0, 0, 0))]

        monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)
        with pytest.raises(ValueError):
            ssrf.validate_url("http://wrapped.example/")
        with pytest.raises(ValueError):
            ssrf._resolve_and_pin("wrapped.example")
        with pytest.raises(DatabaseTargetError):
            authorize_database_target(
                {"host": "wrapped.example", "port": 5432, "database": "d", "username": "u", "password": "p"}
            )

"""The test session cannot reach a host other than this machine."""

from __future__ import annotations

import socket
import urllib.request

import httpx
import pytest

from fim_one.core.channels.feishu import FeishuChannel

from .conftest import RealNetworkBlocked


class TestRealNetworkIsBlocked:
    def test_a_public_address_does_not_connect(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            with pytest.raises(RealNetworkBlocked):
                sock.connect(("203.0.113.10", 443))
            with pytest.raises(RealNetworkBlocked):
                sock.connect_ex(("203.0.113.10", 443))

    def test_loopback_stays_open(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            with socket.create_connection(server.getsockname(), timeout=5) as client:
                assert client.getpeername() == server.getsockname()

    def test_the_system_proxy_is_not_used(self) -> None:
        assert not {"http", "https", "all"} & set(urllib.request.getproxies())

    @pytest.mark.asyncio
    async def test_an_unmocked_http_request_fails_instead_of_leaving(self) -> None:
        async with httpx.AsyncClient(timeout=5) as client:
            with pytest.raises(httpx.ConnectError) as caught:
                await client.get("https://203.0.113.10/")
        # Refused by the guard itself, not answered or refused by a proxy.
        assert _caused_by_the_guard(caught.value)

    @pytest.mark.asyncio
    async def test_an_unmocked_feishu_channel_sends_nothing(self) -> None:
        channel = FeishuChannel(
            {"app_id": "cli_x", "app_secret": "s", "chat_id": "oc_x", "encrypt_key": "k"}
        )
        result = await channel.send_notice("title", "body")
        assert result.ok is False
        assert (result.error or "").startswith("ConnectError")


def _caused_by_the_guard(exc: BaseException | None) -> bool:
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, RealNetworkBlocked):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False

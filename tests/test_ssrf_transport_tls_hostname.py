"""The pinned-address transport and the name the TLS handshake is made for.

A request to a host name is sent to the address the name resolved to, with
the name kept for the handshake. The name has to be handed on as text: the
TLS layer encodes it itself, and given bytes every HTTPS request to a host
name failed with ``'bytes' object has no attribute 'encode'``.
"""

from __future__ import annotations

import httpx
import pytest

from fim_one.core.security import ssrf


async def _sent(monkeypatch: pytest.MonkeyPatch, url: str) -> httpx.Request:
    seen: list[httpx.Request] = []

    async def record(self: object, request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", record)
    monkeypatch.setattr(ssrf, "_resolve_and_pin", lambda host: "93.184.216.34")
    async with httpx.AsyncClient(transport=ssrf.SSRFSafeTransport()) as client:
        await client.get(url)
    return seen[0]


async def test_the_handshake_name_is_text_and_the_request_goes_to_the_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = await _sent(monkeypatch, "https://api.example.com/v1/things")
    assert request.extensions["sni_hostname"] == "api.example.com"
    assert isinstance(request.extensions["sni_hostname"], str)
    assert request.url.host == "93.184.216.34"
    assert request.headers["host"] == "api.example.com"


async def test_the_tls_layer_accepts_the_name_it_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    # What the handshake does with the name first, in the backend httpx uses.
    from anyio._core._sockets import idna2008_resolve

    request = await _sent(monkeypatch, "https://api.example.com/")
    assert idna2008_resolve(request.extensions["sni_hostname"]) == b"api.example.com"

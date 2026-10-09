"""Security tests for SSRF transport-level DNS pinning."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fim_one.core.security.ssrf import (
    SSRFSafeTransport,
    _is_ip_literal,
    _resolve_and_pin,
    get_safe_async_client,
)


class TestIsIpLiteral:
    def test_ipv4(self):
        assert _is_ip_literal("1.2.3.4") is True

    def test_ipv6(self):
        assert _is_ip_literal("::1") is True

    def test_hostname(self):
        assert _is_ip_literal("example.com") is False

    def test_empty(self):
        assert _is_ip_literal("") is False


class TestResolveAndPin:
    def test_public_ip_passes(self):
        """Resolving a known public hostname should return an IP."""
        # Use a well-known domain -- we mock to avoid network dependency
        with patch("fim_one.core.security.ssrf.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [
                (2, 1, 6, "", ("93.184.216.34", 0)),
            ]
            ip = _resolve_and_pin("example.com")
            assert ip == "93.184.216.34"

    def test_private_ip_blocked(self):
        with patch("fim_one.core.security.ssrf.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [
                (2, 1, 6, "", ("192.168.1.1", 0)),
            ]
            with pytest.raises(ValueError, match="private"):
                _resolve_and_pin("evil.com")

    def test_localhost_blocked(self):
        with patch("fim_one.core.security.ssrf.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [
                (2, 1, 6, "", ("127.0.0.1", 0)),
            ]
            with pytest.raises(ValueError, match="private"):
                _resolve_and_pin("evil.com")

    def test_dns_failure_raises(self):
        import socket

        with patch("fim_one.core.security.ssrf.socket.getaddrinfo") as mock_gai:
            mock_gai.side_effect = socket.gaierror("Name resolution failed")
            with pytest.raises(ValueError, match="DNS resolution failed"):
                _resolve_and_pin("nonexistent.example.com")

    def test_mixed_ips_blocked_if_any_private(self):
        """If any resolved IP is private, the entire resolution should fail."""
        with patch("fim_one.core.security.ssrf.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [
                (2, 1, 6, "", ("93.184.216.34", 0)),
                (2, 1, 6, "", ("10.0.0.1", 0)),  # Private!
            ]
            with pytest.raises(ValueError, match="private"):
                _resolve_and_pin("sneaky.com")


class TestGetSafeAsyncClient:
    def test_returns_async_client(self):
        client = get_safe_async_client(timeout=30)
        import httpx

        assert isinstance(client, httpx.AsyncClient)

    def test_uses_ssrf_transport(self):
        client = get_safe_async_client()
        assert isinstance(client._transport, SSRFSafeTransport)


class TestTransportBlocksPrivateTargets:
    """The transport itself refuses private targets, with or without DNS."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",
            "http://127.0.0.1:8000/",
            "http://10.0.0.5/",
            "http://[::1]/",
            "http://[::ffff:127.0.0.1]/",
        ],
    )
    async def test_ip_literal_private_blocked(self, url: str) -> None:
        import httpx

        transport = SSRFSafeTransport()
        with patch.object(
            httpx.AsyncHTTPTransport, "handle_async_request"
        ) as mock_send:
            with pytest.raises(ValueError, match="SSRF blocked"):
                await transport.handle_async_request(httpx.Request("GET", url))
        mock_send.assert_not_called()

    async def test_ip_literal_public_allowed(self) -> None:
        import httpx

        transport = SSRFSafeTransport()
        with patch.object(
            httpx.AsyncHTTPTransport,
            "handle_async_request",
            return_value=httpx.Response(200),
        ) as mock_send:
            resp = await transport.handle_async_request(
                httpx.Request("GET", "http://93.184.216.34/")
            )
        assert resp.status_code == 200
        mock_send.assert_called_once()

    async def test_redirect_to_metadata_ip_blocked(self) -> None:
        """A public host answering 302 -> metadata IP must not be followed."""
        import httpx

        sent: list[str] = []

        async def fake_send(
            self: httpx.AsyncHTTPTransport, request: httpx.Request
        ) -> httpx.Response:
            sent.append(str(request.url))
            return httpx.Response(
                302, headers={"Location": "http://169.254.169.254/latest/"}
            )

        with (
            patch("fim_one.core.security.ssrf.socket.getaddrinfo") as mock_gai,
            patch.object(httpx.AsyncHTTPTransport, "handle_async_request", fake_send),
        ):
            mock_gai.return_value = [(2, 1, 6, "", ("93.184.216.34", 0))]
            async with get_safe_async_client(follow_redirects=True) as client:
                with pytest.raises(ValueError, match="SSRF blocked"):
                    await client.get("http://example.com/")
        assert sent == ["http://93.184.216.34/"]

    async def test_dns_rebinding_after_validation_blocked(self) -> None:
        """Host resolved public at set time, private at send time -> refused."""
        import httpx

        with (
            patch("fim_one.core.security.ssrf.socket.getaddrinfo") as mock_gai,
            patch.object(
                httpx.AsyncHTTPTransport, "handle_async_request"
            ) as mock_send,
        ):
            mock_gai.return_value = [(2, 1, 6, "", ("169.254.169.254", 0))]
            async with get_safe_async_client() as client:
                with pytest.raises(ValueError, match="private"):
                    await client.post("http://rebind.example/")
        mock_send.assert_not_called()


class TestBlocklistRanges:
    @pytest.mark.parametrize(
        "ip",
        [
            "100.100.100.200",  # Alibaba Cloud metadata
            "100.64.0.1",
            "224.0.0.1",
            "255.255.255.255",
            "::",
            "ff02::1",
        ],
    )
    def test_blocked(self, ip: str) -> None:
        from fim_one.core.security.ssrf import is_private_ip

        assert is_private_ip(ip) is True

    @pytest.mark.parametrize("ip", ["93.184.216.34", "100.63.255.255", "198.18.0.1", "2606:4700::1111"])
    def test_public_allowed(self, ip: str) -> None:
        from fim_one.core.security.ssrf import is_private_ip

        assert is_private_ip(ip) is False


class TestPinPrefersIPv4:
    def test_ipv4_chosen_when_ipv6_listed_first(self) -> None:
        import socket

        with patch("fim_one.core.security.ssrf.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [
                (socket.AF_INET6, 1, 6, "", ("2606:2800:220:1::1", 0, 0, 0)),
                (socket.AF_INET, 1, 6, "", ("93.184.216.34", 0)),
            ]
            assert _resolve_and_pin("example.com") == "93.184.216.34"

    def test_ipv6_only_host_still_resolves(self) -> None:
        import socket

        with patch("fim_one.core.security.ssrf.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [
                (socket.AF_INET6, 1, 6, "", ("2606:2800:220:1::1", 0, 0, 0)),
            ]
            assert _resolve_and_pin("example.com") == "2606:2800:220:1::1"

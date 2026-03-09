"""Security tests for SSRF transport-level DNS pinning."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fim_agent.core.security.ssrf import (
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
        with patch("fim_agent.core.security.ssrf.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [
                (2, 1, 6, "", ("93.184.216.34", 0)),
            ]
            ip = _resolve_and_pin("example.com")
            assert ip == "93.184.216.34"

    def test_private_ip_blocked(self):
        with patch("fim_agent.core.security.ssrf.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [
                (2, 1, 6, "", ("192.168.1.1", 0)),
            ]
            with pytest.raises(ValueError, match="private"):
                _resolve_and_pin("evil.com")

    def test_localhost_blocked(self):
        with patch("fim_agent.core.security.ssrf.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [
                (2, 1, 6, "", ("127.0.0.1", 0)),
            ]
            with pytest.raises(ValueError, match="private"):
                _resolve_and_pin("evil.com")

    def test_dns_failure_raises(self):
        import socket
        with patch("fim_agent.core.security.ssrf.socket.getaddrinfo") as mock_gai:
            mock_gai.side_effect = socket.gaierror("Name resolution failed")
            with pytest.raises(ValueError, match="DNS resolution failed"):
                _resolve_and_pin("nonexistent.example.com")

    def test_mixed_ips_blocked_if_any_private(self):
        """If any resolved IP is private, the entire resolution should fail."""
        with patch("fim_agent.core.security.ssrf.socket.getaddrinfo") as mock_gai:
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

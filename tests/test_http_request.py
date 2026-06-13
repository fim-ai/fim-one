"""Tests for the built-in ``HttpRequestTool``."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from fim_one.core.tool.builtin.http_request import (
    HttpRequestTool,
    _looks_like_json,
)
from fim_one.core.security.ssrf import is_private_ip, resolve_and_check


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture()
def tool() -> HttpRequestTool:
    return HttpRequestTool()


# ======================================================================
# Unit tests — helper functions
# ======================================================================


class TestIsPrivateIp:
    """Tests for the ``is_private_ip`` helper."""

    def test_localhost_v4(self) -> None:
        assert is_private_ip("127.0.0.1") is True

    def test_localhost_v6(self) -> None:
        assert is_private_ip("::1") is True

    def test_private_10(self) -> None:
        assert is_private_ip("10.0.0.1") is True

    def test_private_172(self) -> None:
        assert is_private_ip("172.16.0.1") is True

    def test_private_192(self) -> None:
        assert is_private_ip("192.168.1.1") is True

    def test_link_local(self) -> None:
        assert is_private_ip("169.254.1.1") is True

    def test_public_ip(self) -> None:
        assert is_private_ip("8.8.8.8") is False

    def test_public_ip_v6(self) -> None:
        assert is_private_ip("2607:f8b0:4004:800::200e") is False

    def test_unparseable(self) -> None:
        assert is_private_ip("not-an-ip") is True

    def test_zero_network(self) -> None:
        assert is_private_ip("0.0.0.1") is True

    def test_fc00_v6(self) -> None:
        assert is_private_ip("fc00::1") is True

    def test_fe80_v6(self) -> None:
        assert is_private_ip("fe80::1") is True

    # IPv4-mapped IPv6 (::ffff:0:0/96) — Python parses these as IPv6Address,
    # so without an explicit ipv4_mapped check they bypass the IPv4 blocklist
    # and reach internal services on dual-stack hosts (regression: PR #15).
    def test_ipv4_mapped_loopback(self) -> None:
        assert is_private_ip("::ffff:127.0.0.1") is True

    def test_ipv4_mapped_imds(self) -> None:
        assert is_private_ip("::ffff:169.254.169.254") is True

    def test_ipv4_mapped_private_10(self) -> None:
        assert is_private_ip("::ffff:10.0.0.1") is True

    def test_ipv4_mapped_public_stays_allowed(self) -> None:
        assert is_private_ip("::ffff:8.8.8.8") is False


class TestLooksLikeJson:
    """Tests for the ``_looks_like_json`` heuristic."""

    def test_json_object(self) -> None:
        assert _looks_like_json('{"key": "value"}') is True

    def test_json_array(self) -> None:
        assert _looks_like_json("[1, 2, 3]") is True

    def test_plain_text(self) -> None:
        assert _looks_like_json("hello world") is False

    def test_whitespace_padded_json(self) -> None:
        assert _looks_like_json('  {"key": "value"}  ') is True

    def test_empty_string(self) -> None:
        assert _looks_like_json("") is False


class TestResolveAndCheck:
    """Tests for the ``resolve_and_check`` SSRF guard."""

    def test_blocks_private_resolved_ip(self) -> None:
        with patch("socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [
                (2, 1, 6, "", ("127.0.0.1", 0)),
            ]
            with pytest.raises(ValueError, match="SSRF blocked"):
                resolve_and_check("evil.example.com")

    def test_allows_public_ip(self) -> None:
        with patch("socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [
                (2, 1, 6, "", ("93.184.216.34", 0)),
            ]
            resolve_and_check("example.com")  # Should not raise

    def test_dns_failure_raises(self) -> None:
        import socket as _socket

        with patch("socket.getaddrinfo", side_effect=_socket.gaierror("nope")):
            with pytest.raises(ValueError, match="DNS resolution failed"):
                resolve_and_check("nonexistent.invalid")

    def test_no_results_raises(self) -> None:
        with patch("socket.getaddrinfo", return_value=[]):
            with pytest.raises(ValueError, match="no results"):
                resolve_and_check("empty.example.com")


# ======================================================================
# Tool protocol compliance
# ======================================================================


class TestHttpRequestToolProperties:
    """Verify tool protocol properties."""

    def test_name(self, tool: HttpRequestTool) -> None:
        assert tool.name == "http_request"

    def test_category(self, tool: HttpRequestTool) -> None:
        assert tool.category == "web"

    def test_description(self, tool: HttpRequestTool) -> None:
        assert "HTTP" in tool.description
        assert "REST API" in tool.description

    def test_parameters_schema(self, tool: HttpRequestTool) -> None:
        schema = tool.parameters_schema
        assert schema["type"] == "object"
        assert "url" in schema["properties"]
        assert "method" in schema["properties"]
        assert "headers" in schema["properties"]
        assert "params" in schema["properties"]
        assert "body" in schema["properties"]
        assert "timeout" in schema["properties"]
        assert schema["required"] == ["url"]


# ======================================================================
# Input validation
# ======================================================================


class TestInputValidation:
    """Tests for input validation before the request is sent."""

    async def test_empty_url(self, tool: HttpRequestTool) -> None:
        result = await tool.run(url="")
        assert "[Error]" in result
        assert "Invalid URL" in result

    async def test_no_url(self, tool: HttpRequestTool) -> None:
        result = await tool.run()
        assert "[Error]" in result

    async def test_file_scheme_blocked(self, tool: HttpRequestTool) -> None:
        result = await tool.run(url="file:///etc/passwd")
        assert "[Error]" in result
        assert "Unsupported scheme" in result

    async def test_ftp_scheme_blocked(self, tool: HttpRequestTool) -> None:
        result = await tool.run(url="ftp://ftp.example.com/file")
        assert "[Error]" in result
        assert "Unsupported scheme" in result

    async def test_no_hostname(self, tool: HttpRequestTool) -> None:
        result = await tool.run(url="http://")
        assert "[Error]" in result

    async def test_unsupported_method(self, tool: HttpRequestTool) -> None:
        with patch("fim_one.core.tool.builtin.http_request.resolve_and_check"):
            result = await tool.run(url="https://example.com", method="TRACE")
            assert "[Error]" in result
            assert "Unsupported HTTP method" in result


# ======================================================================
# SSRF protection
# ======================================================================


class TestSSRFProtection:
    """Tests for SSRF prevention."""

    async def test_blocks_localhost(self, tool: HttpRequestTool) -> None:
        with patch("socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(2, 1, 6, "", ("127.0.0.1", 0))]
            result = await tool.run(url="http://localhost/admin")
            assert "[Error]" in result
            assert "SSRF blocked" in result

    async def test_blocks_private_10(self, tool: HttpRequestTool) -> None:
        with patch("socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(2, 1, 6, "", ("10.0.0.1", 0))]
            result = await tool.run(url="http://internal.corp/api")
            assert "SSRF blocked" in result

    async def test_blocks_private_192(self, tool: HttpRequestTool) -> None:
        with patch("socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(2, 1, 6, "", ("192.168.1.1", 0))]
            result = await tool.run(url="http://router.local/config")
            assert "SSRF blocked" in result

    async def test_blocks_link_local(self, tool: HttpRequestTool) -> None:
        with patch("socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(2, 1, 6, "", ("169.254.169.254", 0))]
            result = await tool.run(url="http://metadata.google.internal/")
            assert "SSRF blocked" in result


# ======================================================================
# Successful requests (mocked)
# ======================================================================


def _make_mock_response(
    *,
    status_code: int = 200,
    reason_phrase: str = "OK",
    headers: dict[str, str] | None = None,
    content: bytes = b"",
    encoding: str = "utf-8",
) -> httpx.Response:
    """Build a fake ``httpx.Response`` for testing."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.reason_phrase = reason_phrase
    resp.content = content
    resp.encoding = encoding

    resp_headers = httpx.Headers(headers or {})
    resp.headers = resp_headers
    return resp


class TestSuccessfulRequests:
    """Tests for successful HTTP request handling (with mocked transport)."""

    async def test_simple_get(self, tool: HttpRequestTool) -> None:
        mock_resp = _make_mock_response(
            content=b"Hello, World!",
            headers={"content-type": "text/plain"},
        )
        with patch("fim_one.core.tool.builtin.http_request.resolve_and_check"):
            with patch(
                "httpx.AsyncClient.request",
                new_callable=AsyncMock,
                return_value=mock_resp,
            ):
                result = await tool.run(url="https://example.com")

        assert "HTTP 200 OK" in result
        assert "Hello, World!" in result

    async def test_json_pretty_print(self, tool: HttpRequestTool) -> None:
        body = json.dumps({"name": "test", "value": 42})
        mock_resp = _make_mock_response(
            content=body.encode(),
            headers={"content-type": "application/json"},
        )
        with patch("fim_one.core.tool.builtin.http_request.resolve_and_check"):
            with patch(
                "httpx.AsyncClient.request",
                new_callable=AsyncMock,
                return_value=mock_resp,
            ):
                result = await tool.run(url="https://api.example.com/data")

        assert "HTTP 200 OK" in result
        # Pretty-printed JSON should have indentation
        assert '"name": "test"' in result
        assert '"value": 42' in result

    async def test_post_with_body(self, tool: HttpRequestTool) -> None:
        mock_resp = _make_mock_response(
            status_code=201,
            reason_phrase="Created",
            content=b'{"id": 1}',
            headers={"content-type": "application/json"},
        )
        with patch("fim_one.core.tool.builtin.http_request.resolve_and_check"):
            with patch(
                "httpx.AsyncClient.request",
                new_callable=AsyncMock,
                return_value=mock_resp,
            ) as mock_req:
                result = await tool.run(
                    url="https://api.example.com/items",
                    method="POST",
                    body='{"name": "new item"}',
                )

        assert "HTTP 201 Created" in result
        # Verify Content-Type was auto-detected as JSON
        call_kwargs = mock_req.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert headers.get("Content-Type") == "application/json"

    async def test_custom_headers(self, tool: HttpRequestTool) -> None:
        mock_resp = _make_mock_response(content=b"ok")
        with patch("fim_one.core.tool.builtin.http_request.resolve_and_check"):
            with patch(
                "httpx.AsyncClient.request",
                new_callable=AsyncMock,
                return_value=mock_resp,
            ) as mock_req:
                result = await tool.run(
                    url="https://api.example.com",
                    headers={"Authorization": "Bearer token123"},
                )

        call_kwargs = mock_req.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert headers["Authorization"] == "Bearer token123"

    async def test_query_params(self, tool: HttpRequestTool) -> None:
        mock_resp = _make_mock_response(content=b"ok")
        with patch("fim_one.core.tool.builtin.http_request.resolve_and_check"):
            with patch(
                "httpx.AsyncClient.request",
                new_callable=AsyncMock,
                return_value=mock_resp,
            ) as mock_req:
                result = await tool.run(
                    url="https://api.example.com/search",
                    params={"q": "test", "page": "1"},
                )

        call_kwargs = mock_req.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params == {"q": "test", "page": "1"}

    async def test_delete_method(self, tool: HttpRequestTool) -> None:
        mock_resp = _make_mock_response(
            status_code=204,
            reason_phrase="No Content",
            content=b"",
        )
        with patch("fim_one.core.tool.builtin.http_request.resolve_and_check"):
            with patch(
                "httpx.AsyncClient.request",
                new_callable=AsyncMock,
                return_value=mock_resp,
            ) as mock_req:
                result = await tool.run(
                    url="https://api.example.com/items/1",
                    method="DELETE",
                )

        assert "HTTP 204" in result
        call_kwargs = mock_req.call_args
        method = (
            call_kwargs.kwargs.get("method")
            or call_kwargs[1].get("method")
            or call_kwargs[0][0]
        )
        assert method == "DELETE"

    async def test_default_method_is_get(self, tool: HttpRequestTool) -> None:
        mock_resp = _make_mock_response(content=b"ok")
        with patch("fim_one.core.tool.builtin.http_request.resolve_and_check"):
            with patch(
                "httpx.AsyncClient.request",
                new_callable=AsyncMock,
                return_value=mock_resp,
            ) as mock_req:
                await tool.run(url="https://example.com")

        call_kwargs = mock_req.call_args
        method = (
            call_kwargs.kwargs.get("method")
            or call_kwargs[1].get("method")
            or call_kwargs[0][0]
        )
        assert method == "GET"


# ======================================================================
# Response truncation
# ======================================================================


class TestResponseTruncation:
    """Tests for response body size limits."""

    async def test_large_response_truncated(self, tool: HttpRequestTool) -> None:
        large_body = b"x" * (200 * 1024 + 1000)
        mock_resp = _make_mock_response(
            content=large_body,
            headers={"content-type": "text/plain"},
        )
        with patch("fim_one.core.tool.builtin.http_request.resolve_and_check"):
            with patch(
                "httpx.AsyncClient.request",
                new_callable=AsyncMock,
                return_value=mock_resp,
            ):
                result = await tool.run(url="https://example.com/big")

        assert "[Truncated" in result
        assert "chars total" in result


# ======================================================================
# Error handling
# ======================================================================


class TestErrorHandling:
    """Tests for various error scenarios."""

    async def test_timeout(self, tool: HttpRequestTool) -> None:
        with patch("fim_one.core.tool.builtin.http_request.resolve_and_check"):
            with patch(
                "httpx.AsyncClient.request",
                new_callable=AsyncMock,
                side_effect=httpx.TimeoutException("timed out"),
            ):
                result = await tool.run(url="https://slow.example.com")

        assert "[Timeout]" in result
        assert "30s" in result

    async def test_custom_timeout(self, tool: HttpRequestTool) -> None:
        with patch("fim_one.core.tool.builtin.http_request.resolve_and_check"):
            with patch(
                "httpx.AsyncClient.request",
                new_callable=AsyncMock,
                side_effect=httpx.TimeoutException("timed out"),
            ):
                result = await tool.run(url="https://slow.example.com", timeout=60)

        assert "[Timeout]" in result
        assert "60s" in result

    async def test_timeout_capped_at_max(self, tool: HttpRequestTool) -> None:
        """Timeout values above 120s should be capped."""
        with patch("fim_one.core.tool.builtin.http_request.resolve_and_check"):
            with patch(
                "httpx.AsyncClient.request",
                new_callable=AsyncMock,
                side_effect=httpx.TimeoutException("timed out"),
            ):
                result = await tool.run(url="https://slow.example.com", timeout=999)

        assert "[Timeout]" in result
        assert "120s" in result

    async def test_too_many_redirects(self, tool: HttpRequestTool) -> None:
        with patch("fim_one.core.tool.builtin.http_request.resolve_and_check"):
            with patch(
                "httpx.AsyncClient.request",
                new_callable=AsyncMock,
                side_effect=httpx.TooManyRedirects("too many redirects"),
            ):
                result = await tool.run(url="https://loop.example.com")

        assert "[Error]" in result
        assert "redirects" in result.lower()

    async def test_connection_error(self, tool: HttpRequestTool) -> None:
        with patch("fim_one.core.tool.builtin.http_request.resolve_and_check"):
            with patch(
                "httpx.AsyncClient.request",
                new_callable=AsyncMock,
                side_effect=httpx.ConnectError("connection refused"),
            ):
                result = await tool.run(url="https://down.example.com")

        assert "[Error]" in result

    async def test_dns_resolution_failure(self, tool: HttpRequestTool) -> None:
        import socket as _socket

        with patch(
            "socket.getaddrinfo",
            side_effect=_socket.gaierror("Name or service not known"),
        ):
            result = await tool.run(url="https://nonexistent.invalid")

        assert "[Error]" in result
        assert "DNS resolution failed" in result


# ======================================================================
# Content-Type auto-detection
# ======================================================================


class TestContentTypeAutoDetection:
    """Tests for auto-detecting JSON body content type."""

    async def test_json_body_gets_content_type(self, tool: HttpRequestTool) -> None:
        mock_resp = _make_mock_response(content=b"ok")
        with patch("fim_one.core.tool.builtin.http_request.resolve_and_check"):
            with patch(
                "httpx.AsyncClient.request",
                new_callable=AsyncMock,
                return_value=mock_resp,
            ) as mock_req:
                await tool.run(
                    url="https://api.example.com",
                    method="POST",
                    body='{"key": "value"}',
                )

        call_kwargs = mock_req.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert headers.get("Content-Type") == "application/json"

    async def test_non_json_body_no_auto_content_type(
        self, tool: HttpRequestTool
    ) -> None:
        mock_resp = _make_mock_response(content=b"ok")
        with patch("fim_one.core.tool.builtin.http_request.resolve_and_check"):
            with patch(
                "httpx.AsyncClient.request",
                new_callable=AsyncMock,
                return_value=mock_resp,
            ) as mock_req:
                await tool.run(
                    url="https://api.example.com",
                    method="POST",
                    body="plain text body",
                )

        call_kwargs = mock_req.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert "Content-Type" not in headers

    async def test_explicit_content_type_not_overridden(
        self, tool: HttpRequestTool
    ) -> None:
        mock_resp = _make_mock_response(content=b"ok")
        with patch("fim_one.core.tool.builtin.http_request.resolve_and_check"):
            with patch(
                "httpx.AsyncClient.request",
                new_callable=AsyncMock,
                return_value=mock_resp,
            ) as mock_req:
                await tool.run(
                    url="https://api.example.com",
                    method="POST",
                    body='{"key": "value"}',
                    headers={"Content-Type": "text/plain"},
                )

        call_kwargs = mock_req.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert headers["Content-Type"] == "text/plain"


# ======================================================================
# Response formatting
# ======================================================================


class TestResponseFormatting:
    """Tests for the response output format."""

    async def test_includes_selected_headers(self, tool: HttpRequestTool) -> None:
        mock_resp = _make_mock_response(
            content=b"ok",
            headers={
                "content-type": "text/plain",
                "date": "Thu, 01 Jan 2026 00:00:00 GMT",
                "x-custom-header": "should-be-excluded",
            },
        )
        with patch("fim_one.core.tool.builtin.http_request.resolve_and_check"):
            with patch(
                "httpx.AsyncClient.request",
                new_callable=AsyncMock,
                return_value=mock_resp,
            ):
                result = await tool.run(url="https://example.com")

        assert "content-type: text/plain" in result
        assert "date:" in result
        # Custom headers not in the interesting set should be excluded
        assert "x-custom-header" not in result

    async def test_body_section_present(self, tool: HttpRequestTool) -> None:
        mock_resp = _make_mock_response(content=b"response content")
        with patch("fim_one.core.tool.builtin.http_request.resolve_and_check"):
            with patch(
                "httpx.AsyncClient.request",
                new_callable=AsyncMock,
                return_value=mock_resp,
            ):
                result = await tool.run(url="https://example.com")

        assert "Body:" in result
        assert "response content" in result

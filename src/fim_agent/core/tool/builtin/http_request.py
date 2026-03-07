"""Built-in tool for sending arbitrary HTTP requests to REST APIs."""

from __future__ import annotations

import ipaddress
import json
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from ..base import BaseTool

_DEFAULT_TIMEOUT_SECONDS: int = 30
_MAX_TIMEOUT_SECONDS: int = 120
_MAX_RESPONSE_BYTES: int = 200 * 1024  # 200 KB
_MAX_REDIRECTS: int = 5
_USER_AGENT = "FIM-Agent/1.0 (http_request tool)"

# ------------------------------------------------------------------
# SSRF protection — blocked IP ranges
# ------------------------------------------------------------------

_BLOCKED_IPV4_NETWORKS = [
    ipaddress.IPv4Network("127.0.0.0/8"),
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("169.254.0.0/16"),
    ipaddress.IPv4Network("0.0.0.0/8"),
]

_BLOCKED_IPV6_NETWORKS = [
    ipaddress.IPv6Network("::1/128"),
    ipaddress.IPv6Network("fc00::/7"),
    ipaddress.IPv6Network("fe80::/10"),
]


def _is_private_ip(ip_str: str) -> bool:
    """Return True if the IP address falls within a blocked range."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # Unparseable addresses are blocked.

    if isinstance(addr, ipaddress.IPv4Address):
        return any(addr in net for net in _BLOCKED_IPV4_NETWORKS)
    return any(addr in net for net in _BLOCKED_IPV6_NETWORKS)


def _resolve_and_check(hostname: str) -> None:
    """Resolve *hostname* and raise if any resolved address is private.

    This prevents SSRF attacks where a public hostname resolves to a
    private/internal IP address.
    """
    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"DNS resolution failed for '{hostname}': {exc}") from exc

    if not results:
        raise ValueError(f"DNS resolution returned no results for '{hostname}'")

    for _family, _type, _proto, _canonname, sockaddr in results:
        ip = sockaddr[0]
        if _is_private_ip(ip):
            raise ValueError(
                "SSRF blocked: requests to private/internal addresses are not allowed"
            )


def _looks_like_json(text: str) -> bool:
    """Heuristic check for JSON-shaped strings."""
    stripped = text.strip()
    return (stripped.startswith("{") and stripped.endswith("}")) or (
        stripped.startswith("[") and stripped.endswith("]")
    )


# ------------------------------------------------------------------
# Tool implementation
# ------------------------------------------------------------------


class HttpRequestTool(BaseTool):
    """Send HTTP requests to any URL.

    A general-purpose HTTP client tool for calling REST APIs directly.
    Supports GET, POST, PUT, PATCH, DELETE methods with custom headers,
    query parameters, and request body.

    Security features:
    - SSRF protection (blocks private/internal IP ranges)
    - Only ``http`` and ``https`` schemes allowed
    - Configurable timeout with a hard cap at 120 s
    - Response body truncated at 200 KB
    """

    def __init__(self, *, timeout: int = _DEFAULT_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Tool protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "http_request"

    @property
    def display_name(self) -> str:
        return "HTTP Request"

    @property
    def category(self) -> str:
        return "web"

    @property
    def description(self) -> str:
        return (
            "Send HTTP requests to REST APIs and return raw responses (status, headers, body). "
            "Supports GET, POST, PUT, PATCH, DELETE with custom headers, params, and body. "
            "Best for JSON APIs and structured data. For reading web pages, use web_fetch instead."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                    "description": "HTTP method. Defaults to GET.",
                },
                "url": {
                    "type": "string",
                    "description": "The full URL to send the request to.",
                },
                "headers": {
                    "type": "object",
                    "description": "Optional HTTP headers as key-value pairs.",
                },
                "params": {
                    "type": "object",
                    "description": "Optional URL query parameters as key-value pairs.",
                },
                "body": {
                    "type": "string",
                    "description": (
                        "Optional request body. For JSON APIs, pass a JSON string."
                    ),
                },
                "timeout": {
                    "type": "number",
                    "description": "Request timeout in seconds. Defaults to 30.",
                },
            },
            "required": ["url"],
        }

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def run(self, **kwargs: Any) -> str:  # noqa: C901
        url: str = kwargs.get("url", "").strip()
        if not url:
            return "[Error] Invalid URL: empty string"

        # --- Validate scheme ---
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return "[Error] Unsupported scheme: only http and https are allowed"

        hostname = parsed.hostname
        if not hostname:
            return f"[Error] Invalid URL: cannot extract hostname from '{url}'"

        # --- SSRF check ---
        try:
            _resolve_and_check(hostname)
        except ValueError as exc:
            return f"[Error] {exc}"

        # --- Method ---
        method: str = kwargs.get("method", "GET").upper().strip()
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            return f"[Error] Unsupported HTTP method: {method}"

        # --- Timeout ---
        timeout_val: float = _DEFAULT_TIMEOUT_SECONDS
        raw_timeout = kwargs.get("timeout")
        if raw_timeout is not None:
            try:
                timeout_val = float(raw_timeout)
            except (TypeError, ValueError):
                timeout_val = _DEFAULT_TIMEOUT_SECONDS
            timeout_val = max(1.0, min(timeout_val, _MAX_TIMEOUT_SECONDS))

        # --- Headers ---
        req_headers: dict[str, str] = {"User-Agent": _USER_AGENT}
        custom_headers = kwargs.get("headers")
        if isinstance(custom_headers, dict):
            req_headers.update({str(k): str(v) for k, v in custom_headers.items()})

        # --- Query params ---
        req_params: dict[str, str] | None = None
        custom_params = kwargs.get("params")
        if isinstance(custom_params, dict):
            req_params = {str(k): str(v) for k, v in custom_params.items()}

        # --- Body ---
        body: str | None = kwargs.get("body")
        content_bytes: bytes | None = None
        if body is not None:
            body = str(body)
            # Auto-detect JSON content type.
            if "Content-Type" not in req_headers and "content-type" not in req_headers:
                if _looks_like_json(body):
                    req_headers["Content-Type"] = "application/json"
            content_bytes = body.encode("utf-8")

        # --- Send request ---
        try:
            async with httpx.AsyncClient(
                timeout=timeout_val,
                follow_redirects=True,
                max_redirects=_MAX_REDIRECTS,
            ) as client:
                resp = await client.request(
                    method=method,
                    url=url,
                    headers=req_headers,
                    params=req_params,
                    content=content_bytes,
                )
        except httpx.TimeoutException:
            return f"[Timeout] Request timed out after {timeout_val:.0f}s"
        except httpx.TooManyRedirects:
            return f"[Error] Too many redirects (max {_MAX_REDIRECTS})"
        except httpx.RequestError as exc:
            return f"[Error] {exc}"

        # --- Build response ---
        return self._format_response(resp)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_response(resp: httpx.Response) -> str:
        """Format an httpx Response into a human-readable string."""
        parts: list[str] = []

        # Status line
        reason = resp.reason_phrase or ""
        parts.append(f"HTTP {resp.status_code} {reason}".strip())

        # Response headers (selected subset to keep output concise)
        _interesting_headers = {
            "content-type",
            "content-length",
            "date",
            "server",
            "x-request-id",
            "x-ratelimit-remaining",
            "retry-after",
            "location",
            "cache-control",
        }
        header_lines: list[str] = []
        for key, value in resp.headers.items():
            if key.lower() in _interesting_headers:
                header_lines.append(f"{key}: {value}")
        if header_lines:
            parts.append("\nHeaders:\n" + "\n".join(header_lines))

        # Body
        raw_body = resp.content
        truncated = False
        if len(raw_body) > _MAX_RESPONSE_BYTES:
            raw_body = raw_body[:_MAX_RESPONSE_BYTES]
            truncated = True

        # Attempt to decode as text.
        try:
            text = raw_body.decode(resp.encoding or "utf-8", errors="replace")
        except (UnicodeDecodeError, LookupError):
            text = raw_body.decode("utf-8", errors="replace")

        # Pretty-print JSON responses for readability.
        content_type = resp.headers.get("content-type", "")
        if "json" in content_type or "javascript" in content_type:
            try:
                parsed_json = json.loads(text)
                text = json.dumps(parsed_json, indent=2, ensure_ascii=False)
            except (json.JSONDecodeError, ValueError):
                pass  # Not valid JSON; keep raw text.

        body_section = "\nBody:\n" + text
        if truncated:
            body_section += (
                f"\n\n[Truncated — response exceeded {_MAX_RESPONSE_BYTES // 1024} KB]"
            )

        parts.append(body_section)
        return "\n".join(parts)

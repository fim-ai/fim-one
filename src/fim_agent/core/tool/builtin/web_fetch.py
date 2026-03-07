"""Built-in tool for fetching web page content.

Delegates to the configured BaseWebFetch backend (Jina or plain httpx).
Backend selection is controlled by the WEB_FETCH_PROVIDER environment variable.
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.parse
from typing import Any

import httpx

from fim_agent.core.web.fetch import get_web_fetcher

from ..base import BaseTool

_DEFAULT_TIMEOUT: int = 30
_MAX_CHARS: int = 20_000

# Explicit blocklist — avoid Python's ``is_private`` which over-blocks in 3.11+
# (e.g. 198.18.0.0/15 used by TUN-mode proxies like Clash/Surge).
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


def _is_blocked_ip(ip_str: str) -> bool:
    """Return True if the IP address falls within a blocked range."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # Unparseable addresses are blocked.

    if isinstance(addr, ipaddress.IPv4Address):
        return any(addr in net for net in _BLOCKED_IPV4_NETWORKS)
    return any(addr in net for net in _BLOCKED_IPV6_NETWORKS)


def _validate_url(url: str) -> None:
    """Validate a URL against SSRF risks.

    Raises ValueError if:
    - The scheme is not http or https.
    - The resolved IP falls in a blocked internal range.

    DNS resolution failures are treated as non-blocking: if the hostname
    cannot be resolved here, the request is allowed to proceed and will
    fail naturally at the HTTP layer.
    """
    parsed = urllib.parse.urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"Blocked URL scheme '{parsed.scheme}': only http and https are allowed."
        )

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL contains no hostname.")

    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        # DNS resolution failed — not an SSRF issue; let the fetcher handle it.
        return

    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        ip = sockaddr[0]
        if _is_blocked_ip(ip):
            raise ValueError(
                f"Blocked request to internal address '{ip}' "
                f"resolved from hostname '{hostname}'."
            )


class WebFetchTool(BaseTool):
    """Fetch a URL and return its content as clean Markdown or plain text.

    Supports Jina Reader (clean Markdown output) and plain httpx (text extraction).
    Backend is selected via the WEB_FETCH_PROVIDER environment variable.
    """

    def __init__(self, *, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def category(self) -> str:
        return "web"

    @property
    def description(self) -> str:
        return (
            "Fetch a web page and return its content as clean Markdown text "
            "(HTML is converted, navigation/ads stripped). "
            "Best for reading articles, blog posts, documentation, and Wikipedia. "
            "For REST APIs that return JSON, use http_request instead."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch (must start with http:// or https://).",
                },
            },
            "required": ["url"],
        }

    async def run(self, **kwargs: Any) -> str:
        url: str = kwargs.get("url", "").strip()
        if not url:
            return "[Error] No URL provided."

        try:
            _validate_url(url)
        except ValueError as exc:
            return f"[Blocked] {exc}"

        fetcher = get_web_fetcher(timeout=self._timeout)
        try:
            content = await fetcher.fetch(url)
        except httpx.TimeoutException:
            return f"[Timeout] Request exceeded {self._timeout} seconds."
        except httpx.HTTPStatusError as exc:
            return f"[HTTP {exc.response.status_code}] {exc.response.text[:500]}"
        except httpx.RequestError as exc:
            return f"[Error] {exc}"

        if len(content) > _MAX_CHARS:
            content = content[:_MAX_CHARS] + f"\n\n[Truncated — {len(content)} chars total]"
        return content

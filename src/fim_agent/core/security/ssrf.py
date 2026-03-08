"""Shared SSRF (Server-Side Request Forgery) protection.

Provides URL and hostname validation against internal/private IP ranges.
Used by http_request, web_fetch, connector adapter, and OpenAPI importer.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# ------------------------------------------------------------------
# Blocked IP ranges
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


def is_private_ip(ip_str: str) -> bool:
    """Return True if the IP address falls within a blocked range."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # Unparseable addresses are blocked.

    if isinstance(addr, ipaddress.IPv4Address):
        return any(addr in net for net in _BLOCKED_IPV4_NETWORKS)
    return any(addr in net for net in _BLOCKED_IPV6_NETWORKS)


def resolve_and_check(hostname: str) -> None:
    """Resolve *hostname* and raise if any resolved address is private.

    Raises:
        ValueError: If DNS resolution fails or any resolved IP is private.
    """
    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"DNS resolution failed for '{hostname}': {exc}") from exc

    if not results:
        raise ValueError(f"DNS resolution returned no results for '{hostname}'")

    for _family, _type, _proto, _canonname, sockaddr in results:
        ip = sockaddr[0]
        if is_private_ip(ip):
            raise ValueError(
                "SSRF blocked: requests to private/internal addresses are not allowed"
            )


def validate_url(url: str, *, allow_dns_failure: bool = False) -> None:
    """Validate a URL against SSRF risks.

    Args:
        url: The URL to validate.
        allow_dns_failure: If True, DNS resolution failures are treated as
            non-blocking (the request proceeds and may fail at the HTTP layer).
            If False (default), DNS failures raise ValueError.

    Raises:
        ValueError: If the URL scheme is not http/https, has no hostname,
            or resolves to a private/internal IP address.
    """
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"Blocked URL scheme '{parsed.scheme}': only http and https are allowed."
        )

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL contains no hostname.")

    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        if allow_dns_failure:
            return
        raise ValueError(f"DNS resolution failed for '{hostname}': {exc}") from exc

    if not results:
        if allow_dns_failure:
            return
        raise ValueError(f"DNS resolution returned no results for '{hostname}'")

    for _family, _type, _proto, _canonname, sockaddr in results:
        ip = sockaddr[0]
        if is_private_ip(ip):
            raise ValueError(
                f"Blocked request to internal address '{ip}' "
                f"resolved from hostname '{hostname}'."
            )

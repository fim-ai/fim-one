"""Shared SSRF (Server-Side Request Forgery) protection.

Provides URL and hostname validation against internal/private IP ranges.
Used by http_request, web_fetch, connector adapter, and OpenAPI importer.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

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
    # Shared address space; Alibaba Cloud's metadata service is 100.100.100.200.
    ipaddress.IPv4Network("100.64.0.0/10"),
    ipaddress.IPv4Network("192.0.0.0/24"),
    ipaddress.IPv4Network("224.0.0.0/4"),
    ipaddress.IPv4Network("240.0.0.0/4"),
]

# Benchmarking range (RFC 2544).  Not routed on the public Internet, so a
# real service there is an internal one.  Fake-IP DNS proxies (Surge, Clash)
# resolve every public host name into it; a machine behind one sets
# ``SSRF_ALLOW_FAKE_IP_RANGE=true``.
_FAKE_IP_NETWORK = ipaddress.IPv4Network("198.18.0.0/15")
FAKE_IP_ENV = "SSRF_ALLOW_FAKE_IP_RANGE"

_BLOCKED_IPV6_NETWORKS = [
    ipaddress.IPv6Network("::/128"),
    ipaddress.IPv6Network("::1/128"),
    ipaddress.IPv6Network("fc00::/7"),
    ipaddress.IPv6Network("fe80::/10"),
    ipaddress.IPv6Network("fec0::/10"),  # site-local (deprecated)
    ipaddress.IPv6Network("ff00::/8"),
    # IPv4-compatible addresses (deprecated): ``::a.b.c.d``.
    ipaddress.IPv6Network("::/96"),
    # Teredo: carries an IPv4 endpoint behind a relay.
    ipaddress.IPv6Network("2001::/32"),
]

# Prefixes that carry an IPv4 address inside an IPv6 one.  A connection to
# such an address ends at the embedded IPv4 host, so that is the address
# that has to pass.
_NAT64_WELL_KNOWN = ipaddress.IPv6Network("64:ff9b::/96")
_NAT64_LOCAL_USE = ipaddress.IPv6Network("64:ff9b:1::/48")
_SIX_TO_FOUR = ipaddress.IPv6Network("2002::/16")


def _fake_ip_range_allowed() -> bool:
    return os.environ.get(FAKE_IP_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _embedded_ipv4(addr: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """The IPv4 address *addr* stands for, if it is a translation address."""
    if addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    packed = addr.packed
    if addr in _NAT64_LOCAL_USE:
        # RFC 6052, /48 prefix: 16 bits, the reserved octet, then 16 bits.
        return ipaddress.IPv4Address(packed[6:8] + packed[9:11])
    if addr in _NAT64_WELL_KNOWN:
        return ipaddress.IPv4Address(packed[12:16])
    if addr in _SIX_TO_FOUR:
        return ipaddress.IPv4Address(packed[2:6])
    return None


def _is_blocked_ipv4(addr: ipaddress.IPv4Address) -> bool:
    if any(addr in net for net in _BLOCKED_IPV4_NETWORKS):
        return True
    return addr in _FAKE_IP_NETWORK and not _fake_ip_range_allowed()


def is_private_ip(ip_str: str) -> bool:
    """Return True if the IP address falls within a blocked range."""
    try:
        addr = ipaddress.ip_address(ip_str.split("%", 1)[0] if isinstance(ip_str, str) else ip_str)
    except ValueError:
        return True  # Unparseable addresses are blocked.

    if isinstance(addr, ipaddress.IPv4Address):
        return _is_blocked_ipv4(addr)

    # IPv4-mapped (``::ffff:a.b.c.d``), NAT64 and 6to4 addresses reach an IPv4
    # host: judge that host.  The IPv4 list applies to them as well.
    embedded = _embedded_ipv4(addr)
    if embedded is not None:
        return _is_blocked_ipv4(embedded)

    return any(addr in net for net in _BLOCKED_IPV6_NETWORKS)


class UnreachableHostError(ValueError):
    """A host name refused for an outbound request.

    Raised whether the name resolves to an internal address or does not
    resolve at all, with the same message (:func:`unreachable_host_message`).
    """


def unreachable_host_message(hostname: str) -> str:
    """What the caller is told of a host name that may not be reached.

    One message whether the name resolves to an internal address, resolves
    to nothing, or fails to resolve: a different answer for each would tell
    a user which internal names exist.
    """
    return f"SSRF blocked: the host '{hostname}' does not resolve to a public address."


def _internal_host_refusal(hostname: str, ip: str) -> UnreachableHostError:
    """The refusal for a name that resolves to an internal address.

    The address it resolved to is logged and never returned to the caller.
    """
    logger.warning("Outbound request refused: %r resolves to internal address %s", hostname, ip)
    return UnreachableHostError(unreachable_host_message(hostname))


def _unresolved_host_refusal(hostname: str, reason: object) -> UnreachableHostError:
    """The refusal for a name that does not resolve; the same message as above."""
    logger.info("Outbound request refused: %r did not resolve (%s)", hostname, reason)
    return UnreachableHostError(unreachable_host_message(hostname))


def resolve_and_check(hostname: str) -> None:
    """Resolve *hostname* and raise if any resolved address is private.

    Raises:
        ValueError: If DNS resolution fails or any resolved IP is private.
    """
    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise _unresolved_host_refusal(hostname, exc) from None

    if not results:
        raise _unresolved_host_refusal(hostname, "no addresses")

    for _family, _type, _proto, _canonname, sockaddr in results:
        ip = str(sockaddr[0])
        if is_private_ip(ip):
            raise _internal_host_refusal(hostname, ip)


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
        raise _unresolved_host_refusal(hostname, exc) from None

    if not results:
        if allow_dns_failure:
            return
        raise _unresolved_host_refusal(hostname, "no addresses")

    for _family, _type, _proto, _canonname, sockaddr in results:
        ip = str(sockaddr[0])
        if is_private_ip(ip):
            raise _internal_host_refusal(hostname, ip)


# ------------------------------------------------------------------
# Transport-level DNS pinning (prevents DNS rebinding)
# ------------------------------------------------------------------


def _is_ip_literal(host: str) -> bool:
    """Return True if *host* is already an IP address (v4 or v6)."""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _resolve_and_pin(hostname: str) -> str:
    """Resolve *hostname*, check all IPs, return the one to connect to.

    Prefers the first IPv4 address: the pinned address gets no Happy
    Eyeballs fallback, and many hosts have no IPv6 route.

    Raises:
        ValueError: If DNS fails or any resolved IP is private/internal.
    """
    try:
        results = socket.getaddrinfo(
            hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise _unresolved_host_refusal(hostname, exc) from None

    if not results:
        raise _unresolved_host_refusal(hostname, "no addresses")

    first_ip: str | None = None
    first_ipv4: str | None = None
    for family, _type, _proto, _canonname, sockaddr in results:
        ip = str(sockaddr[0])
        if is_private_ip(ip):
            raise _internal_host_refusal(hostname, ip)
        if first_ip is None:
            first_ip = ip
        if first_ipv4 is None and family == socket.AF_INET:
            first_ipv4 = ip

    assert first_ip is not None
    return first_ipv4 or first_ip


class SSRFSafeTransport(httpx.AsyncHTTPTransport):
    """httpx transport that pins DNS resolution to prevent rebinding.

    On each request, resolves the hostname ourselves, checks all IPs
    against the private range blocklist, then rewrites the request URL
    to use the resolved IP directly (preserving the original Host header).
    IP-literal hosts are checked against the same blocklist, so a redirect
    to e.g. ``http://169.254.169.254/`` is refused like a hostname would be.
    """

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host and _is_ip_literal(host):
            if is_private_ip(host):
                raise ValueError(
                    f"SSRF blocked: '{host}' is a private/internal address"
                )
        elif host:
            # getaddrinfo blocks; keep it off the event loop.
            ip = await asyncio.to_thread(_resolve_and_pin, host)
            # Rewrite URL to use the pinned IP; preserve original Host header
            request.url = request.url.copy_with(host=ip)
            # Preserve original hostname for TLS SNI so certificate verification
            # matches the domain (not the pinned IP). As text: the TLS layer
            # encodes it itself, and fails on bytes.
            request.extensions["sni_hostname"] = host
            # Ensure the original hostname is sent in the Host header
            if "host" not in request.headers:
                request.headers["host"] = host
        return await super().handle_async_request(request)


def get_safe_async_client(**kwargs: Any) -> httpx.AsyncClient:
    """Factory returning an httpx.AsyncClient with SSRF-safe DNS pinning.

    Usage::

        async with get_safe_async_client(timeout=30) as client:
            resp = await client.get("https://example.com")

    Proxy settings and ``.netrc`` credentials from the environment are not
    used (``trust_env=False``): a request through a proxy would be resolved
    by the proxy, past the address check. A proxy or extra mounts cannot be
    passed for the same reason.
    """
    for key in ("proxy", "proxies", "mounts", "trust_env"):
        if key in kwargs:
            raise TypeError(f"get_safe_async_client() does not accept {key!r}")
    transport = kwargs.pop("transport", None)
    if transport is None:
        transport = SSRFSafeTransport()
    return httpx.AsyncClient(transport=transport, trust_env=False, **kwargs)

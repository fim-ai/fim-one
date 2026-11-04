"""Enforce the admin IP allow/deny rules on API requests.

A deny rule that matches the client blocks it.  When at least one allow rule
is active the instance is in allowlist mode: a client that matches no allow
rule is blocked.  With no active rules every client passes.

The client address is ``request.client.host``.  Behind a reverse proxy that is
the proxy's address unless uvicorn trusts the proxy's ``X-Forwarded-For``
(``FORWARDED_ALLOW_IPS``).  ``IP_RULES_ENFORCED=false`` turns enforcement off
without deleting rules, for an administrator locked out by a bad rule.
"""

from __future__ import annotations

import ipaddress
import os
import time
from collections.abc import Iterable

from sqlalchemy import select

IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

# Paths that stay reachable for every client: CORS preflight is handled by
# method, these by prefix.
_PASS_PREFIXES = ("/api/version", "/api/webhooks/")
_TTL = 5.0


def enforcement_enabled() -> bool:
    return os.getenv("IP_RULES_ENFORCED", "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def parse_rules(
    rows: Iterable[tuple[str, str]],
) -> tuple[list[IpNetwork], list[IpNetwork]]:
    """Split ``(ip_address, rule_type)`` rows into allow and deny networks."""
    allow: list[IpNetwork] = []
    deny: list[IpNetwork] = []
    for address, rule_type in rows:
        try:
            network = ipaddress.ip_network(address.strip(), strict=False)
        except ValueError:
            continue
        (allow if rule_type == "allow" else deny).append(network)
    return allow, deny


def is_client_allowed(client_ip: str | None, allow: list[IpNetwork], deny: list[IpNetwork]) -> bool:
    if not allow and not deny:
        return True
    try:
        ip = ipaddress.ip_address(client_ip or "")
    except ValueError:
        # An unknown address can only fail an allowlist, never match a deny.
        return not allow
    if any(ip in net for net in deny):
        return False
    if allow:
        return any(ip in net for net in allow)
    return True


def path_is_exempt(path: str, method: str) -> bool:
    return method == "OPTIONS" or any(path.startswith(p) for p in _PASS_PREFIXES)


class RuleCache:
    """Active rules, reloaded at most every ``_TTL`` seconds per worker."""

    def __init__(self) -> None:
        self._rules: tuple[list[IpNetwork], list[IpNetwork]] | None = None
        self._loaded_at = 0.0

    async def get(self) -> tuple[list[IpNetwork], list[IpNetwork]]:
        now = time.monotonic()
        if self._rules is not None and now - self._loaded_at < _TTL:
            return self._rules

        from fim_one.db import create_session
        from fim_one.db.models import IpRule

        async with create_session() as db:
            result = await db.execute(
                select(IpRule.ip_address, IpRule.rule_type).where(IpRule.is_active.is_(True))
            )
            self._rules = parse_rules((r[0], r[1]) for r in result.all())
        self._loaded_at = now
        return self._rules

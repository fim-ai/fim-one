"""Where a database connector may connect.

A database connector dials a host and port its creator typed.  Left
unchecked that is a way to reach whatever the application server can reach:
the application's own database, other services on its network, cloud
metadata addresses.  This module applies the same address rules as the HTTP
SSRF guard (:mod:`fim_one.core.security.ssrf`) to that host:

* The host is resolved here and **every** resolved address is checked; the
  driver then connects to the checked address, not to the name, so a second
  DNS answer cannot differ from the one that was checked.
* Private, loopback, link-local, shared-address and metadata ranges are
  refused unless the administrator allowed the target in
  ``DB_CONNECTOR_ALLOWED_HOSTS``.
* The hosts of the application's own database and Redis (``DATABASE_URL``,
  ``REDIS_URL``, including a ``?host=`` query parameter) are never a valid
  target, on any port, allow-list or not.  They are compared by resolved
  address; when one of them cannot be resolved, every target is refused.
* Fields that end up in the wire protocol's startup packet are refused when
  they contain control characters, and the host must be a bare host name or
  IP literal (no credentials, path, port list or socket path).

``DB_CONNECTOR_ALLOWED_HOSTS`` is a comma-separated list.  Each entry is an
IP address, a CIDR network, a host name, or a ``*.suffix`` wildcard, with an
optional ``:port`` restriction (``[v6]:port`` for IPv6)::

    DB_CONNECTOR_ALLOWED_HOSTS=10.20.0.0/16,db.corp.internal:5432,*.rds.internal

An address or network entry allows targets whose resolved addresses all fall
inside it.  A name entry allows the name as typed in the connector, whatever
it resolves to.  The default is empty: public addresses only.

An entry that cannot be parsed refuses every target, public ones included,
until it is corrected.  The entry is written to the server log; the user is
told only that the setting is invalid.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .ssrf import is_private_ip

logger = logging.getLogger(__name__)

ALLOWLIST_ENV = "DB_CONNECTOR_ALLOWED_HOSTS"

_HOSTNAME_RE = re.compile(
    r"(?=.{1,253}$)[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,62})(?:\.[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,62}))*\.?"
)
# Config keys whose value travels in the protocol handshake.
_WIRE_FIELDS = ("username", "password", "database", "schema", "charset")
_DEFAULT_PORTS = {"postgresql": 5432, "postgres": 5432, "mysql": 3306, "redis": 6379, "rediss": 6379}


class DatabaseTargetError(ValueError):
    """The connector's host, port or credentials may not be used."""


@dataclass(frozen=True)
class _AllowEntry:
    network: ipaddress.IPv4Network | ipaddress.IPv6Network | None
    name: str | None  # exact host name, or ".suffix" for a wildcard
    port: int | None

    def allows_address(self, ip: str, port: int) -> bool:
        if self.network is None or (self.port is not None and self.port != port):
            return False
        addr = _unmap(ipaddress.ip_address(ip))
        return addr.version == self.network.version and addr in self.network

    def allows_name(self, host: str, port: int) -> bool:
        if self.name is None or (self.port is not None and self.port != port):
            return False
        if self.name.startswith("."):
            return host.endswith(self.name) and len(host) > len(self.name)
        return host == self.name


def _unmap(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def _split_port(entry: str) -> tuple[str, int | None]:
    """Split an allow-list entry into ``(target, port)``."""
    if entry.startswith("["):
        target, closed, rest = entry[1:].partition("]")
        if not closed:
            raise ValueError(entry)
        if not rest:
            return target, None
        if rest.startswith(":") and rest[1:].isdigit():
            return target, int(rest[1:])
        raise ValueError(entry)
    if entry.count(":") == 1:
        target, _, port = entry.partition(":")
        if not port.isdigit():
            raise ValueError(entry)
        return target, int(port)
    return entry, None  # bare name, IPv4, CIDR, or unbracketed IPv6


def parse_allowlist(raw: str | None) -> list[_AllowEntry]:
    """Parse ``DB_CONNECTOR_ALLOWED_HOSTS``.

    Raises:
        ValueError: on an entry that is none of the accepted forms.  A
            mistyped allow-list must not silently allow less — or more —
            than the administrator meant.
    """
    entries: list[_AllowEntry] = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            target, port = _split_port(item)
            if port is not None and not 1 <= port <= 65535:
                raise ValueError(item)
            try:
                network = ipaddress.ip_network(target, strict=False)
            except ValueError:
                network = None
            if network is not None:
                entries.append(_AllowEntry(network, None, port))
                continue
            name = target.lower().rstrip(".")
            wildcard = name.startswith("*.")
            bare = name[2:] if wildcard else name
            if not bare or _HOSTNAME_RE.fullmatch(bare) is None or bare.replace(".", "").isdigit():
                raise ValueError(item)
            entries.append(_AllowEntry(None, f".{bare}" if wildcard else bare, port))
        except ValueError as exc:
            raise ValueError(
                f"{ALLOWLIST_ENV}: cannot parse entry {item!r}. Use an IP address, a "
                "CIDR network, a host name or a *.suffix wildcard, optionally "
                "followed by :port."
            ) from exc
    return entries


def _allowlist() -> list[_AllowEntry]:
    return parse_allowlist(os.environ.get(ALLOWLIST_ENV))


def _not_public(host: str) -> DatabaseTargetError:
    """One refusal whether *host* is internal or does not resolve.

    A different answer for each would tell a user which internal names exist.
    """
    return DatabaseTargetError(
        f"The database host '{host}' does not resolve to a public address. An "
        f"administrator can allow internal hosts with {ALLOWLIST_ENV}."
    )


def _resolve(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        logger.info("Database connector target refused: %r did not resolve (%s)", host, exc)
        raise _not_public(host) from None
    addresses: list[str] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        ip = str(sockaddr[0]).split("%", 1)[0]  # drop an IPv6 zone id
        if ip not in addresses:
            addresses.append(ip)
    if not addresses:
        raise _not_public(host)
    return addresses


def _own_infrastructure() -> list[str]:
    """Network hosts of the services this application itself depends on.

    The host of each URL, every host of a multi-host one, and any ``host``
    query parameter (``postgresql://u:p@/db?host=10.0.0.5``).  A Unix
    socket path is not a network host and is left out.
    """
    hosts: list[str] = []
    for env in ("DATABASE_URL", "REDIS_URL"):
        url = os.environ.get(env, "").strip()
        if not url:
            continue
        try:
            parts = urlsplit(url)
        except ValueError:
            continue
        netloc = parts.netloc.rpartition("@")[2]
        candidates = [item for item in netloc.split(",") if item]
        for value in parse_qs(parts.query).get("host", []):
            candidates.extend(item for item in value.split(",") if item)
        for item in candidates:
            item = item.strip()
            if item.startswith("["):
                host = item[1:].partition("]")[0]
            elif item.count(":") == 1:
                host = item.partition(":")[0]
            else:
                host = item
            if host and not host.startswith(("/", "@")) and host not in hosts:
                hosts.append(host)
    return hosts


def _is_own_infrastructure(addresses: list[str]) -> bool:
    """Whether any of *addresses* is a host of the application's own services.

    Raises:
        DatabaseTargetError: when one of those hosts cannot be resolved, so
            that a target cannot be told apart from it.
    """
    canonical = {str(_unmap(ipaddress.ip_address(ip))) for ip in addresses}
    for own_host in _own_infrastructure():
        try:
            own = {str(_unmap(ipaddress.ip_address(ip))) for ip in _resolve(own_host, 0)}
        except DatabaseTargetError as exc:
            logger.error(
                "Database connectors are refused: the application's own database or "
                "Redis host %r cannot be resolved, so a target cannot be checked against it.",
                own_host,
            )
            raise DatabaseTargetError(
                "Database connections are unavailable on this server because of a "
                "configuration problem. An administrator can find the details in the "
                "server log."
            ) from exc
        if own & canonical:
            return True
        # Loopback is one host, whichever loopback address names it.
        if any(ipaddress.ip_address(ip).is_loopback for ip in own) and any(
            ipaddress.ip_address(ip).is_loopback for ip in canonical
        ):
            return True
    return False


def check_db_config_fields(config: dict[str, Any]) -> tuple[str, int]:
    """Validate the shape of a connector config; return ``(host, port)``.

    No name resolution happens here, so this is safe to call from request
    validation.

    Raises:
        DatabaseTargetError: for a malformed host or port, or a control
            character in a field that reaches the wire protocol.
    """
    for key in _WIRE_FIELDS:
        value = config.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise DatabaseTargetError(f"The connection setting '{key}' must be text.")
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
            raise DatabaseTargetError(
                f"The connection setting '{key}' contains control characters."
            )

    raw_host = config.get("host")
    if not isinstance(raw_host, str) or not raw_host.strip(" "):
        raise DatabaseTargetError("A database host is required.")
    # Pasted values often carry surrounding spaces; nothing else is trimmed.
    host = raw_host.strip(" ")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    is_ip = True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        is_ip = False
    if not is_ip and _HOSTNAME_RE.fullmatch(host) is None:
        raise DatabaseTargetError(
            "The database host must be a host name or an IP address, without "
            "credentials, a port, a path or a list of hosts."
        )
    try:
        port = int(config.get("port") or 0)
    except (TypeError, ValueError) as exc:
        raise DatabaseTargetError("The database port must be a number.") from exc
    if not 1 <= port <= 65535:
        raise DatabaseTargetError("The database port must be between 1 and 65535.")
    return host.lower().rstrip("."), port


def authorize_database_target(config: dict[str, Any]) -> str:
    """Decide whether a connector may dial its target; return the address to dial.

    Blocking (resolves the host); call it from a worker thread.

    Returns:
        One resolved IP address, which the driver must connect to in place
        of the configured host.

    Raises:
        DatabaseTargetError: when the target is malformed, unresolvable,
            internal and not allow-listed, or the application's own
            database or Redis.
    """
    host, port = check_db_config_fields(config)
    addresses = _resolve(host, port)

    if _is_own_infrastructure(addresses):
        logger.warning(
            "Database connector target refused: %r (%s) is a host of the application",
            host, ", ".join(addresses),
        )
        raise DatabaseTargetError(
            "This host belongs to the application itself and cannot be used by "
            "a database connector."
        )

    try:
        allowlist = _allowlist()
    except ValueError as exc:
        # Fail closed. The entry itself is for the administrator's eyes.
        logger.error("Database connectors are refused: %s", exc)
        raise DatabaseTargetError(
            f"Database connections are unavailable on this server: {ALLOWLIST_ENV} "
            "is not valid. An administrator can find the details in the server log."
        ) from exc

    name_allowed = any(entry.allows_name(host, port) for entry in allowlist)
    if not name_allowed:
        for ip in addresses:
            if is_private_ip(ip) and not any(
                entry.allows_address(ip, port) for entry in allowlist
            ):
                logger.warning(
                    "Database connector target refused: %r resolves to internal "
                    "address %s, port %d, which %s does not allow",
                    host, ip, port, ALLOWLIST_ENV,
                )
                raise _not_public(host)

    ipv4 = [ip for ip in addresses if ":" not in ip]
    return (ipv4 or addresses)[0]

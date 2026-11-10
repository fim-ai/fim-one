"""Keeps the step editor's "list this MCP server's tools" from piling up.

Listing opens the server: a subprocess for stdio, a network connection
otherwise, and up to half a minute of waiting. The editor asks every time
an MCP step is opened. Two things bound that:

- a listing is remembered for :data:`TTL_SECONDS`, for the user who asked
  and for the server and credential it was asked with. It is never served
  to another user, and a changed server or credential has another
  fingerprint, so what was remembered for the old one is not used;
- a user has at most :data:`MAX_IN_FLIGHT_PER_USER` listings being fetched
  at a time; one more is refused (:class:`TooManyListings`).

The state lives in this process: with several workers each has its own,
so the bounds are per worker.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

__all__ = [
    "MAX_IN_FLIGHT_PER_USER",
    "TTL_SECONDS",
    "McpToolListings",
    "TooManyListings",
    "listing_fingerprint",
    "listings",
]

# How long a listing is served again without asking the server. The
# frontend remembers its own copy for the same time (``MCP_TOOLS_TTL_MS``).
TTL_SECONDS = 30.0
MAX_IN_FLIGHT_PER_USER = 3
_MAX_ENTRIES = 512


class TooManyListings(Exception):
    """The user already has the allowed number of listings being fetched."""


@dataclass(frozen=True)
class _Entry:
    fingerprint: str
    expires_at: float
    tools: list[dict[str, Any]]


def listing_fingerprint(server: Any, credential: Any) -> str:
    """A digest of everything a listing depends on besides who asks.

    The server's connection settings and last update, and the credential it
    would be opened with (whose it is and what it holds). Only the digest is
    kept, never the credential.
    """
    updated_at = getattr(server, "updated_at", None)
    material = {
        "updated_at": updated_at.isoformat() if updated_at is not None else None,
        "transport": getattr(server, "transport", None),
        "url": getattr(server, "url", None),
        "command": getattr(server, "command", None),
        "args": getattr(server, "args", None),
        "working_dir": getattr(server, "working_dir", None),
        "source": str(getattr(credential, "source", "")),
        "env": getattr(credential, "env", None),
        "headers": getattr(credential, "headers", None),
    }
    encoded = json.dumps(material, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


class McpToolListings:
    """Remembered listings and the count of those being fetched, per user."""

    def __init__(
        self,
        *,
        ttl: float = TTL_SECONDS,
        max_in_flight: int = MAX_IN_FLIGHT_PER_USER,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl
        self._max_in_flight = max_in_flight
        self._clock = clock
        self._entries: OrderedDict[tuple[str, str], _Entry] = OrderedDict()
        self._in_flight: dict[str, int] = {}

    def get(self, user_id: str, server_id: str, fingerprint: str) -> list[dict[str, Any]] | None:
        """The listing remembered for this user, server and fingerprint, if still fresh."""
        entry = self._entries.get((user_id, server_id))
        if entry is None:
            return None
        if entry.fingerprint != fingerprint or entry.expires_at <= self._clock():
            del self._entries[(user_id, server_id)]
            return None
        return entry.tools

    def put(
        self, user_id: str, server_id: str, fingerprint: str, tools: list[dict[str, Any]]
    ) -> None:
        key = (user_id, server_id)
        self._entries[key] = _Entry(fingerprint, self._clock() + self._ttl, tools)
        self._entries.move_to_end(key)
        while len(self._entries) > _MAX_ENTRIES:
            self._entries.popitem(last=False)

    @contextmanager
    def fetching(self, user_id: str) -> Iterator[None]:
        """Hold one of the user's fetch slots; :class:`TooManyListings` when none is free."""
        if self._in_flight.get(user_id, 0) >= self._max_in_flight:
            raise TooManyListings
        self._in_flight[user_id] = self._in_flight.get(user_id, 0) + 1
        try:
            yield
        finally:
            left = self._in_flight[user_id] - 1
            if left:
                self._in_flight[user_id] = left
            else:
                del self._in_flight[user_id]

    def clear(self) -> None:
        self._entries.clear()
        self._in_flight.clear()


# The process's listings, used by the step-options endpoint.
listings = McpToolListings()

"""Recording refusals that anyone can provoke, without letting them fill the table.

A request from a blocked address and a trigger call with an unknown key
need no account: someone scanning the service produces them as fast as
they can send. Each is worth one record; a thousand a second are not.

:func:`record_refusal` writes the first refusal of a kind from a source,
then counts the rest for :data:`WINDOW_SECONDS` without writing. The next
one recorded for that source says how many were left out. Sources are
remembered per server process: with several processes each writes its own
first record, which keeps the bound (one per source, per process, per
window) and loses only the exactness of the count. A count still pending
when a process stops is lost.

Two more bounds hold when the sources themselves are many (a scan from a
whole network): at most :data:`MAX_SOURCES` are remembered, and at most
:data:`MAX_RECORDS_PER_WINDOW` records are written per window whatever
their source. What exceeds either is counted and reported with the next
record that is written.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from . import writer as audit
from .events import Actor

__all__ = [
    "MAX_RECORDS_PER_WINDOW",
    "MAX_SOURCES",
    "WINDOW_SECONDS",
    "RefusalThrottle",
    "record_refusal",
]

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 600.0
MAX_SOURCES = 10_000
MAX_RECORDS_PER_WINDOW = 600


@dataclass
class _Seen:
    window_start: float
    left_out: int = 0


class RefusalThrottle:
    """Decides which refusals are written. Not shared between processes."""

    def __init__(
        self,
        *,
        window_seconds: float = WINDOW_SECONDS,
        max_sources: int = MAX_SOURCES,
        max_records_per_window: int = MAX_RECORDS_PER_WINDOW,
    ) -> None:
        self._window = window_seconds
        self._max_sources = max_sources
        self._max_records = max_records_per_window
        self._seen: dict[str, _Seen] = {}
        # The window of the overall bound, and what it and the source bound left out.
        self._budget_start = 0.0
        self._budget_used = 0
        self._overflow = 0

    def admit(self, key: str, *, now: float | None = None) -> tuple[int, int] | None:
        """Whether a refusal from *key* is written now.

        ``None``: no, it was counted. Otherwise ``(repeats, overflow)``: how
        many from this source were left out since its last record, and how
        many from any source the overall bounds left out since they last
        reported.
        """
        moment = time.monotonic() if now is None else now
        seen = self._seen.get(key)
        if seen is not None and moment - seen.window_start < self._window:
            seen.left_out += 1
            return None

        if moment - self._budget_start >= self._window:
            self._budget_start, self._budget_used = moment, 0
        if seen is None and len(self._seen) >= self._max_sources:
            self._forget_expired(moment)
        if self._budget_used >= self._max_records or (
            seen is None and len(self._seen) >= self._max_sources
        ):
            self._overflow += 1
            return None

        repeats = seen.left_out if seen is not None else 0
        self._seen[key] = _Seen(window_start=moment)
        self._budget_used += 1
        overflow, self._overflow = self._overflow, 0
        return repeats, overflow

    def _forget_expired(self, moment: float) -> None:
        expired = [
            key
            for key, seen in self._seen.items()
            if moment - seen.window_start >= self._window and seen.left_out == 0
        ]
        for key in expired:
            del self._seen[key]

    def reset(self) -> None:
        """Forget everything (tests)."""
        self._seen.clear()
        self._budget_start, self._budget_used, self._overflow = 0.0, 0, 0


_throttle = RefusalThrottle()


async def record_refusal(
    action: str,
    *,
    source: str | None,
    detail: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    actor: Actor | None = None,
) -> bool:
    """Record that something from *source* was refused, at most once per window.

    *source* is what the refusals are counted by: the caller's address.
    The record is written in a session of its own with the outcome
    ``denied``; it never raises. Returns whether a record was written.
    """
    verdict = _throttle.admit(f"{action}|{source or '-'}")
    if verdict is None:
        return False
    repeats, overflow = verdict
    notes = [detail] if detail else []
    if repeats:
        notes.append(f"{repeats} more from this source were not recorded since its last record")
    if overflow:
        notes.append(f"{overflow} refusals from other sources were not recorded")
    await audit.record(
        action,
        actor=actor or Actor(user_id=None, label=None, kind="anonymous"),
        target_type=target_type,
        target_id=target_id,
        outcome="denied",
        detail="; ".join(notes) or None,
    )
    return True

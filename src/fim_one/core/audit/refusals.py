"""Recording refusals that anyone can provoke, without letting them fill the table.

A request from a blocked address and a trigger call with an unknown key
need no account: someone scanning the service produces them as fast as
they can send. Each is worth one record; a thousand a second are not.

:func:`record_refusal` writes the first refusal of a kind from a source and
counts the rest for :data:`WINDOW_SECONDS` without writing. When the
window is over the source is forgotten; one from it then is written again.

Each kind of refusal (each action) has its own budget of
:data:`MAX_RECORDS_PER_WINDOW` records per window, whatever their source:
a scan from a whole network cannot write more, and cannot use up another
kind's budget. Only a source that got a record is remembered, and only for
one window, so no more than the budget's worth of sources is ever held:
memory is bounded by the budget, and a new source is never refused its
first record for any reason but the budget of the current window.

What was counted and not written is reported: about one window after the
first uncounted refusal of a kind, one summary record of that kind is
written with the system as actor and the number left out. A count still
pending when a process stops is lost. All of this is per server process:
with several processes each has its own budget and writes its own summary.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from . import writer as audit
from .events import Actor

__all__ = [
    "MAX_RECORDS_PER_WINDOW",
    "WINDOW_SECONDS",
    "RefusalThrottle",
    "admit_refusal",
    "record_refusal",
    "safe_path",
    "write_summary",
]

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 600.0
MAX_RECORDS_PER_WINDOW = 600


@dataclass
class _Kind:
    """What is remembered for one kind of refusal."""

    # source -> [when its first record of the window was written, how many
    # records it has had since]; oldest first.
    written: OrderedDict[str, list[float]] = field(default_factory=OrderedDict)
    budget_start: float = 0.0
    budget_used: int = 0
    # Refusals counted and not written since the last summary.
    left_out: int = 0


class RefusalThrottle:
    """Decides which refusals are written. Not shared between processes."""

    def __init__(
        self,
        *,
        window_seconds: float = WINDOW_SECONDS,
        max_records_per_window: int = MAX_RECORDS_PER_WINDOW,
    ) -> None:
        self._window = window_seconds
        self._max_records = max_records_per_window
        self._kinds: dict[str, _Kind] = {}

    def admit(
        self, action: str, source: str, *, now: float | None = None, per_source: int = 1
    ) -> bool:
        """Whether a refusal of kind *action* from *source* is written now.

        A source gets *per_source* records per window. ``False``: this one
        was counted (:meth:`take_left_out` reports it).
        """
        moment = time.monotonic() if now is None else now
        kind = self._kinds.setdefault(action, _Kind())
        self._forget_expired(kind, moment)
        seen = kind.written.get(source)
        if seen is not None and seen[1] >= per_source:
            kind.left_out += 1
            return False
        if moment - kind.budget_start >= self._window:
            kind.budget_start, kind.budget_used = moment, 0
        if kind.budget_used >= self._max_records:
            kind.left_out += 1
            return False
        if seen is None:
            kind.written[source] = [moment, 1]
        else:
            seen[1] += 1
        kind.budget_used += 1
        return True

    def _forget_expired(self, kind: _Kind, moment: float) -> None:
        # Insertion order is time order: stop at the first one still fresh.
        while kind.written:
            source, (written_at, _count) = next(iter(kind.written.items()))
            if moment - written_at < self._window:
                break
            del kind.written[source]

    def take_left_out(self, action: str) -> int:
        """How many refusals of kind *action* were counted since last asked; resets the count."""
        kind = self._kinds.get(action)
        if kind is None:
            return 0
        count, kind.left_out = kind.left_out, 0
        return count

    def remembered(self) -> int:
        """How many sources are held, over all kinds."""
        return sum(len(kind.written) for kind in self._kinds.values())

    def reset(self) -> None:
        """Forget everything (tests)."""
        self._kinds.clear()


_throttle = RefusalThrottle()
# Kinds with a summary already scheduled.
_summary_pending: set[str] = set()

# How much of a refused request's path is kept: the part that names an area
# of the API. Route names here are lower-case words.
_KEPT_SEGMENTS = 3
_ROUTE_WORD = re.compile(r"[a-z][a-z0-9_-]{0,31}")


def safe_path(path: str) -> str:
    """A request path as it may be stored for a refused request.

    Nothing has matched the path to a route yet, and a path can carry a
    secret of any length (the deprecated trigger route has the key in it;
    a short password is as likely there as a long key). Only the first
    three segments are kept, and each only when it looks like the name of
    a route; whatever else there was is written ``…``. That still says
    which part of the API was probed.
    """
    segments = [segment for segment in path.split("?", 1)[0].split("/") if segment]
    kept = []
    for segment in segments[:_KEPT_SEGMENTS]:
        if not _ROUTE_WORD.fullmatch(segment):
            break
        kept.append(segment)
    return "/" + "/".join(kept) + ("/…" if len(kept) < len(segments) else "")


async def write_summary(action: str) -> bool:
    """Write the summary record for *action* if any refusal was left out; never raises."""
    _summary_pending.discard(action)
    count = _throttle.take_left_out(action)
    if not count:
        return False
    await audit.record(
        action,
        actor=audit.system_actor("refusal summary"),
        outcome="denied",
        detail=(
            f"{count} more refusal(s) of this kind were counted and not recorded one by "
            f"one in the last {int(WINDOW_SECONDS // 60)} minutes"
        ),
    )
    return True


def _schedule_summary(action: str) -> None:
    """Have the summary for *action* written about one window from now, once."""
    if action in _summary_pending:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover - only reachable outside an event loop
        return
    _summary_pending.add(action)

    def _fire() -> None:
        task = loop.create_task(write_summary(action))
        # Keep the task until it is done; the loop holds only a weak reference.
        _summary_tasks.add(task)
        task.add_done_callback(_summary_tasks.discard)

    loop.call_later(WINDOW_SECONDS, _fire)


_summary_tasks: set[asyncio.Task[bool]] = set()


def admit_refusal(action: str, source: str | None, *, per_source: int = 1) -> bool:
    """Whether a record of kind *action* from *source* may be written now.

    For a caller that writes the record itself, to another table (a failed
    sign-in). One that may not is counted and reported in the kind's next
    summary record, like a refusal :func:`record_refusal` leaves out.
    """
    if _throttle.admit(action, source or "-", per_source=per_source):
        return True
    _schedule_summary(action)
    return False


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
    ``denied``; it never raises. A refusal that is only counted is reported
    by the kind's next summary record. Returns whether a record was written.
    """
    if not _throttle.admit(action, source or "-"):
        _schedule_summary(action)
        return False
    await audit.record(
        action,
        actor=actor or Actor(user_id=None, label=None, kind="anonymous"),
        target_type=target_type,
        target_id=target_id,
        outcome="denied",
        detail=detail,
    )
    return True

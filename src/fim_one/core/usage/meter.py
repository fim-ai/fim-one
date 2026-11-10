"""Opening a metering scope and writing what it consumed to the ledger.

An entry point that can reach a model opens a scope and says whose it is::

    async with metered(UsageScope(user_id=..., source_type="endpoint", ...)):
        ...  # every model call in here, in child tasks too, is counted

or, where the scope does not end with a block (a streamed response)::

    opened = open_meter(scope)
    try:
        ...
    finally:
        await opened.close()

Model calls report to the innermost open scope only. A workflow run opens
its own in the task that drives it, so a run an agent starts as a tool is
recorded on the run and not also on the conversation; a delegated agent
opens none and is recorded on the conversation.

Nothing is written per model call. The meter adds up in memory and
:meth:`OpenMeter.flush` turns what was added since the last flush into
ledger rows, one per model: at the end of the scope, however it ends, and
wherever the entry point flushes in between. A scope also writes on its
own, in the background, :data:`CHECKPOINT_INTERVAL` seconds after usage
arrives that nobody has flushed: a long turn or run is written as it goes,
and a scope that is never closed (a response nobody read) is still
written. If the process is killed, what is lost is what its open scopes
consumed in that interval at most, plus the calls in flight.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fim_one.core.model.usage import ModelUsage, UsageMeter, current_meter

from .ledger import UsageEntry, write_entries

if TYPE_CHECKING:
    from fim_one.core.assembly.principal import CallContext

logger = logging.getLogger(__name__)

__all__ = [
    "CHECKPOINT_INTERVAL",
    "OpenMeter",
    "UsageScope",
    "flush_all",
    "metered",
    "open_meter",
    "scope_from_context",
    "wait_for_writes",
]

# Usage nobody flushed is written in the background this long after it arrived.
CHECKPOINT_INTERVAL = 30.0
# ...and this long after, once the scope has ended (a stream the event loop
# closes late, a background task still running).
LATE_FLUSH_DELAY = 1.0

# Background writes in flight, held so they are not collected mid-write.
_writes: set[asyncio.Task[bool]] = set()
# Every scope that may still hold usage nobody wrote, for the shutdown flush.
_scopes: weakref.WeakSet[OpenMeter] = weakref.WeakSet()


@dataclass(frozen=True)
class UsageScope:
    """Whose usage a scope's model calls are, and what they were for."""

    # Whose account: whoever runs it; the author for a trigger key or a schedule.
    user_id: str | None
    # The organization of the agent or workflow it runs through, if any.
    org_id: str | None
    # ``conversation`` / ``workflow_run`` / ``eval_run`` / ``endpoint``.
    source_type: str
    source_id: str | None
    # The entry point: ``chat``, ``dag``, ``auto``, a run mode, ``ai_assist.<name>``...
    entry: str
    agent_id: str | None = None
    workflow_id: str | None = None


def scope_from_context(
    ctx: CallContext,
    *,
    source_type: str,
    source_id: str | None,
    entry: str,
    workflow_id: str | None = None,
) -> UsageScope:
    """The scope of a use made under *ctx*: its principal pays, its binding's
    organization is the one it ran through."""
    binding = ctx.binding
    return UsageScope(
        user_id=ctx.principal.user_id,
        org_id=binding.org_id if binding is not None else None,
        source_type=source_type,
        source_id=source_id,
        entry=entry,
        agent_id=ctx.agent_id,
        workflow_id=workflow_id
        or (binding.id if binding is not None and binding.kind == "workflow" else None),
    )


def _entries(scope: UsageScope, items: list[ModelUsage]) -> list[UsageEntry]:
    return [
        UsageEntry(
            user_id=scope.user_id,
            org_id=scope.org_id,
            source_type=scope.source_type,
            source_id=scope.source_id,
            entry=scope.entry,
            agent_id=scope.agent_id,
            workflow_id=scope.workflow_id,
            model=item.model,
            prompt_tokens=item.prompt_tokens,
            completion_tokens=item.completion_tokens,
            cache_read_tokens=item.cache_read_tokens,
            cache_creation_tokens=item.cache_creation_tokens,
            total_tokens=item.total_tokens,
            estimated=item.estimated,
        )
        for item in items
    ]


class OpenMeter:
    """An open metering scope: the meter model calls report to, and its way to the ledger."""

    def __init__(self, scope: UsageScope | None = None) -> None:
        self.meter = UsageMeter()
        self._scope = scope
        self._token: contextvars.Token[UsageMeter | None] | None = None
        self._closed = False
        # A background write is on its way (set from whichever thread reports).
        self._armed = False
        self._timer: asyncio.TimerHandle | None = None
        try:
            self._loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self.meter.on_add = self._usage_arrived
        _scopes.add(self)

    @property
    def scope(self) -> UsageScope | None:
        return self._scope

    def bind(self, scope: UsageScope) -> None:
        """Say whose scope this is, once that is known (before any model call)."""
        self._scope = scope

    def total(self) -> int:
        """Tokens consumed in this scope so far, written or not."""
        return self.meter.total()

    def enter(self) -> OpenMeter:
        """Make this the scope model calls report to, from here on in this context."""
        self._token = current_meter.set(self.meter)
        return self

    def leave(self) -> None:
        """Hand reporting back to the enclosing scope (same context as :meth:`enter`)."""
        token, self._token = self._token, None
        if token is None:
            return
        try:
            current_meter.reset(token)
        except ValueError:
            # Entered in another context (a response's generator): there is
            # nothing to restore here, that context ends with its task.
            pass

    def _drain(self) -> list[UsageEntry]:
        timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()
        self._armed = False
        items = self.meter.drain()
        if not items:
            return []
        if self._scope is None:
            # A model was called before anyone said whose scope this is.
            # Recorded with no user so that the total is not lost.
            logger.error("Usage metered in a scope that was never bound to a user")
            unbound = UsageScope(
                user_id=None, org_id=None, source_type="endpoint", source_id=None, entry="unbound"
            )
            return _entries(unbound, items)
        return _entries(self._scope, items)

    async def flush(self) -> None:
        """Write what was consumed since the last flush. Never raises.

        The write is shielded: if the caller is cancelled while it runs
        (a client that went away), the rows are still stored.
        """
        entries = self._drain()
        if not entries:
            return
        task = _spawn_write(entries)
        if task is None:
            await write_entries(entries)
            return
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("Usage flush failed", exc_info=True)

    def flush_soon(self) -> None:
        """:meth:`flush` without waiting: the write runs in the background."""
        entries = self._drain()
        if entries and _spawn_write(entries) is None:
            logger.error(
                "Usage not recorded (no event loop): %s",
                [(e.user_id, e.source_type, e.source_id, e.model, e.total_tokens) for e in entries],
            )

    async def close(self) -> None:
        """The scope is over: write what is left.

        A model call that reports later still (its stream was abandoned and
        is closed by the event loop afterwards, or a background task of the
        scope is still running) is written :data:`LATE_FLUSH_DELAY` seconds
        after it reports.
        """
        self._closed = True
        await self.flush()

    # -- writing without being asked -------------------------------------------

    def _usage_arrived(self) -> None:
        """Usage was reported (from any thread): see that it gets written."""
        if self._armed:
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        self._armed = True
        try:
            loop.call_soon_threadsafe(self._arm)
        except RuntimeError:
            self._armed = False

    def _arm(self) -> None:
        if self._timer is not None or self._loop is None or not self._armed:
            return
        delay = LATE_FLUSH_DELAY if self._closed else CHECKPOINT_INTERVAL
        self._timer = self._loop.call_later(delay, self._write_now)

    def _write_now(self) -> None:
        self._timer = None
        self.flush_soon()


def _spawn_write(entries: list[UsageEntry]) -> asyncio.Task[bool] | None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(write_entries(entries))
    _writes.add(task)
    task.add_done_callback(_writes.discard)
    return task


async def wait_for_writes(timeout: float | None = 10.0) -> None:
    """Wait for the background writes in flight (shutdown, and tests)."""
    pending = [task for task in _writes if not task.done()]
    if pending:
        await asyncio.wait(pending, timeout=timeout)


async def flush_all(timeout: float | None = 10.0) -> None:
    """Write what every scope still holds, and wait for it: the last thing
    before the database goes away on shutdown."""
    for scope in list(_scopes):
        scope.flush_soon()
    await wait_for_writes(timeout)


def open_meter(scope: UsageScope | None = None) -> OpenMeter:
    """Open a scope and make it the one model calls report to.

    The caller ends it with ``await opened.close()`` in a ``finally``. The
    context is not handed back to an enclosing scope: use this where the
    scope lasts as long as its task or request (a streamed response, a
    request's dependency), and :func:`metered` for a block.
    """
    return OpenMeter(scope).enter()


@asynccontextmanager
async def metered(scope: UsageScope) -> AsyncIterator[OpenMeter]:
    """A metering scope for a block; written when the block ends, however it ends."""
    opened = OpenMeter(scope).enter()
    try:
        yield opened
    finally:
        opened.leave()
        await opened.close()

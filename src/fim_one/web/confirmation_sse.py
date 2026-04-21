"""In-process bridge between ``FeishuGateHook`` (core) and the chat SSE stream.

The core ``FeishuGateHook`` handles the "inline" confirmation path by
committing a ``ConfirmationRequest(mode='inline')`` row and then calling
:func:`fim_one.core.hooks.emit_inline_confirmation`, which fires whatever
listener was registered via ``set_inline_confirmation_listener``.

This module provides that listener for the FastAPI web layer: it parks each
fresh confirmation on an in-memory ``asyncio.Queue`` keyed by
``(agent_id, user_id)``.  The chat SSE generator then calls
:func:`drain_confirmations` to pull pending confirmations out of the queue
and emit them as ``awaiting_confirmation`` SSE events — mid-stream, while
the agent turn is still suspended inside the gate hook's DB poll.

Design notes
------------

* **Single-process only.**  This bridge lives entirely in RAM; a second
  uvicorn worker will not see a confirmation created on its sibling.  The
  hook's DB-poll fallback keeps things correct — the SSE push is an
  optimisation, not the source of truth.
* **Non-blocking.**  The listener uses ``Queue.put_nowait``.  If a queue
  is saturated (per-user backlog > ``_MAX_QUEUE_SIZE``), the oldest entry
  is dropped.  The hook MUST return fast — we cannot afford to block the
  agent task on frontend liveness.
* **Frozen event contract.**  The dict produced by
  :func:`_request_to_event_payload` is the wire shape consumed by the
  frontend.  Do not add / remove / rename keys without coordinating a
  frontend release.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any, TYPE_CHECKING

from fim_one.core.hooks import set_inline_confirmation_listener
from fim_one.core.hooks.feishu_gate_hook import (
    DEFAULT_TIMEOUT_SECONDS as _HOOK_DEFAULT_TIMEOUT_SECONDS,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fim_one.web.models.channel import ConfirmationRequest


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------


_QueueKey = tuple[str, str]
"""Key = (agent_id, user_id).  Both MUST be non-empty strings."""

# Per-key queue of event payload dicts.  ``defaultdict`` is safe even under
# concurrent ``__getitem__`` — the cost of the occasional extra empty Queue
# in a race is negligible.
_queues: dict[_QueueKey, asyncio.Queue[dict[str, Any]]] = defaultdict(
    lambda: asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)
)
_queues_lock = asyncio.Lock()

# A saturated queue almost certainly indicates a misbehaving frontend (the
# SSE stream is closed but the hook keeps firing).  Drop the oldest entry
# to keep memory bounded; the DB poll fallback will still catch up.
_MAX_QUEUE_SIZE = 64


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def queue_for(agent_id: str, user_id: str) -> asyncio.Queue[dict[str, Any]]:
    """Return the shared queue for ``(agent_id, user_id)``.

    Creates an empty queue on first access.  Safe to call from both the
    listener (producer) and the SSE generator (consumer) — there is no
    race because ``defaultdict`` serialises ``__getitem__`` under the GIL.
    """
    return _queues[(agent_id, user_id)]


def _request_to_event_payload(request: "ConfirmationRequest") -> dict[str, Any]:
    """Convert an ORM ``ConfirmationRequest`` to the FROZEN SSE event dict.

    Wire shape (MUST match frontend expectations — do not change without
    a coordinated release):

    .. code-block:: json

        {
          "type": "awaiting_confirmation",
          "confirmation_id": "<uuid>",
          "tool_name": "<string>",
          "arguments": <object>,
          "timeout_at": "<ISO8601 UTC>",
          "agent_id": "<uuid>"
        }
    """
    payload = request.payload if isinstance(request.payload, dict) else {}
    tool_name = str(payload.get("tool_name") or "")
    arguments = payload.get("tool_args") or {}
    if not isinstance(arguments, dict):
        # Defensive: tool_args might be any JSON-serialisable value in
        # older rows.  Keep the event contract dict-typed either way.
        arguments = {"value": arguments}

    # ``created_at`` is a TimestampMixin column; fall back to utcnow() so
    # the event is always well-formed even if we get a detached instance.
    created = getattr(request, "created_at", None) or datetime.now(timezone.utc)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    timeout_at = created + timedelta(seconds=_HOOK_DEFAULT_TIMEOUT_SECONDS)

    return {
        "type": "awaiting_confirmation",
        "confirmation_id": str(request.id),
        "tool_name": tool_name,
        "arguments": arguments,
        "timeout_at": timeout_at.astimezone(timezone.utc).isoformat(),
        "agent_id": str(request.agent_id or ""),
    }


async def _listener(request: "ConfirmationRequest") -> None:
    """Inline-confirmation listener — pushes the event onto the right queue.

    The hook ignores our return value and swallows exceptions.  We still
    catch defensively so a transient bug here never blocks a tool call.
    """
    agent_id = str(request.agent_id or "")
    user_id = str(request.user_id or "")
    if not agent_id or not user_id:
        # No addressable SSE stream — the DB poll fallback covers this.
        logger.debug(
            "confirmation_sse: skipping dispatch — missing agent_id or "
            "user_id on request %s",
            getattr(request, "id", "<unknown>"),
        )
        return

    try:
        event = _request_to_event_payload(request)
    except Exception:  # pragma: no cover - defensive
        logger.exception(
            "confirmation_sse: failed to serialise confirmation %s",
            getattr(request, "id", "<unknown>"),
        )
        return

    q = queue_for(agent_id, user_id)
    try:
        q.put_nowait(event)
    except asyncio.QueueFull:
        # Drop-oldest policy keeps memory bounded and favours freshness.
        try:
            _ = q.get_nowait()
        except asyncio.QueueEmpty:  # pragma: no cover - defensive
            pass
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:  # pragma: no cover - defensive
            logger.warning(
                "confirmation_sse: queue for (%s, %s) stuck full; "
                "dropping confirmation %s",
                agent_id,
                user_id,
                getattr(request, "id", "<unknown>"),
            )


def register_confirmation_bridge() -> None:
    """Install :func:`_listener` as THE inline-confirmation listener.

    Called exactly once at FastAPI startup.  Safe to call again in tests —
    it simply replaces the previous registration.
    """
    set_inline_confirmation_listener(_listener)
    logger.info(
        "confirmation_sse: inline-confirmation listener registered"
    )


def unregister_confirmation_bridge() -> None:
    """Tear down the listener (tests / shutdown)."""
    set_inline_confirmation_listener(None)


async def drain_confirmations(
    agent_id: str,
    user_id: str,
) -> AsyncIterator[dict[str, Any]]:
    """Yield every queued confirmation for ``(agent_id, user_id)`` without blocking.

    Intended to be called from the SSE generator on a cadence (e.g. after
    each progress-queue item) so pending ``awaiting_confirmation`` events
    are flushed mid-stream.  Returns immediately when the queue is empty.
    """
    q = queue_for(agent_id, user_id)
    while True:
        try:
            event = q.get_nowait()
        except asyncio.QueueEmpty:
            return
        yield event


async def clear_all_queues() -> None:
    """Test helper: drop every pending event across every key."""
    async with _queues_lock:
        _queues.clear()


__all__ = [
    "register_confirmation_bridge",
    "unregister_confirmation_bridge",
    "drain_confirmations",
    "queue_for",
    "clear_all_queues",
]

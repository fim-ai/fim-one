"""In-app (inline) confirmation listener plumbing.

The core ``FeishuGateHook`` supports two delivery modes:

* ``channel`` — push an Approve/Reject card to a messaging channel
  (currently Feishu only).  This is the v0 behaviour.
* ``inline`` — create a ``ConfirmationRequest`` row with ``mode="inline"``
  and surface it in the frontend (via SSE) so the conversation initiator
  can click Approve / Reject without leaving the portal.

The SSE emission itself lives in the web layer (``fim_one.web.api.chat``)
— the core layer MUST NOT import ``fim_one.web``.  To bridge the two,
the web layer registers an async listener through
:func:`set_inline_confirmation_listener`; the hook calls it after
committing the pending row.

The listener receives a ``ConfirmationRequest`` ORM instance.  It should
return promptly (ideally by pushing onto an ``asyncio.Queue`` keyed by
``(agent_id, user_id)``).  Exceptions raised by the listener are caught
and logged — a misbehaving listener MUST NOT block the tool call.

Only one listener may be registered at a time; registering a new one
replaces the previous.  This keeps the plumbing simple: production has
exactly one SSE fan-out, and tests can install and later clear a
function per test case.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from fim_one.web.models.channel import ConfirmationRequest


logger = logging.getLogger(__name__)


#: Signature of an inline-confirmation listener.  Receives the freshly
#: committed ``ConfirmationRequest`` row (mode="inline").  Must be a
#: coroutine; return value is ignored.
InlineConfirmationListener = Callable[["ConfirmationRequest"], Awaitable[None]]


_inline_confirmation_listener: Optional[InlineConfirmationListener] = None


def set_inline_confirmation_listener(
    fn: InlineConfirmationListener | None,
) -> None:
    """Register (or clear) the inline-confirmation listener.

    Pass ``None`` to clear a previously-registered listener — useful in
    tests that want to assert the fall-through behaviour.
    """
    global _inline_confirmation_listener
    _inline_confirmation_listener = fn


def get_inline_confirmation_listener() -> InlineConfirmationListener | None:
    """Return the currently-registered listener, or ``None``."""
    return _inline_confirmation_listener


async def emit_inline_confirmation(
    request: "ConfirmationRequest",
) -> None:
    """Fire the listener if one is registered.  Never raises.

    Called by :class:`fim_one.core.hooks.feishu_gate_hook.FeishuGateHook`
    right after committing a ``mode="inline"`` ``ConfirmationRequest``.
    """
    listener = _inline_confirmation_listener
    if listener is None:
        logger.debug(
            "No inline-confirmation listener registered; "
            "request %s will wait on DB polling only.",
            getattr(request, "id", "<unknown>"),
        )
        return

    try:
        await listener(request)
    except Exception:  # pragma: no cover - defensive
        logger.exception(
            "Inline-confirmation listener raised for request %s — "
            "continuing; the DB-poll fallback still works.",
            getattr(request, "id", "<unknown>"),
        )


__all__ = [
    "InlineConfirmationListener",
    "set_inline_confirmation_listener",
    "get_inline_confirmation_listener",
    "emit_inline_confirmation",
]

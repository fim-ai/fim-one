"""Which secrets an MCP connection may be opened with.

An MCP server row carries the owner's ``env`` (read by a stdio server) and
``headers`` (sent to an SSE / streamable-HTTP server).  A user who is not the
owner may store a personal credential with either field.  When the owner has
switched ``allow_fallback`` off, such a user may connect only with what they
supplied themselves, so the personal credential has to cover the field the
server's transport reads: an ``env``-only credential on an HTTP server
supplies nothing, and treating it as "has a credential" would send the
owner's headers.

Used by chat (``web/api/chat.py``) and the workflow MCP node
(``core/workflow/nodes.py``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["must_bring_own_credential", "personal_credential_fits"]

_HEADER_TRANSPORTS = frozenset({"sse", "streamable_http"})


def personal_credential_fits(
    transport: str | None,
    env: Mapping[str, Any] | None,
    headers: Mapping[str, Any] | None,
) -> bool:
    """Whether a personal credential supplies what *transport* reads.

    stdio reads ``env``; SSE and streamable HTTP read ``headers``.  Any other
    transport cannot be connected, so nothing fits it.
    """
    if transport == "stdio":
        return bool(env)
    if transport in _HEADER_TRANSPORTS:
        return bool(headers)
    return False


def must_bring_own_credential(server: Any, user_id: str | None) -> bool:
    """Whether *user_id* may connect to *server* only with their own credential.

    True when the owner has switched ``allow_fallback`` off and the caller is
    not the owner.  A caller with no identity is not the owner.
    """
    if getattr(server, "allow_fallback", True):
        return False
    return not user_id or getattr(server, "user_id", None) != user_id

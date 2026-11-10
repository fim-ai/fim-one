"""Sets where a request came from, for the audit writer.

A plain ASGI middleware: it reads the client address, the ``User-Agent``
header and makes a request id, puts them in
:data:`fim_one.core.audit.context.request_meta`, and calls on. An endpoint,
a dependency or another middleware that records an audit event gets the
address without being handed the ``Request``.

The address is the ASGI ``client``: behind the reverse proxy that is the
proxy's view of the caller, which uvicorn fills from the forwarded headers
of the proxies it trusts (``FORWARDED_ALLOW_IPS``). It is the same value
the IP rule gate and the sign-in record read.
"""

from __future__ import annotations

import uuid
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

from fim_one.core.audit.context import request_auth, request_meta
from fim_one.core.audit.events import RequestMeta

__all__ = ["RequestMetaMiddleware", "meta_of_scope"]

_USER_AGENT_LIMIT = 500


def meta_of_scope(scope: Scope) -> RequestMeta:
    """The address, user agent and a fresh request id for an ASGI HTTP scope."""
    client: Any = scope.get("client")
    user_agent: str | None = None
    for name, value in scope.get("headers") or ():
        if name == b"user-agent":
            user_agent = value.decode("latin-1")[:_USER_AGENT_LIMIT] or None
            break
    return RequestMeta(
        ip=str(client[0]) if client else None,
        user_agent=user_agent,
        request_id=str(uuid.uuid4()),
    )


class RequestMetaMiddleware:
    """Make the request's origin available to the audit writer."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        meta_token = request_meta.set(meta_of_scope(scope))
        # Nobody is authenticated yet; the authentication dependency says who.
        auth_token = request_auth.set(None)
        try:
            await self.app(scope, receive, send)
        finally:
            request_auth.reset(auth_token)
            request_meta.reset(meta_token)

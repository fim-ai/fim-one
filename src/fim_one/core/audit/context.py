"""Where the current request came from, for the audit writer.

The HTTP layer sets :data:`request_meta` when a request comes in; the
writer reads it. A function that records an event therefore needs no
``Request`` parameter, and an entry that is not an HTTP request (a
schedule, a background sweep) reads ``None``.

:data:`request_auth` is how the caller of the current request was
authenticated, set by the authentication dependency: the method and, for
an API key, the key's id.
"""

from __future__ import annotations

from contextvars import ContextVar

from .events import RequestMeta

__all__ = ["current_request_auth", "current_request_meta", "request_auth", "request_meta"]

request_meta: ContextVar[RequestMeta | None] = ContextVar("audit_request_meta", default=None)
# (auth method, credential reference)
request_auth: ContextVar[tuple[str, str | None] | None] = ContextVar(
    "audit_request_auth", default=None
)


def current_request_meta() -> RequestMeta | None:
    """The request being served, or ``None`` outside one."""
    return request_meta.get()


def current_request_auth() -> tuple[str, str | None] | None:
    """How the current request's caller was authenticated, if it was."""
    return request_auth.get()

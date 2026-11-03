"""Route policy for scoped API keys.

An API key either carries no scopes (``scopes`` is ``NULL``: the key acts with
the owner's full access, as documented) or a comma-separated scope set. A
scoped key may call only the routes its scopes grant below; every route that is
not listed is refused (default deny), so a new endpoint never becomes reachable
by a restricted key by accident. Sessions (JWT logins) are never restricted.

The check runs centrally in :func:`fim_one.web.auth.get_current_user`, the one
dependency every API-key-authenticated route goes through, and in the SSE chat
entry points that authenticate keys themselves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fastapi import Request

from fim_one.web.exceptions import AppError

_ANY = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True)
class _Rule:
    scope: str
    methods: frozenset[str]
    pattern: re.Pattern[str]


def _compile(template: str) -> re.Pattern[str]:
    """``/api/agents/{agent_id}`` → exact match; trailing ``/**`` → subtree."""
    subtree = template.endswith("/**")
    if subtree:
        template = template[: -len("/**")]
    parts = re.split(r"(\{[^/{}]+\})", template)
    body = "".join("[^/]+" if p.startswith("{") else re.escape(p) for p in parts)
    return re.compile(f"^{body}(?:/.*)?$" if subtree else f"^{body}$")


def _rule(scope: str, methods: frozenset[str] | set[str], template: str) -> _Rule:
    return _Rule(scope, frozenset(methods), _compile(template))


_RULES: tuple[_Rule, ...] = (
    # chat — run agents and manage the conversations they produce.
    _rule("chat", {"POST"}, "/api/react"),
    _rule("chat", {"POST"}, "/api/dag"),
    _rule("chat", {"POST"}, "/api/auto"),
    _rule("chat", {"POST"}, "/api/chat/ticket"),
    _rule("chat", {"POST"}, "/api/chat/inject"),
    _rule("chat", {"POST"}, "/api/chat/inject/recall"),
    _rule("chat", {"POST"}, "/api/chat/resume"),
    _rule("chat", {"GET", "POST"}, "/api/conversations"),
    _rule("chat", {"GET", "PATCH", "DELETE"}, "/api/conversations/{conversation_id}"),
    # agents — read-only.
    _rule("agents", {"GET"}, "/api/agents"),
    _rule("agents", {"GET"}, "/api/agents/{agent_id}"),
    # kb — list and retrieve.
    _rule("kb", {"GET"}, "/api/knowledge-bases"),
    _rule("kb", {"POST"}, "/api/knowledge-bases/{kb_id}/retrieve"),
    # connectors — connector management.
    _rule("connectors", _ANY, "/api/connectors/**"),
    # admin — administrative endpoints (the owner must still be an admin).
    _rule("admin", _ANY, "/api/admin/**"),
)


def parse_scopes(raw: str | None) -> set[str] | None:
    """Parse a stored scope string.

    ``None`` or ``""`` means unrestricted. Any other value is a restriction,
    even one that names no usable scope (``","`` grants nothing).
    """
    if not raw:
        return None
    return {s.strip() for s in raw.split(",") if s.strip()}


def required_scope(method: str, path: str) -> str | None:
    """Scope that grants *method* on *path*, or ``None`` if no scope does."""
    method = method.upper()
    if method == "HEAD":
        method = "GET"
    for rule in _RULES:
        if method in rule.methods and rule.pattern.match(path):
            return rule.scope
    return None


def scopes_allow(scopes: set[str] | None, method: str, path: str) -> bool:
    """Whether a key with *scopes* may call *method* *path*."""
    if scopes is None:
        return True
    needed = required_scope(method, path)
    return needed is not None and needed in scopes


def _route_path(request: Request) -> str:
    path: str = request.scope.get("path", "") or ""
    root: str = request.scope.get("root_path", "") or ""
    if root and path.startswith(root):
        path = path[len(root):] or "/"
    return path


def enforce_api_key_scopes(request: Request, scopes: set[str] | None) -> None:
    """Raise 403 unless a key with *scopes* may call this request's route."""
    if scopes is None:
        return
    method = str(request.scope.get("method", "") or "")
    path = _route_path(request)
    if not scopes_allow(scopes, method, path):
        raise AppError(
            "api_key_scope_denied",
            status_code=403,
            detail="This API key's scopes do not allow this request",
        )


def enforce_scope(scopes: set[str] | None, scope: str) -> None:
    """Raise 403 unless a key with *scopes* holds *scope* (``None`` = all)."""
    if scopes is not None and scope not in scopes:
        raise AppError(
            "api_key_scope_denied",
            status_code=403,
            detail="This API key's scopes do not allow this request",
        )


def check_delegation(caller_scopes: set[str] | None, requested: str | None) -> None:
    """A key may only create keys whose scopes are a subset of its own.

    ``requested=None`` means an unrestricted key, so a scoped caller can never
    create one. Sessions and unrestricted keys (``caller_scopes is None``) may
    create any key.
    """
    if caller_scopes is None:
        return
    wanted = parse_scopes(requested)
    if wanted is None or not wanted <= caller_scopes:
        raise AppError(
            "api_key_scope_escalation",
            status_code=403,
            detail="An API key cannot create a key with broader scopes than its own",
        )

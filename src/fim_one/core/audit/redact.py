"""What of a change may be written into an audit record.

:func:`diff_changes` is the only place that turns a before and an after
into the ``changes`` of an event. It keeps secrets out by construction:

- a field listed as secret, or whose name says it is one (``api_key``,
  ``webhook_secret``, ``password``; the reading of names is the one the
  MCP address rule uses), is recorded as ``{"changed": True}`` and nothing
  else;
- a value shaped like a credential, whatever its field is called, is
  recorded as ``***`` (a bare UUID is an id, and is kept);
- a URL loses its user-info, query string, fragment and token-shaped path
  segments (:func:`fim_one.core.assembly.mcp_address.strip_url_secrets`,
  the stripper call records and logs already go through);
- a nested structure is never copied: it is recorded as changed.

:func:`hint` is what may be shown of a code or a key: its first characters.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Mapping
from typing import Any

__all__ = ["CHANGED", "diff_changes", "hint", "safe_path", "safe_text"]

# What is recorded for a secret that was set, replaced or removed.
CHANGED: dict[str, Any] = {"changed": True}

_MAX_TEXT = 200
_MAX_ITEMS = 50


def hint(secret: str | None, *, keep: int = 4) -> str | None:
    """The first *keep* characters of a code or key, enough to tell which one it was."""
    if not secret:
        return None
    return f"{secret[:keep]}…"


def safe_path(path: str | None, *, limit: int = _MAX_TEXT) -> str | None:
    """A request path (no scheme, no host) with its query string and token-shaped segments cut.

    For a record's detail: never quote a raw path there, it carries a
    credential in the same places a URL does.
    """
    if path is None:
        return None
    from fim_one.core.assembly.mcp_address import strip_path_secrets

    stripped = strip_path_secrets(str(path)) or ""
    return stripped if len(stripped) <= limit else stripped[: limit - 1] + "…"


def safe_text(text: str | None, *, limit: int = _MAX_TEXT) -> str | None:
    """*text* with URL secrets cut out and no longer than *limit*.

    Text that is one path (``/v1/send?api_key=…``) is treated as the path
    of a URL.
    """
    if text is None:
        return None
    from fim_one.core.assembly.mcp_address import strip_url_secrets

    if str(text).startswith("/") and not any(ch.isspace() for ch in str(text)):
        return safe_path(text, limit=limit)
    stripped = strip_url_secrets(str(text)) or ""
    return stripped if len(stripped) <= limit else stripped[: limit - 1] + "…"


def _is_identifier(value: str) -> bool:
    """Whether *value* is a bare UUID: how rows are named here, not a credential.

    The shape rule takes any UUID for a possible token. In a change record
    a bare UUID is the id of a bound resource, a channel or a user, and the
    record is of no use without it. A UUID inside a URL is still cut.
    """
    try:
        return str(uuid.UUID(value)) == value.lower() and len(value) == 36
    except ValueError:
        return False


def _printable(value: Any) -> Any:
    """*value* as it may be stored: a scalar, a short list of scalars, or ``***``."""
    from fim_one.core.assembly.mcp_address import value_is_secret_shaped

    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        if _is_identifier(value):
            return value
        if value.startswith("/") or "://" in value:
            # A path or a URL: where it points stays, what authenticates goes.
            return safe_text(value)
        if value_is_secret_shaped(value):
            return "***"
        return safe_text(value)
    if isinstance(value, list | tuple | set | frozenset):
        items = list(value)
        if all(item is None or isinstance(item, str | bool | int | float) for item in items):
            shown = [_printable(item) for item in items[:_MAX_ITEMS]]
            if isinstance(value, set | frozenset):
                shown.sort(key=str)
            return shown
    # A mapping, a list of mappings, an object: never copied.
    return "(changed)"


def diff_changes(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    *,
    secret_fields: Collection[str] = (),
    fields: Collection[str] | None = None,
) -> dict[str, dict[str, Any]] | None:
    """The fields that differ between *before* and *after*, safe to store.

    ``{"role": {"from": "member", "to": "admin"}}``; a secret field is
    ``{"changed": True}``. *fields* limits the comparison to those names
    (default: every key of either side). ``None`` when nothing differs.
    A missing side counts as every field being ``None`` there.
    """
    from fim_one.core.assembly.mcp_address import names_a_secret

    old = before or {}
    new = after or {}
    names = fields if fields is not None else [*old, *(k for k in new if k not in old)]
    changes: dict[str, dict[str, Any]] = {}
    for name in names:
        was, now = old.get(name), new.get(name)
        if was == now:
            continue
        shown_was, shown_now = _printable(was), _printable(now)
        if name in secret_fields or names_a_secret(name) or shown_was == shown_now:
            # A secret, or two values that differ only in what was cut from
            # them (the token in a URL): it changed, and that is all.
            changes[name] = dict(CHANGED)
        else:
            changes[name] = {"from": shown_was, "to": shown_now}
    return changes or None

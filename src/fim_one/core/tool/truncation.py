"""Shared response-truncation utilities for all tool types.

Every tool adapter and built-in tool delegates truncation to this module so
that oversized responses are handled consistently across the entire tool layer.

Two main entry points:

* :func:`truncate_tool_output` — character-level, JSON-aware truncation for
  structured API responses (used by ConnectorToolAdapter, MCPToolAdapter,
  HttpRequestTool, WebFetchTool).
* :func:`truncate_bytes` — byte-level truncation for raw command/script
  output where character semantics matter less than memory safety (used by
  ShellExecTool, PythonExecTool, NodeExecTool).

Truncation strategy (truncate_tool_output)
------------------------------------------
- **JSON array** (too many items): keep first *max_items* complete entries and
  append a hint showing total count and available keys so the agent knows what
  was omitted and can refine its query parameters.
- **JSON array** (few items, but raw text too large): character-based truncation
  with a key-list hint.
- **JSON object**: character-based truncation with top-level key list.
- **Non-JSON**: plain character truncation.
"""

from __future__ import annotations

import json
import os
from typing import Any

# Module-level defaults, overridable via environment variables.
_DEFAULT_MAX_CHARS = int(os.environ.get("TOOL_OUTPUT_MAX_CHARS", "50000"))
_DEFAULT_MAX_ITEMS = int(os.environ.get("TOOL_OUTPUT_MAX_ITEMS", "10"))
_DEFAULT_MAX_BYTES = int(os.environ.get("TOOL_OUTPUT_MAX_BYTES", str(100 * 1024)))


def truncate_tool_output(
    content: str,
    max_chars: int = _DEFAULT_MAX_CHARS,
    max_items: int = _DEFAULT_MAX_ITEMS,
) -> str:
    """Truncate *content* with JSON-aware structure hints.

    Parameters
    ----------
    content:
        Raw string output from a tool call.
    max_chars:
        Maximum number of characters to return for non-array or large-item
        responses.
    max_items:
        Maximum number of array items to include when the response is a JSON
        array.

    Returns
    -------
    str
        Possibly-truncated content with an appended hint describing what was
        omitted and the data structure, so the agent can act on the hint.
    """
    # Fast path: short enough to return as-is (still check array item count).
    if len(content) <= max_chars:
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return content
        if isinstance(data, list) and len(data) > max_items:
            return _truncate_array(data, max_items)
        return content

    # Content exceeds max_chars — parse to add a structure hint.
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return content[:max_chars] + f"\n\n[Truncated: {len(content)} chars total]"

    if isinstance(data, list):
        if len(data) > max_items:
            return _truncate_array(data, max_items)
        # Small array but items are large — char-truncate with key hint.
        keys = _item_keys(data[0]) if data else []
        hint = f" Item keys: {keys}." if keys else ""
        return content[:max_chars] + f"\n\n[Truncated: {len(content)} chars total.{hint}]"

    if isinstance(data, dict):
        keys = list(data.keys())
        return (
            content[:max_chars]
            + f"\n\n[Truncated: {len(content)} chars total. Top-level keys: {keys}]"
        )

    return content[:max_chars] + f"\n\n[Truncated: {len(content)} chars total]"


def _truncate_array(data: list[Any], max_items: int) -> str:
    sample = data[0] if data else {}
    keys = _item_keys(sample)
    truncated = json.dumps(data[:max_items], ensure_ascii=False, indent=2)
    key_hint = f"Each item has keys: {keys}. " if keys else ""
    return (
        truncated
        + f"\n\n[Showing {max_items}/{len(data)} items. "
        + key_hint
        + "Use more specific query parameters to narrow results.]"
    )


def _item_keys(item: object) -> list[str]:
    return list(item.keys()) if isinstance(item, dict) else []


def truncate_bytes(
    text: str,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> str:
    """Truncate *text* if its UTF-8 encoding exceeds *max_bytes*.

    Unlike :func:`truncate_tool_output` which works at the character level
    with JSON-awareness, this function operates at the byte level and is
    intended for raw command/script output where character semantics are
    less important than memory safety.

    Parameters
    ----------
    text:
        Raw text output to check.
    max_bytes:
        Maximum number of UTF-8 bytes to allow.

    Returns
    -------
    str
        The original text (if within limit) or a truncated version with a
        trailing hint.
    """
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    truncated = encoded[:max_bytes].decode("utf-8", errors="replace")
    return (
        truncated
        + f"\n\n[Output truncated — exceeded {max_bytes // 1024} KB limit]"
    )

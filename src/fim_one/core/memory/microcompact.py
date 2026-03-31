"""Rule-based cleanup of old tool results in conversation history.

Replaces the content of older tool result messages with a short placeholder
to reduce context window usage.  This is a lightweight, deterministic
pre-pass that runs *before* the heavier :class:`ContextGuard` compaction.

Two tool-result patterns are recognised:

* **Native mode**: ``role="tool"`` messages (have a ``tool_call_id``).
* **JSON mode**: ``role="user"`` messages whose content starts with
  ``"Observation: "`` (the ReAct observe step).
"""

from __future__ import annotations

import logging
from typing import Any

from fim_one.core.model.types import ChatMessage

logger = logging.getLogger(__name__)

_PLACEHOLDER = "[result cleared -- older than {keep} most recent tool results]"


def _is_tool_result(msg: ChatMessage) -> bool:
    """Return ``True`` if *msg* represents a tool result in either mode."""
    if msg.role == "tool":
        return True
    if msg.role == "user":
        content = msg.content
        if isinstance(content, str) and content.startswith("Observation: "):
            return True
    return False


def micro_compact(
    messages: list[ChatMessage],
    keep_recent: int = 6,
) -> list[ChatMessage]:
    """Replace old tool result content with a short placeholder.

    Scans *messages* for tool-result entries (both native ``role="tool"``
    and JSON-mode ``role="user"`` observations).  The *keep_recent* most
    recent tool results are left intact; older ones have their content
    replaced with a compact placeholder to save context tokens.

    The function returns a **new** list with new :class:`ChatMessage`
    instances for any modified entries; unmodified messages are shared
    with the original list.

    Args:
        messages: Conversation history (oldest first).
        keep_recent: Number of most-recent tool results to preserve.

    Returns:
        A (possibly modified) copy of *messages*.
    """
    if keep_recent < 0:
        keep_recent = 0

    # 1. Collect indices of all tool-result messages.
    tool_indices: list[int] = [
        i for i, msg in enumerate(messages) if _is_tool_result(msg)
    ]

    if len(tool_indices) <= keep_recent:
        # Nothing to compact.
        return list(messages)

    # Indices to clear: everything except the last *keep_recent*.
    indices_to_clear = set(tool_indices[: len(tool_indices) - keep_recent])
    placeholder = _PLACEHOLDER.format(keep=keep_recent)

    result: list[ChatMessage] = []
    cleared = 0
    for i, msg in enumerate(messages):
        if i in indices_to_clear:
            result.append(ChatMessage(
                role=msg.role,
                content=placeholder,
                tool_call_id=msg.tool_call_id,
                tool_calls=msg.tool_calls,
                name=msg.name,
                pinned=msg.pinned,
            ))
            cleared += 1
        else:
            result.append(msg)

    if cleared:
        logger.debug(
            "micro_compact: cleared %d / %d tool results (kept %d recent)",
            cleared, len(tool_indices), keep_recent,
        )

    return result

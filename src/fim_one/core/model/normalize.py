"""Message normalization for LLM calls.

Collapses consecutive same-role messages into a single message before the
list is sent to the provider.

Rationale
---------
The Anthropic Claude Messages API strictly enforces alternating user /
assistant turns; a request with two user messages in a row returns HTTP
400. OpenAI is more lenient but still produces lower-quality responses
when fed consecutive same-role turns.

FIM One's conversation store persists every user message — including
"orphan" user messages from a turn that was stopped or interrupted before
an assistant response landed. When the user retries, the DB-loaded
history therefore contains several consecutive user rows. Without this
normalization step the retry flow either 400s on Claude or produces
degraded output on other providers.

Design choices
--------------
* System / user / assistant plain-text messages are merged with a
  ``\\n\\n`` separator.
* Multi-modal ``list[dict]`` content is merged by list concatenation.
* ``role="tool"`` messages are never merged — each tool response is
  paired to a distinct ``tool_call_id``.
* Assistant messages that carry ``tool_calls`` or ``tool_call_id`` are
  never merged — they represent a discrete agent step.
* ``cache_control`` and ``signature`` from the tail-most merged message
  win, so the Anthropic prompt-cache breakpoint and the extended-thinking
  signature remain attached to the final merged content (which is what
  the provider will see).
"""

from __future__ import annotations

__fim_license__ = "FIM-SAL-1.1"
__fim_origin__ = "https://github.com/fim-ai/fim-one"

from typing import Any

from .types import ChatMessage


def normalize_alternating_messages(
    messages: list[ChatMessage],
) -> list[ChatMessage]:
    """Collapse consecutive same-role messages into one.

    Args:
        messages: The raw message list as constructed by the caller.

    Returns:
        A new list where runs of mergeable same-role messages have been
        collapsed. The input list is not mutated.
    """
    if not messages:
        return list(messages)

    out: list[ChatMessage] = []
    for msg in messages:
        if not out:
            out.append(msg)
            continue
        prev = out[-1]
        if _can_merge(prev, msg):
            out[-1] = _merge(prev, msg)
        else:
            out.append(msg)
    return out


def _can_merge(a: ChatMessage, b: ChatMessage) -> bool:
    if a.role != b.role:
        return False
    # Tool responses must stay 1-to-1 with their tool_call_id.
    if a.role == "tool" or b.role == "tool":
        return False
    # Never merge messages that carry tool-call structure — each is a
    # discrete agent action.
    if a.tool_calls or b.tool_calls:
        return False
    if a.tool_call_id or b.tool_call_id:
        return False
    return True


def _merge(a: ChatMessage, b: ChatMessage) -> ChatMessage:
    return ChatMessage(
        role=a.role,
        content=_merge_content(a.content, b.content),
        # Tail wins: the prompt-cache breakpoint and the extended-thinking
        # signature must attach to the last message of the merged run,
        # which is what the provider actually consumes.
        cache_control=b.cache_control or a.cache_control,
        signature=b.signature or a.signature,
        # Tail wins here too, and deliberately without concatenation:
        # reasoning items are ordered and each belongs to the turn that
        # produced it.  Splicing two turns' items together would present
        # the provider with a sequence it never emitted.
        reasoning_items=b.reasoning_items or a.reasoning_items,
        pinned=a.pinned or b.pinned,
        name=b.name or a.name,
        reasoning_content=_join_optional_str(
            a.reasoning_content, b.reasoning_content
        ),
        tool_call_id=None,
        tool_calls=None,
    )


def _merge_content(
    a: str | list[dict[str, Any]] | None,
    b: str | list[dict[str, Any]] | None,
) -> str | list[dict[str, Any]] | None:
    if a is None:
        return b
    if b is None:
        return a
    if isinstance(a, str) and isinstance(b, str):
        return _join_str(a, b)
    # At least one side is a content array (multi-modal). Normalize both
    # to list form and concatenate.
    a_list: list[dict[str, Any]] = (
        a if isinstance(a, list) else [{"type": "text", "text": a}]
    )
    b_list: list[dict[str, Any]] = (
        b if isinstance(b, list) else [{"type": "text", "text": b}]
    )
    return a_list + b_list


def _join_str(a: str, b: str) -> str:
    if a and b:
        return f"{a}\n\n{b}"
    return a or b


def _join_optional_str(a: str | None, b: str | None) -> str | None:
    if a and b:
        return f"{a}\n\n{b}"
    return a or b or None


__all__ = ["normalize_alternating_messages"]

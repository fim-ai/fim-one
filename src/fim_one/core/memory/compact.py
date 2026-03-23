"""Smart truncation utilities for conversation history compaction.

Provides token estimation and message truncation so that long conversation
histories fit within a configurable token budget.  Supports both a fast
heuristic mode (``smart_truncate``) and an LLM-powered mode
(``llm_compact``) that summarises old turns to preserve semantic context.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fim_one.core.model.types import ChatMessage

if TYPE_CHECKING:
    from fim_one.core.model import BaseLLM
    from fim_one.core.model.usage import UsageTracker

logger = logging.getLogger(__name__)

_COMPACT_PROMPT = """\
Summarise the following conversation history into a concise paragraph.
Preserve key facts, decisions, tool results, and any data the user or
assistant referenced.  When images were shared, preserve the assistant's
description of the image content (what was in the image, key visual details).
Drop greetings, filler, and redundant back-and-forth.
Reply with ONLY the summary text — no JSON, no markdown headers.
Write in the same language as the conversation."""


class CompactUtils:
    """Stateless helpers for estimating and truncating conversation history."""

    @staticmethod
    def content_as_text(content: str | list[dict[str, Any]] | None) -> str:
        """Extract plain text from message content (str or vision array).

        For vision content arrays, extracts all text parts and appends
        a descriptive note for each image part.
        """
        if not content:
            return ""
        if isinstance(content, str):
            return content
        # Vision content array: list[dict]
        parts: list[str] = []
        image_count = 0
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif item.get("type") == "image_url":
                image_count += 1
        if image_count:
            parts.append(f"[{image_count} image(s) were attached to this message]")
        return " ".join(parts)

    @staticmethod
    def estimate_tokens(text: str | list[dict[str, Any]]) -> int:
        """Estimate token count for mixed-language text.

        Uses different heuristics depending on character type:
        - ASCII characters (English, code, punctuation): ~4 chars per token
        - CJK / non-ASCII characters (Chinese, Japanese, Korean, etc.):
          ~1.5 chars per token (each CJK char is typically 1-2 tokens)

        Also handles vision content arrays (list of dicts).

        Args:
            text: The string (or vision content list) to estimate.

        Returns:
            Approximate number of tokens.
        """
        if not text:
            return 0

        # Handle vision content arrays
        if isinstance(text, list):
            total = 0
            for part in text:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    total += CompactUtils.estimate_tokens(part.get("text", ""))
                elif part.get("type") == "image_url":
                    total += 765  # approximate token cost for a base64 image
            return max(1, total) if total else 0

        ascii_chars = 0
        non_ascii_chars = 0
        for ch in text:
            if ord(ch) < 128:
                ascii_chars += 1
            else:
                non_ascii_chars += 1

        # ASCII: ~4 chars per token; CJK/non-ASCII: ~1.5 chars per token
        tokens = ascii_chars / 4.0 + non_ascii_chars / 1.5
        return max(1, int(tokens))

    @classmethod
    def estimate_messages_tokens(cls, messages: list[ChatMessage]) -> int:
        """Estimate total token count across multiple messages.

        Each message adds ~4 tokens of overhead (role, delimiters).

        Args:
            messages: The list of messages.

        Returns:
            Approximate total token count.
        """
        total = 0
        for msg in messages:
            total += 4  # per-message overhead
            content = msg.content or ""
            total += cls.estimate_tokens(content)
        return total

    @classmethod
    def smart_truncate(
        cls,
        messages: list[ChatMessage],
        max_tokens: int = 8000,
    ) -> list[ChatMessage]:
        """Truncate messages to fit within a token budget.

        Keeps the most recent messages by scanning backwards from the end.
        Ensures the returned list does not start with an ``assistant`` message
        (which would confuse the LLM).

        Args:
            messages: Full conversation history (oldest first).
            max_tokens: Maximum token budget.

        Returns:
            A suffix of *messages* that fits within *max_tokens*.
        """
        if not messages:
            return []

        if cls.estimate_messages_tokens(messages) <= max_tokens:
            return list(messages)

        # Pinned messages are always kept; deduct their cost first.
        pinned = [m for m in messages if m.pinned]
        non_pinned = [m for m in messages if not m.pinned]

        budget = max_tokens
        for msg in pinned:
            budget -= 4 + cls.estimate_tokens(msg.content or "")

        # Walk backwards through non-pinned, accumulating until budget exhausted.
        recent: list[ChatMessage] = []
        for msg in reversed(non_pinned):
            cost = 4 + cls.estimate_tokens(msg.content or "")
            if budget - cost < 0:
                break
            recent.append(msg)
            budget -= cost

        recent.reverse()

        result = pinned + recent

        # Drop leading assistant messages — the history must start with a
        # user message so the LLM doesn't see a context-free assistant turn.
        while result and result[0].role == "assistant":
            result.pop(0)

        return result

    @classmethod
    async def llm_compact(
        cls,
        messages: list[ChatMessage],
        llm: BaseLLM,
        max_tokens: int = 8000,
        keep_recent: int = 4,
        usage_tracker: UsageTracker | None = None,
    ) -> list[ChatMessage]:
        """Compress conversation history using an LLM summary.

        If the history already fits within *max_tokens*, it is returned
        unchanged.  Otherwise the earliest turns are summarised into a
        single system message while the most recent *keep_recent*
        user/assistant pairs are kept verbatim.

        Args:
            messages: Full conversation history (oldest first).
            llm: A fast LLM to use for summarisation.
            max_tokens: Maximum token budget for the returned history.
            keep_recent: Number of recent messages to preserve verbatim.

        Returns:
            A compacted message list that fits within *max_tokens*.
        """
        if not messages:
            return []

        total = cls.estimate_messages_tokens(messages)
        if total <= max_tokens:
            return list(messages)

        # Three-way split: system / pinned / compactable.
        system_msgs = [m for m in messages if m.role == "system"]
        pinned_msgs = [m for m in messages if m.pinned and m.role != "system"]
        compactable = [m for m in messages if m.role != "system" and not m.pinned]

        if len(compactable) <= keep_recent:
            return cls.smart_truncate(messages, max_tokens)

        old_messages = compactable[:-keep_recent]
        recent_messages = list(compactable[-keep_recent:])

        # Build the text block to summarise.
        lines: list[str] = []
        for msg in old_messages:
            prefix = "User" if msg.role == "user" else "Assistant"
            lines.append(f"{prefix}: {cls.content_as_text(msg.content)}")
        history_text = "\n".join(lines)

        try:
            result = await llm.chat([
                ChatMessage(role="system", content=_COMPACT_PROMPT),
                ChatMessage(role="user", content=history_text),
            ])
            raw_content = result.message.content
            summary = (raw_content if isinstance(raw_content, str) else "").strip()
            if usage_tracker and result.usage:
                await usage_tracker.record(result.usage)
        except Exception:
            logger.warning("LLM compact failed, falling back to truncation", exc_info=True)
            return cls.smart_truncate(messages, max_tokens)

        if not summary:
            return cls.smart_truncate(messages, max_tokens)

        compacted = [
            *system_msgs,
            *pinned_msgs,
            ChatMessage(role="system", content=f"[Conversation summary]: {summary}"),
            *recent_messages,
        ]

        # If the compacted result is still too long, truncate the recent part.
        if cls.estimate_messages_tokens(compacted) > max_tokens:
            return cls.smart_truncate(compacted, max_tokens)

        return compacted

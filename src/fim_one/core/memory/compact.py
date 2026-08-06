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

# Prefix marking a system message that carries compacted conversation
# context rather than agent instructions.  Agents rebuild their own system
# prompt each run and therefore drop system messages loaded from history —
# this marker is how a summary survives that filter, so every producer must
# use it and :func:`is_summary_message` must be the only test for it.
SUMMARY_PREFIX = "[Conversation summary]: "

# Elision notice placed between the kept ends of a head+tail truncation.
TRUNCATION_MARKER = "\n\n... (output truncated) ...\n\n"


def is_summary_message(message: ChatMessage) -> bool:
    """Return ``True`` when *message* carries a compaction summary.

    Used by agent loops to tell carried-over context apart from a stale
    system prompt when replaying history from memory.
    """
    if message.role != "system":
        return False
    content = message.content
    return isinstance(content, str) and content.startswith(SUMMARY_PREFIX)


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

    @staticmethod
    def truncate_to_tokens(text: str, max_tokens: int) -> str:
        """Return the longest prefix of *text* costing at most *max_tokens*.

        The inverse of :meth:`estimate_tokens`.  A plain ``tokens * 4``
        character budget is only correct for ASCII: it hands a CJK string
        roughly 2.7x more characters than the estimator will later charge
        for, so a caller trimming to that budget still overshoots.  This
        walks the string accumulating the same per-character cost the
        estimator uses and cuts where the budget runs out.

        Args:
            text: The string to trim.
            max_tokens: Token budget for the returned prefix.

        Returns:
            A prefix of *text*, or ``""`` when the budget is non-positive.
        """
        if max_tokens <= 0:
            return ""
        if not text:
            return text

        ascii_chars = 0
        non_ascii_chars = 0
        for idx, ch in enumerate(text):
            if ord(ch) < 128:
                ascii_chars += 1
            else:
                non_ascii_chars += 1
            if ascii_chars / 4.0 + non_ascii_chars / 1.5 > max_tokens:
                return text[:idx]
        return text

    @staticmethod
    def _tail_within_tokens(text: str, max_tokens: int) -> str:
        """Return the longest *suffix* of *text* costing at most *max_tokens*."""
        if max_tokens <= 0 or not text:
            return ""
        ascii_chars = 0
        non_ascii_chars = 0
        for idx in range(len(text) - 1, -1, -1):
            if ord(text[idx]) < 128:
                ascii_chars += 1
            else:
                non_ascii_chars += 1
            if ascii_chars / 4.0 + non_ascii_chars / 1.5 > max_tokens:
                return text[idx + 1 :]
        return text

    @classmethod
    def truncate_head_tail(
        cls,
        text: str,
        max_tokens: int,
        marker: str = TRUNCATION_MARKER,
    ) -> str:
        """Trim *text* to *max_tokens*, keeping both ends and eliding the middle.

        Tool output routinely puts its conclusion last — an exit status, a
        final row, the error that explains the run — so a head-only cut
        drops precisely the part the model needs.  Splitting the budget
        between the two ends keeps the setup and the outcome.

        Args:
            text: The string to trim.
            max_tokens: Token budget for the result, marker included.
            marker: Elision notice placed between the kept ends.

        Returns:
            *text* unchanged when it already fits, else ``head + marker + tail``.
        """
        if max_tokens <= 0:
            return ""
        if not text or cls.estimate_tokens(text) <= max_tokens:
            return text

        budget = max_tokens - cls.estimate_tokens(marker)
        if budget <= 0:
            return cls.truncate_to_tokens(text, max_tokens)

        head = cls.truncate_to_tokens(text, budget // 2)
        tail = cls._tail_within_tokens(text, budget - budget // 2)
        # A tiny budget can make the two ends overlap; prefer the head then.
        if len(head) + len(tail) >= len(text):
            return cls.truncate_to_tokens(text, max_tokens)
        return f"{head}{marker}{tail}"

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

        # Orphan tool results at the head of the kept window (their
        # assistant tool_calls turn fell outside the budget) would be
        # rejected with HTTP 400 by OpenAI and Anthropic.  Prune them
        # BEFORE prepending pinned messages — pinned user messages would
        # otherwise mask the orphans from the leading-role check below.
        while recent and recent[0].role == "tool":
            recent.pop(0)

        result = pinned + recent

        # Drop leading assistant messages — the history must start with a
        # user message so the LLM doesn't see a context-free assistant turn.
        # Popping an assistant turn that carried tool_calls exposes its tool
        # results, which the "tool" case then removes as well.
        while result and result[0].role in ("assistant", "tool"):
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

        # Build the text block to summarise.  Include extended-thinking so a
        # clue the model surfaced only in a reasoning block survives the
        # summary instead of being silently dropped (the summariser can only
        # preserve what it is shown).
        lines: list[str] = []
        for msg in old_messages:
            prefix = "User" if msg.role == "user" else "Assistant"
            if msg.reasoning_content:
                lines.append(f"{prefix} (thinking): {msg.reasoning_content}")
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
            ChatMessage(role="system", content=f"{SUMMARY_PREFIX}{summary}"),
            *recent_messages,
        ]

        # If the compacted result is still too long, truncate the recent part.
        if cls.estimate_messages_tokens(compacted) > max_tokens:
            return cls.smart_truncate(compacted, max_tokens)

        return compacted

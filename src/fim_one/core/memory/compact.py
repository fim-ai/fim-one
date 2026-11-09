"""Smart truncation utilities for conversation history compaction.

Provides token estimation and message truncation so that long conversation
histories fit within a configurable token budget.  Supports both a fast
heuristic mode (``smart_truncate``) and an LLM-powered mode
(``llm_compact``) that summarises old turns to preserve semantic context.
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING, Any

from fim_one.core.model.types import ChatMessage

from .budget import llm_input_budget
from .microcompact import _is_tool_result

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

#: Upper bound on summariser calls for one compaction.  The history handed
#: to the summariser is cut into parts that each fit its window; a history
#: needing more parts than this keeps the first part and the most recent
#: ones and drops the middle, so the cost of one compaction stays bounded.
MAX_COMPACT_CHUNKS = max(1, int(os.getenv("CONTEXT_GUARD_MAX_COMPACT_CHUNKS") or "8"))

# Hard cap on the running summary carried from one part to the next.
_CARRY_SUMMARY_MAX_TOKENS = 10_000

# Smallest part worth a summariser call.  A summariser whose window leaves
# less room than this after the prompt cannot make progress; compaction
# then falls back to truncation instead of looping on slivers.
_MIN_CHUNK_TOKENS = 256

# Tokens set aside per request for what is not prompt, carried summary or
# history: the two per-message overheads, the fold labels, the elision
# notice and the rounding slack of the character heuristic.
_REQUEST_OVERHEAD_TOKENS = 64

_FOLD_INSTRUCTION = (
    "\n\nThe conversation is too long to summarise in one request, so it "
    "arrives in consecutive parts. The message below holds the summary of "
    "the earlier parts, then the next part. Reply with ONE updated summary "
    "that covers both, in the format described above. Keep everything from "
    "the earlier summary that still matters."
)
_FOLD_SUMMARY_LABEL = "[Summary of the conversation so far]\n"
_FOLD_CHUNK_LABEL = "\n\n[Next part of the conversation]\n"
_ELISION_NOTICE = (
    "[... {count} earlier part(s) of the conversation omitted: too long to summarise ...]"
)

# Scratchpad block the structured compact prompts ask the summariser for.
_ANALYSIS_RE = re.compile(r"<analysis>.*?</analysis>", re.DOTALL)


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

    @staticmethod
    def split_on_tool_boundary(
        compactable: list[ChatMessage],
        keep_recent: int,
    ) -> tuple[list[ChatMessage], list[ChatMessage]]:
        """Split *compactable* into ``(old, recent)`` without orphaning tools.

        A naive ``[-keep_recent:]`` slice can land between an assistant
        ``tool_calls`` message and its ``role="tool"`` results.  Summarising
        the assistant turn away while keeping the tool results produces a
        conversation that starts with orphan tool messages — OpenAI and
        Anthropic both reject that with HTTP 400.  Walk the boundary left
        until the recent slice no longer starts with a tool message, so the
        tool_use/tool_result pair stays intact on the kept side.
        """
        old = compactable[:-keep_recent]
        recent = compactable[-keep_recent:]
        while recent and recent[0].role == "tool" and old:
            recent.insert(0, old.pop())
        return old, recent

    # ------------------------------------------------------------------
    # Chunked summarisation
    # ------------------------------------------------------------------

    @classmethod
    def _render_units(cls, messages: list[ChatMessage]) -> list[list[str]]:
        """Render *messages* as summariser input lines, grouped into units.

        A unit is what must not be separated when the history is cut into
        parts: a message together with the tool results that answer it
        (native ``role="tool"`` messages and JSON-mode observations).
        Extended thinking is rendered too, so a clue the model surfaced only
        in a reasoning block survives the summary instead of being silently
        dropped (the summariser can only preserve what it is shown).
        """
        units: list[list[str]] = []
        for msg in messages:
            label = msg.role.capitalize()
            lines: list[str] = []
            if msg.reasoning_content:
                lines.append(f"{label} (thinking): {msg.reasoning_content}")
            lines.append(f"{label}: {cls.content_as_text(msg.content)}")
            if units and _is_tool_result(msg):
                units[-1].extend(lines)
            else:
                units.append(lines)
        return units

    @classmethod
    def _line_cost(cls, line: str) -> int:
        """Token cost charged for one rendered line inside a part.

        Two above the estimate: one for the joining newline and one for the
        fraction :meth:`estimate_tokens` rounds away, so the per-line costs
        of a part always add up to at least the estimate of the joined text.
        """
        return cls.estimate_tokens(line) + 2

    @staticmethod
    def _split_line(
        line: str,
        max_tokens: int,
        first_max_tokens: int | None = None,
    ) -> list[str]:
        """Cut *line* into consecutive pieces costing at most *max_tokens* each.

        *first_max_tokens*, when given, sizes the first piece instead, so a
        cut can start by filling the room left in a part.  Uses the same
        per-character cost as :meth:`estimate_tokens`.  Every piece holds at
        least one character, so the walk always advances.
        """
        pieces: list[str] = []
        limit = max_tokens if first_max_tokens is None else first_max_tokens
        start = 0
        ascii_chars = 0
        non_ascii_chars = 0
        for idx, ch in enumerate(line):
            is_ascii = ord(ch) < 128
            ascii_next = ascii_chars + (1 if is_ascii else 0)
            non_ascii_next = non_ascii_chars + (0 if is_ascii else 1)
            if ascii_next / 4.0 + non_ascii_next / 1.5 > limit and idx > start:
                pieces.append(line[start:idx])
                limit = max_tokens
                start = idx
                ascii_chars = 1 if is_ascii else 0
                non_ascii_chars = 0 if is_ascii else 1
            else:
                ascii_chars = ascii_next
                non_ascii_chars = non_ascii_next
        if start < len(line):
            pieces.append(line[start:])
        return pieces

    @classmethod
    def _pack_chunks(cls, units: list[list[str]], chunk_tokens: int) -> list[str]:
        """Pack rendered *units* into parts of at most *chunk_tokens* each.

        Units are kept whole: a tool call is never put in a different part
        from its results.  The one exception is a unit that is larger than
        a whole part on its own — it is laid out across parts line by line,
        and a line that is itself too long is cut into pieces.  The parts
        are plain text for the summariser, so that split only affects what
        the summariser reads side by side, never the message list sent to
        the main model.
        """
        chunks: list[str] = []
        current: list[str] = []
        used = 0
        for unit in units:
            costs = [cls._line_cost(line) for line in unit]
            unit_cost = sum(costs)
            if unit_cost <= chunk_tokens:
                if used + unit_cost > chunk_tokens:
                    chunks.append("\n".join(current))
                    current, used = [], 0
                current.extend(unit)
                used += unit_cost
                continue
            for line, cost in zip(unit, costs, strict=True):
                if cost <= chunk_tokens:
                    pieces = [line]
                else:
                    # Start the cut with the room the current part has left,
                    # unless that room is a sliver.
                    room = chunk_tokens - used
                    pieces = cls._split_line(
                        line,
                        chunk_tokens - 2,
                        first_max_tokens=room - 2 if room >= _MIN_CHUNK_TOKENS else None,
                    )
                for piece in pieces:
                    piece_cost = cls._line_cost(piece)
                    if current and used + piece_cost > chunk_tokens:
                        chunks.append("\n".join(current))
                        current, used = [], 0
                    current.append(piece)
                    used += piece_cost
        if current:
            chunks.append("\n".join(current))
        return chunks

    @staticmethod
    def _cap_chunks(chunks: list[str]) -> list[str]:
        """Bound *chunks* to :data:`MAX_COMPACT_CHUNKS` parts.

        Keeps the first part (it usually carries the original request) and
        the most recent ones; the middle is dropped and replaced by a
        one-line notice so the summariser knows there is a gap.
        """
        if len(chunks) <= MAX_COMPACT_CHUNKS:
            return chunks
        head = chunks[:1] if MAX_COMPACT_CHUNKS > 1 else []
        tail = chunks[len(chunks) - max(MAX_COMPACT_CHUNKS - 1, 1) :]
        omitted = len(chunks) - len(head) - len(tail)
        logger.warning(
            "Compaction input needs %d parts but at most %d are summarised "
            "(CONTEXT_GUARD_MAX_COMPACT_CHUNKS); dropping the %d oldest "
            "parts after the first",
            len(chunks),
            MAX_COMPACT_CHUNKS,
            omitted,
        )
        tail[0] = f"{_ELISION_NOTICE.format(count=omitted)}\n{tail[0]}"
        return head + tail

    @classmethod
    async def summarize_history(
        cls,
        messages: list[ChatMessage],
        llm: BaseLLM,
        prompt: str,
        *,
        input_budget: int,
        usage_tracker: UsageTracker | None = None,
    ) -> str | None:
        """Summarise *messages* without any request exceeding *input_budget*.

        A history that fits the summariser's window goes out as one request.
        A longer one is cut into consecutive parts (see :meth:`_pack_chunks`)
        and folded: the first part is summarised on its own, and each later
        request carries the summary so far plus the next part and asks for
        one updated summary.  The carried summary is capped, so every
        request — prompt, carried summary and part together — stays within
        *input_budget* by the same estimate the rest of context management
        uses.

        Args:
            messages: The turns to summarise (oldest first).
            llm: The summariser.
            prompt: System prompt describing the summary wanted.
            input_budget: Input tokens the summariser can take per request.
            usage_tracker: Records the usage of every summariser call.

        Returns:
            The summary, or ``None`` when it could not be produced (a call
            failed or came back empty, or the prompt leaves no room for
            history in the summariser's window).  Callers fall back to
            :meth:`smart_truncate`.
        """
        units = cls._render_units(messages)
        if not units:
            return None

        whole = "\n".join(line for unit in units for line in unit)
        single_room = input_budget - cls.estimate_tokens(prompt) - _REQUEST_OVERHEAD_TOKENS
        carry_cap = 0
        if cls.estimate_tokens(whole) <= single_room:
            chunks = [whole]
        else:
            carry_cap = min(_CARRY_SUMMARY_MAX_TOKENS, input_budget // 4)
            chunk_tokens = (
                input_budget
                - cls.estimate_tokens(prompt + _FOLD_INSTRUCTION)
                - carry_cap
                - _REQUEST_OVERHEAD_TOKENS
            )
            if chunk_tokens < _MIN_CHUNK_TOKENS:
                logger.warning(
                    "Compaction skipped: the summariser's input budget (%d tokens) "
                    "leaves no room for history after the compact prompt",
                    input_budget,
                )
                return None
            chunks = cls._cap_chunks(cls._pack_chunks(units, chunk_tokens))
            logger.info(
                "Compaction input exceeds the summariser's input budget "
                "(%d tokens); summarising in %d parts",
                input_budget,
                len(chunks),
            )

        summary = ""
        for index, chunk in enumerate(chunks):
            if index == 0:
                request = [
                    ChatMessage(role="system", content=prompt),
                    ChatMessage(role="user", content=chunk),
                ]
            else:
                carried = cls.truncate_head_tail(summary, carry_cap)
                request = [
                    ChatMessage(role="system", content=prompt + _FOLD_INSTRUCTION),
                    ChatMessage(
                        role="user",
                        content=f"{_FOLD_SUMMARY_LABEL}{carried}{_FOLD_CHUNK_LABEL}{chunk}",
                    ),
                ]
            try:
                result = await llm.chat(request)
                raw_content = result.message.content
                if usage_tracker and result.usage:
                    await usage_tracker.record(result.usage)
            except Exception:
                logger.warning(
                    "LLM compact failed on part %d/%d",
                    index + 1,
                    len(chunks),
                    exc_info=True,
                )
                return None
            summary = _ANALYSIS_RE.sub(
                "", raw_content if isinstance(raw_content, str) else ""
            ).strip()
            if not summary:
                return None
        return summary

    @classmethod
    async def llm_compact(
        cls,
        messages: list[ChatMessage],
        llm: BaseLLM,
        max_tokens: int = 8000,
        keep_recent: int = 4,
        usage_tracker: UsageTracker | None = None,
        summary_input_budget: int | None = None,
    ) -> list[ChatMessage]:
        """Compress conversation history using an LLM summary.

        If the history already fits within *max_tokens*, it is returned
        unchanged.  Otherwise the earliest turns are summarised into a
        single system message while the most recent *keep_recent*
        messages are kept verbatim (more when the cut would otherwise land
        between a tool call and its results).

        Args:
            messages: Full conversation history (oldest first).
            llm: A fast LLM to use for summarisation.
            max_tokens: Maximum token budget for the returned history.
            keep_recent: Number of recent messages to preserve verbatim.
            usage_tracker: Records the usage of the summariser calls.
            summary_input_budget: Input tokens *llm* can take per request.
                Defaults to the window *llm* declares, else
                ``CONTEXT_GUARD_DEFAULT_BUDGET``.  Histories larger than
                this are summarised in parts (:meth:`summarize_history`).

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

        old_messages, recent_messages = cls.split_on_tool_boundary(compactable, keep_recent)

        summary = await cls.summarize_history(
            old_messages,
            llm,
            _COMPACT_PROMPT,
            input_budget=summary_input_budget or llm_input_budget(llm),
            usage_tracker=usage_tracker,
        )
        if not summary:
            logger.warning("LLM compact produced no summary, falling back to truncation")
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

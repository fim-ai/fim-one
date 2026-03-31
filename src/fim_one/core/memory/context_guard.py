"""Unified context window budget manager.

Checks message list token count against a budget.  If over budget: LLM
compact (with hint-specific prompt), else :meth:`CompactUtils.smart_truncate`.
Also provides per-message content truncation as a safety net.
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING

from fim_one.core.model.types import ChatMessage

from .compact import CompactUtils

if TYPE_CHECKING:
    from fim_one.core.model import BaseLLM
    from fim_one.core.model.usage import UsageTracker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hint-specific compact prompts
# ---------------------------------------------------------------------------

_COMPACT_PROMPTS: dict[str, str] = {
    "general": (
        "Summarise the following conversation history into a concise paragraph.\n"
        "Preserve key facts, decisions, tool results, and any data the user or "
        "assistant referenced.  When images were shared, preserve the assistant's "
        "description of the image content (what was in the image, key visual details).\n"
        "Drop greetings, filler, and redundant back-and-forth.\n"
        "Reply with ONLY the summary text — no JSON, no markdown headers.\n"
        "Write in the same language as the conversation."
    ),
    "react_iteration": (
        "You are compacting a ReAct agent's conversation history to fit within a "
        "token budget while preserving all information needed to continue the task.\n\n"
        "First, think through the compaction inside <analysis> tags — identify what "
        "is critical to keep, what can be dropped, and how to compress verbose "
        "outputs while retaining their conclusions. Then output the structured "
        "summary OUTSIDE the <analysis> tags.\n\n"
        "Output the summary using these 9 sections. Omit any section that has "
        "no relevant content. Use concise bullet points within each section.\n\n"
        "## 1. Primary Request\n"
        "What the user originally asked for — the root goal driving this session.\n\n"
        "## 2. Key Concepts\n"
        "Domain concepts, terminology, constraints, and configuration values "
        "mentioned. Include descriptions of any images the user shared.\n\n"
        "## 3. Files and Code\n"
        "Files read or modified, code snippets still relevant, function signatures, "
        "schema definitions. Drop code that has been superseded.\n\n"
        "## 4. Errors\n"
        "Error messages, stack traces, and failed approaches that are still "
        "informative. Drop errors from attempts that were successfully retried.\n\n"
        "## 5. Problem Solving\n"
        "Insights gained, hypotheses tested, conclusions reached, and important "
        "tool results. Compress verbose outputs to their key findings.\n\n"
        "## 6. User Messages\n"
        "Important user clarifications, corrections, or preference changes that "
        "affect ongoing work.\n\n"
        "## 7. Pending Tasks\n"
        "Incomplete work, deferred items, and planned next steps not yet started.\n\n"
        "## 8. Current Work\n"
        "What the agent is currently doing — the active sub-goal and progress so far.\n\n"
        "## 9. Next Step\n"
        "The single most important next action the agent should take.\n\n"
        "RULES:\n"
        "- Write in the same language as the conversation.\n"
        "- Be concise — this summary replaces the full history.\n"
        "- Preserve exact numbers, IDs, file paths, and error codes.\n"
        "- Drop greetings, filler, and redundant back-and-forth."
    ),
    "planner_input": (
        "You are compacting conversation history into context for a task planner.\n\n"
        "First, think through the compaction inside <analysis> tags — identify the "
        "user's intent evolution, key decisions, and constraints that affect "
        "planning. Then output the structured summary OUTSIDE the <analysis> tags.\n\n"
        "Output the summary using these sections. Omit any section with no "
        "relevant content. Use concise bullet points.\n\n"
        "## 1. User Intent\n"
        "The user's original request and how it evolved through clarifications.\n\n"
        "## 2. Decisions and Constraints\n"
        "Key decisions made, constraints specified, preferences expressed, and "
        "any trade-offs agreed upon.\n\n"
        "## 3. Gathered Data\n"
        "Final conclusions, data, or results from previous turns that the planner "
        "needs. Include image descriptions if relevant.\n\n"
        "## 4. Open Questions\n"
        "Unresolved ambiguities or decisions that still need to be made.\n\n"
        "## 5. Planning Goal\n"
        "What the planner should produce — the concrete deliverable or action plan.\n\n"
        "RULES:\n"
        "- Write in the same language as the conversation.\n"
        "- Be concise — this summary replaces the full history.\n"
        "- Preserve exact numbers, IDs, file paths, and constraints.\n"
        "- Drop greetings, filler, tool call mechanics, and verbose reasoning."
    ),
    "step_dependency": (
        "You are compacting dependency results for a downstream task step.\n\n"
        "First, think through the compaction inside <analysis> tags — identify "
        "which outputs are actionable for the next step and which are noise. "
        "Then output the structured summary OUTSIDE the <analysis> tags.\n\n"
        "Output the summary using these sections. Omit any section with no "
        "relevant content. Use concise bullet points.\n\n"
        "## 1. Key Outputs\n"
        "Data, numbers, conclusions, and actionable results from upstream steps.\n\n"
        "## 2. Artifacts\n"
        "Files created, schemas defined, IDs generated, or resources provisioned.\n\n"
        "## 3. Constraints for Next Step\n"
        "Conditions, limits, or dependencies the downstream step must respect.\n\n"
        "RULES:\n"
        "- Write in the same language as the content.\n"
        "- Be concise — this summary feeds directly into the next step.\n"
        "- Preserve exact numbers, IDs, file paths, and data values.\n"
        "- Drop reasoning process, failed attempts, and verbose formatting."
    ),
}


class ContextGuard:
    """Unified context window budget manager.

    Checks message list token count against a budget.
    If over budget: LLM compact (if *compact_llm* provided), else smart_truncate.
    Also provides per-message content truncation as a safety net.

    Args:
        compact_llm: Optional fast LLM for summarisation.
        default_budget: Default token budget when none is passed to
            :meth:`check_and_compact`.
        max_message_chars: Maximum character length for any single message
            content.  Messages exceeding this are truncated.
    """

    # Module-level defaults, configurable via environment variables.
    _DEFAULT_BUDGET = int(os.getenv("CONTEXT_GUARD_DEFAULT_BUDGET", "32000"))
    _MAX_MSG_CHARS = int(os.getenv("CONTEXT_GUARD_MAX_MSG_CHARS", "50000"))
    _KEEP_RECENT = int(os.getenv("CONTEXT_GUARD_KEEP_RECENT", "4"))

    def __init__(
        self,
        compact_llm: BaseLLM | None = None,
        default_budget: int = _DEFAULT_BUDGET,
        max_message_chars: int = _MAX_MSG_CHARS,
        usage_tracker: UsageTracker | None = None,
        custom_compact_prompt: str | None = None,
    ) -> None:
        self._compact_llm = compact_llm
        self._default_budget = default_budget
        self.max_message_chars = max_message_chars
        self._usage_tracker = usage_tracker
        self._custom_compact_prompt = custom_compact_prompt

    async def check_and_compact(
        self,
        messages: list[ChatMessage],
        budget: int | None = None,
        hint: str = "general",
    ) -> list[ChatMessage]:
        """Check token count, compact if over budget.

        Args:
            messages: The conversation message list.
            budget: Token budget override (uses *default_budget* if ``None``).
            hint: Controls the compact prompt variant:

                - ``"react_iteration"``: preserve recent reasoning chain,
                  compress old tool outputs.
                - ``"planner_input"``: preserve user intent evolution,
                  compress details.
                - ``"step_dependency"``: preserve key data/conclusions,
                  drop reasoning process.
                - ``"general"``: default compact prompt.

        Returns:
            A (possibly compacted) message list fitting within the budget.
        """
        effective_budget = budget or self._default_budget

        # 1. Truncate any individual oversized messages first.
        messages = self._truncate_oversized(messages)

        # 2. Estimate total tokens.
        total = CompactUtils.estimate_messages_tokens(messages)
        if total <= effective_budget:
            return messages

        logger.info(
            "ContextGuard: %d tokens exceeds budget %d (hint=%s), compacting",
            total, effective_budget, hint,
        )

        # 3. Try LLM compact with hint-specific prompt.
        if self._compact_llm is not None:
            return await self._llm_compact_with_hint(
                messages, effective_budget, hint,
            )

        # 4. Fallback: smart_truncate.
        return CompactUtils.smart_truncate(messages, effective_budget)

    def _truncate_oversized(
        self, messages: list[ChatMessage],
    ) -> list[ChatMessage]:
        """Truncate individual messages exceeding *max_message_chars*.

        Returns a new list (original messages are not mutated).
        """
        result: list[ChatMessage] = []
        limit = self.max_message_chars
        for msg in messages:
            content = msg.content or ""
            # Vision content arrays: skip character-level truncation
            if isinstance(content, list):
                result.append(msg)
                continue
            if len(content) > limit:
                truncated = content[:limit] + "\n[Truncated]"
                result.append(ChatMessage(
                    role=msg.role,
                    content=truncated,
                    tool_call_id=msg.tool_call_id,
                    tool_calls=msg.tool_calls,
                    pinned=msg.pinned,
                ))
            else:
                result.append(msg)
        return result

    async def _llm_compact_with_hint(
        self,
        messages: list[ChatMessage],
        budget: int,
        hint: str,
    ) -> list[ChatMessage]:
        """LLM compact with a hint-specific system prompt.

        Falls back to :meth:`CompactUtils.smart_truncate` on failure.
        """
        prompt = self._custom_compact_prompt if self._custom_compact_prompt else _COMPACT_PROMPTS.get(hint, _COMPACT_PROMPTS["general"])

        # Keep system message(s) and recent messages; summarise old ones.
        # Split: system messages stay, keep last 4 user/assistant messages,
        # summarise the rest.
        system_msgs = [m for m in messages if m.role == "system"]
        pinned_msgs = [m for m in messages if m.pinned and m.role != "system"]
        compactable = [m for m in messages if m.role != "system" and not m.pinned]

        keep_recent = self._KEEP_RECENT
        if len(compactable) <= keep_recent:
            # Not enough to split — fall back to heuristic truncation.
            return CompactUtils.smart_truncate(messages, budget)

        old_messages = compactable[:-keep_recent]
        recent_messages = compactable[-keep_recent:]

        # Unpin oldest inject messages if pinned tokens exceed 50% of budget.
        pinned_tokens = CompactUtils.estimate_messages_tokens(pinned_msgs)
        if pinned_tokens > budget * 0.5 and len(pinned_msgs) > 1:
            logger.warning(
                "ContextGuard: pinned messages consume %d tokens "
                "(%.0f%% of budget %d) — unpinning oldest inject messages",
                pinned_tokens,
                pinned_tokens / budget * 0.5 * 100,
                budget,
            )
            # Keep only the most recent pinned message (the current user
            # query).  Move older pinned messages into compactable so
            # they can be summarised instead of silently exceeding budget.
            overflow = list(pinned_msgs[:-1])
            pinned_msgs = pinned_msgs[-1:]
            for msg in overflow:
                msg.pinned = False
            compactable = overflow + compactable
            old_messages = compactable[:-keep_recent]
            recent_messages = compactable[-keep_recent:]

        # Build the text block to summarise.
        lines: list[str] = []
        for msg in old_messages:
            prefix = msg.role.capitalize()
            lines.append(f"{prefix}: {CompactUtils.content_as_text(msg.content)}")
        history_text = "\n".join(lines)

        try:
            assert self._compact_llm is not None
            result = await self._compact_llm.chat([
                ChatMessage(role="system", content=prompt),
                ChatMessage(role="user", content=history_text),
            ])
            raw_content = result.message.content
            summary = (raw_content if isinstance(raw_content, str) else "").strip()
            # Strip <analysis> scratchpad blocks used by structured prompts.
            summary = re.sub(
                r"<analysis>.*?</analysis>", "", summary, flags=re.DOTALL,
            ).strip()
            if self._usage_tracker and result.usage:
                await self._usage_tracker.record(result.usage)
        except Exception:
            logger.warning(
                "ContextGuard LLM compact failed, falling back to truncation",
                exc_info=True,
            )
            return CompactUtils.smart_truncate(messages, budget)

        if not summary:
            return CompactUtils.smart_truncate(messages, budget)

        compacted = [
            *system_msgs,
            *pinned_msgs,
            ChatMessage(
                role="system",
                content=f"[Conversation summary]: {summary}",
            ),
            *recent_messages,
        ]

        # If compacted result is still too long, truncate the recent part.
        if CompactUtils.estimate_messages_tokens(compacted) > budget:
            return CompactUtils.smart_truncate(compacted, budget)

        return compacted

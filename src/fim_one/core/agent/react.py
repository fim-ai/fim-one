"""ReAct (Reasoning + Acting) agent implementation.

This module provides a ``ReActAgent`` that uses structured JSON output from an
LLM to drive an iterative tool-use loop.  It also supports an optional *native*
function-calling mode where the LLM produces ``tool_calls`` directly (OpenAI
style) instead of emitting JSON action objects.
"""

from __future__ import annotations

__fim_license__ = "FIM-SAL-1.1"
__fim_origin__ = "https://github.com/fim-ai/fim-one"

import asyncio
import hashlib
import json
import logging
import os
import re
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from typing import Any

from fim_one.core.memory.base import BaseMemory
from fim_one.core.memory.context_guard import ContextGuard
from fim_one.core.memory.microcompact import micro_compact
from fim_one.core.model import BaseLLM, ChatMessage, LLMResult
from fim_one.core.model.retry import is_context_overflow
from fim_one.core.model.structured import StructuredCallResult, structured_llm_call
from fim_one.core.model.types import ToolCallRequest
from fim_one.core.model.usage import UsageTracker
from fim_one.core.tool import ToolRegistry
from fim_one.core.utils import extract_json

from .hooks import HookContext, HookPoint, HookRegistry
from .types import Action, AgentResult, StepResult
from .workspace import AgentWorkspace

# Callback invoked after each ReAct iteration.
# Signature: (iteration, action, observation, error, step_result)
IterationCallback = Callable[[int, Action, str | None, str | None, "StepResult | None"], Any]

logger = logging.getLogger(__name__)

# When the total number of available tools exceeds this threshold, the agent
# runs a lightweight "selection" LLM call first to pick the most relevant
# tools for the current query.  This avoids injecting dozens of full schemas
# into the main conversation context.
TOOL_SELECTION_THRESHOLD = int(os.getenv("REACT_TOOL_SELECTION_THRESHOLD", "12"))

# Maximum number of tools the selection phase may pick.
_TOOL_SELECTION_MAX = int(os.getenv("REACT_TOOL_SELECTION_MAX", "6"))

# Every N tool-call iterations, inject a lightweight self-reflection prompt
# to prevent goal drift in long reasoning chains.
_SELF_REFLECTION_INTERVAL = int(os.getenv("REACT_SELF_REFLECTION_INTERVAL", "6"))

# Max chars per tool observation in synthesis prompt to prevent context overflow.
_TOOL_OBS_TRUNCATION = int(os.getenv("REACT_TOOL_OBS_TRUNCATION", "8000"))

# Approximate token budget for all tool results combined across a single run.
# Tool results exceeding this cumulative budget are truncated.
_TOOL_RESULT_BUDGET = int(os.getenv("REACT_TOOL_RESULT_BUDGET", "40000"))

# Cycle detection: when the same (tool_name, args_hash) pair appears this many
# times, inject a deterministic warning message.
_CYCLE_DETECTION_THRESHOLD = int(os.getenv("REACT_CYCLE_DETECTION_THRESHOLD", "2"))

_CYCLE_WARNING_TEMPLATE = (
    "\u26a0 You have called `{tool_name}` with identical arguments "
    "{count} times and received the same result. "
    "Please try a different approach or tool."
)

# Completion checklist: one-time verification prompt injected before accepting
# a final answer when the agent has used enough tools to warrant verification.
_COMPLETION_CHECK_MIN_TOOLS = int(
    os.getenv("REACT_COMPLETION_CHECK_MIN_TOOLS", "3"),
)

# Character threshold for skipping the completion check.  Answers longer
# than this (~200 tokens) are almost always substantive enough that the
# extra verification round-trip adds latency without value.
_COMPLETION_CHECK_SKIP_CHARS = int(
    os.getenv("REACT_COMPLETION_CHECK_SKIP_CHARS", "800"),
)

_COMPLETION_CHECK_PROMPT = (
    "Before finalizing your answer, verify:\n"
    "1. Does your answer fully address the original question?\n"
    "2. Did you verify key facts from tool results?\n"
    "3. Are there any contradictions in the information gathered?\n"
    "If everything checks out, proceed with your final answer. "
    "If not, continue investigating."
)

_SELF_REFLECTION_PROMPT = (
    "[Self-check] You have completed {iteration} tool-call iterations. "
    "Pause and reflect:\n"
    "- Original goal: {goal}\n"
    "- Are you still on track toward this goal?\n"
    "- Have you been repeating similar actions or going in circles?\n"
    "- What is the most direct next step to finish?\n"
    "If you have enough information, produce your final answer now."
)

_TOOL_SELECTION_PROMPT = """\
You are a tool selection assistant.  Given a user query and a catalog of \
available tools, select the tools most likely to be needed.

User query: {query}

Available tools:
{catalog}

Return a JSON object with a single key "tools" whose value is a list of \
tool names (strings) you would use to answer this query.  Select at most \
{max_tools} tools.  Only include tools that are clearly relevant.

Example response:
{{"tools": ["web_search", "python_exec"]}}
"""

_TOOL_SELECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tools": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["tools"],
}

_SYSTEM_PROMPT_TEMPLATE = """\
You are FIM One, an AI-powered assistant. \
You solve tasks by reasoning step-by-step and using tools when necessary. \
Never claim to be any other AI — you are FIM One.

Current date and time: {current_datetime} (the current year is {current_year}). \
When searching for up-to-date information, always use the current year \
({current_year}) in your queries, NOT a previous year.

You MUST respond with a single JSON object (no markdown, no extra text) in \
one of the following two formats:

1. To call a tool:
{{
  "type": "tool_call",
  "reasoning": "<your step-by-step reasoning>",
  "tool_name": "<name of the tool>",
  "tool_args": {{<arguments as key-value pairs>}}
}}

2. To signal you are done (no more tools needed):
{{
  "type": "final_answer",
  "reasoning": "<your step-by-step reasoning>",
  "answer": "<concise summary of key findings and results>"
}}

Available tools:
{tool_descriptions}

Guidelines:
- Always explain your reasoning before acting.
- Use tools only when the task requires external information or computation.
- When you have enough information, produce a final_answer immediately.
- If a tool call fails, analyse the error and decide whether to retry with \
different arguments or produce a final answer with the information you have.
- Be EFFICIENT: try to accomplish as much as possible in each tool call. \
Write a single comprehensive script rather than making many small calls. \
For example, generate data AND analyse it in one script when feasible.
- IMPORTANT: In the "answer" field, write a concise summary of the key findings \
and results you gathered (NOT the full polished answer — a separate synthesis \
step handles that). Focus on facts, data points, and conclusions. Keep it brief \
but substantive. Do NOT use python_exec just to print/format results — write \
the summary directly in the "answer" field instead.
- Do NOT generate charts, plots, or images (e.g. matplotlib) unless the user \
explicitly asks for visualisation. Prefer text tables and formatted output.
- If you need a tool that is not listed above, use request_tools to load it \
(when available). The request_tools description lists all unloaded tools.
- LANGUAGE: By default, respond in the same language as the user's query. \
However, if an Agent Directive specifies different language behaviour \
(e.g. a translation agent), follow the Agent Directive instead.
- CRITICAL: Your ENTIRE response must be a single JSON object. No markdown, no plain text, no code fences.
- FILE INTEGRITY: When a user asks about a specific file, you MUST only use \
content from THAT file to answer. If you cannot read or extract content from \
the target file, inform the user clearly — NEVER read other files and present \
their content as if it belongs to the target file. This is a critical safety \
rule: using content from unrelated files to answer questions about a specific \
file constitutes hallucination and is strictly forbidden.
- If an approach fails, diagnose WHY before switching tactics. Don't retry identical actions.
- If you called the same tool with identical arguments twice and got the same result, change approach or finalize.
- When a tool returns exit code 1 for grep/diff/test, this means "no match/difference/false" — NOT an error.
"""

_VISION_CONTEXT_HINT = """\
- VISION CONTEXT: Images from uploaded documents have been included in this \
conversation. You can see them directly in the message. When a file's text \
content cannot be extracted (e.g., scanned PDFs, image-based documents), \
look at the attached images to describe and answer questions about the file's \
content. Do NOT report that you cannot read the file if its visual content \
is visible to you in the conversation."""

_NATIVE_TOOLS_SYSTEM_PROMPT_TEMPLATE = """\
You are FIM One, an AI-powered assistant. \
You solve tasks by reasoning step-by-step and using tools when necessary. \
Never claim to be any other AI — you are FIM One.

Current date and time: {current_datetime} (the current year is {current_year}). \
When searching for up-to-date information, always use the current year \
({current_year}) in your queries, NOT a previous year.

Guidelines:
- Always think carefully before acting.
- Use tools only when the task requires external information or computation.
- Be EFFICIENT: try to accomplish as much as possible in each tool call. \
Write a single comprehensive script rather than making many small calls.
- If a tool call fails, analyse the error and decide whether to retry with \
different arguments or move on with the information you have.
- When you have gathered enough information to answer, STOP calling tools and \
respond with a concise summary of the key findings and results you gathered. \
Do NOT write the full polished answer — a separate synthesis step handles that. \
Focus on facts, data points, and conclusions. Do NOT use python_exec just to \
print/format results — write the summary directly in your response instead.
- If you need a tool that is not currently available, use request_tools to load \
it (when available). The request_tools description lists all unloaded tools.
- LANGUAGE: By default, respond in the same language as the user's query. \
However, if an Agent Directive specifies different language behaviour \
(e.g. a translation agent), follow the Agent Directive instead.
- FILE INTEGRITY: When a user asks about a specific file, you MUST only use \
content from THAT file to answer. If you cannot read or extract content from \
the target file, inform the user clearly — NEVER read other files and present \
their content as if it belongs to the target file. This is a critical safety \
rule: using content from unrelated files to answer questions about a specific \
file constitutes hallucination and is strictly forbidden.
- If an approach fails, diagnose WHY before switching tactics. Don't retry identical actions.
- If you called the same tool with identical arguments twice and got the same result, change approach or finalize.
- When a tool returns exit code 1 for grep/diff/test, this means "no match/difference/false" — NOT an error.
"""


class ReActAgent:
    """A ReAct agent that reasons and acts through structured JSON output.

    The agent maintains a growing conversation history.  At each iteration it
    asks the LLM for a JSON action, executes any requested tool call, appends
    the observation, and continues until a ``final_answer`` is produced or the
    maximum iteration count is reached.

    When ``use_native_tools=True`` and the LLM advertises ``tool_call``
    capability, the agent delegates tool invocation to the LLM's native
    function-calling interface instead of parsing JSON actions.

    Args:
        llm: The language model backend to use for reasoning.
        tools: A registry of tools the agent may invoke.
        system_prompt: An optional override for the default system prompt.
            When provided the default ReAct instructions are *replaced*
            entirely -- make sure to include tool descriptions yourself.
        extra_instructions: Optional additional instructions appended to the
            default system prompt.  Unlike ``system_prompt``, these are
            *merged* with the default template rather than replacing it.
            Ideal for per-agent customisation (e.g. "You are a financial
            analyst...").
        max_iterations: Safety limit on reasoning iterations.
        use_native_tools: Whether to prefer the LLM's native function-calling
            interface.  The feature is only activated when the LLM also
            advertises ``tool_call`` capability.
        memory: Optional conversation memory for multi-turn sessions.
        context_guard: Optional context-window budget manager.  When
            provided, messages are checked against a token budget before
            each LLM call and compacted if necessary.
        hook_registry: Optional hook registry for deterministic enforcement
            hooks that run outside the LLM loop.  When provided, hooks
            are executed at PRE_TOOL_USE, POST_TOOL_USE, and SESSION_START
            points automatically.
        workspace: Optional per-conversation workspace for offloading large
            tool outputs.  When provided, workspace tools are auto-registered
            and tool outputs exceeding the offload threshold are saved to
            files with a preview injected into the conversation.
        agent_directive: Optional agent-level instructions that define the
            agent's core purpose (e.g. "translate Chinese to English").
            Unlike ``extra_instructions`` (which bundles Skills, KB hints,
            and preferences), this is injected into the **synthesis** step
            so the final answer honours the agent's identity.
    """

    def __init__(
        self,
        llm: BaseLLM,
        tools: ToolRegistry,
        system_prompt: str | None = None,
        extra_instructions: str | None = None,
        max_iterations: int = int(os.getenv("REACT_MAX_ITERATIONS", "50")),
        use_native_tools: bool = True,
        memory: BaseMemory | None = None,
        context_guard: ContextGuard | None = None,
        hook_registry: HookRegistry | None = None,
        workspace: AgentWorkspace | None = None,
        fast_llm: BaseLLM | None = None,
        user_timezone: str | None = None,
        agent_directive: str | None = None,
        pinned_tools: list[str] | None = None,
        max_turn_tokens: int = int(os.getenv("REACT_MAX_TURN_TOKENS", "0")),
        completion_check: bool = True,
    ) -> None:
        self._llm = llm
        self._fast_llm = fast_llm
        # Tool-decision iterations use the fast model when available;
        # final synthesis (stream_answer) keeps the primary model.
        self._tool_llm = fast_llm or llm
        self._tools = tools
        self._system_prompt_override = system_prompt
        self._user_timezone = user_timezone
        self._extra_instructions = extra_instructions
        self._agent_directive = agent_directive
        self._pinned_tools = pinned_tools or []
        self._max_iterations = max_iterations
        self._use_native_tools = use_native_tools
        self._memory = memory
        self._context_guard = context_guard
        self._hook_registry = hook_registry
        self._workspace = workspace
        self._max_turn_tokens = max_turn_tokens
        self._completion_check = completion_check

        # Auto-register workspace tools when a workspace is provided.
        if workspace is not None:
            self._register_workspace_tools(workspace)

    # ------------------------------------------------------------------
    # Public read-only properties
    # ------------------------------------------------------------------

    @property
    def tools(self) -> ToolRegistry:
        """The tool registry available to this agent."""
        return self._tools

    @property
    def system_prompt_override(self) -> str | None:
        """The custom system prompt, if one was provided."""
        return self._system_prompt_override

    @property
    def extra_instructions(self) -> str | None:
        """Additional instructions appended to the default system prompt."""
        return self._extra_instructions

    @property
    def max_iterations(self) -> int:
        """Maximum number of reasoning iterations."""
        return self._max_iterations

    @property
    def context_guard(self) -> ContextGuard | None:
        """The context-window budget manager, if configured."""
        return self._context_guard

    @property
    def hook_registry(self) -> HookRegistry | None:
        """The hook registry for deterministic enforcement hooks."""
        return self._hook_registry

    @property
    def workspace(self) -> AgentWorkspace | None:
        """The per-conversation workspace, if configured."""
        return self._workspace

    @property
    def _native_mode_active(self) -> bool:
        """Whether native function-calling mode is currently active."""
        return (
            self._use_native_tools
            and self._tool_llm.abilities.get("tool_call", False)
        )

    def _register_workspace_tools(self, workspace: AgentWorkspace) -> None:
        """Register the three workspace builtin tools into the tool registry.

        This is called automatically during ``__init__`` when a workspace
        is provided.  Tools that are already registered (e.g. from a
        previous call) are silently skipped.
        """
        from .workspace_tools import (
            ListWorkspaceFilesTool,
            ReadWorkspaceFileTool,
            WriteHandoffTool,
        )

        workspace_tools = [
            ReadWorkspaceFileTool(workspace),
            ListWorkspaceFilesTool(workspace),
            WriteHandoffTool(workspace),
        ]
        for tool in workspace_tools:
            if tool.name not in self._tools:
                self._tools.register(tool)

    @staticmethod
    def _compute_args_hash(args: dict[str, Any] | None) -> str:
        """Compute a stable hash of tool arguments for cycle detection.

        Args:
            args: The tool arguments dict.

        Returns:
            An MD5 hex digest of the JSON-serialised arguments.
        """
        serialised = json.dumps(args or {}, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(serialised.encode()).hexdigest()

    def _check_cycle(
        self,
        tool_name: str,
        tool_args: dict[str, Any] | None,
        cycle_tracker: dict[tuple[str, str], int],
    ) -> str | None:
        """Check for repeated identical tool calls and return a warning if needed.

        Increments the count for ``(tool_name, args_hash)`` and returns a
        warning message when the count reaches ``_CYCLE_DETECTION_THRESHOLD``.

        Args:
            tool_name: The name of the tool being called.
            tool_args: The arguments passed to the tool.
            cycle_tracker: Mutable dict tracking call counts per
                ``(tool_name, args_hash)`` pair.

        Returns:
            A warning message string if the threshold is reached, else ``None``.
        """
        args_hash = self._compute_args_hash(tool_args)
        key = (tool_name, args_hash)
        cycle_tracker[key] = cycle_tracker.get(key, 0) + 1
        count = cycle_tracker[key]
        if count >= _CYCLE_DETECTION_THRESHOLD:
            warning = _CYCLE_WARNING_TEMPLATE.format(
                tool_name=tool_name, count=count,
            )
            logger.warning(
                "Cycle detected: %s called %d times with identical args",
                tool_name, count,
            )
            return warning
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        query: str,
        on_iteration: IterationCallback | None = None,
        image_urls: list[str] | None = None,
        interrupt_queue: Any | None = None,
    ) -> AgentResult:
        """Execute the ReAct loop for a given user query.

        When the number of registered tools exceeds
        ``TOOL_SELECTION_THRESHOLD``, a lightweight selection phase runs
        first to pick only the most relevant tools for *query*.  The
        selected subset is then used for the main reasoning loop,
        reducing context consumption significantly.

        Args:
            query: The user question or task description.
            on_iteration: Optional callback invoked after each iteration with
                ``(iteration, action, observation, error)``.
            image_urls: Optional list of base64 data-URLs for images to
                include in the first user message (vision model support).
            interrupt_queue: Optional queue for mid-stream user message
                injection.  Messages are drained between iterations.

        Returns:
            An ``AgentResult`` containing the final answer and full step trace.
        """
        # --- Two-phase tool selection ---
        effective_tools = self._tools
        if len(self._tools) > TOOL_SELECTION_THRESHOLD:
            effective_tools = await self._select_relevant_tools(
                query, on_iteration,
            )

            # Register the request_tools meta-tool when tool selection
            # actually filtered the set (effective_tools is a strict subset
            # of self._tools).  This lets the LLM dynamically load tools
            # that weren't included in the initial selection.
            if effective_tools is not self._tools:
                from fim_one.core.tool.builtin.request_tools import (
                    RequestToolsTool,
                )

                request_tools_tool = RequestToolsTool(
                    all_tools=self._tools,
                    active_tools=effective_tools,
                )
                effective_tools.register(request_tools_tool)
                # Also register in self._tools so _execute_tool_call /
                # _execute_native_tool_calls can find it during lookup.
                if "request_tools" not in self._tools:
                    self._tools.register(request_tools_tool)

        if self._native_mode_active:
            return await self._run_native(
                query, on_iteration, image_urls=image_urls,
                interrupt_queue=interrupt_queue,
                effective_tools=effective_tools,
            )
        return await self._run_json(
            query, on_iteration, image_urls=image_urls,
            interrupt_queue=interrupt_queue,
            effective_tools=effective_tools,
        )

    async def stream_answer(
        self,
        query: str,
        result: AgentResult,
        *,
        language_directive: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream the final answer via a dedicated LLM call.

        Mirrors ``PlanAnalyzer.stream_synthesize()`` -- tool iterations use
        fast non-streaming ``chat()``, then this method generates the real
        streamed answer from the full conversation context.

        Args:
            query: The original user query.
            result: The ``AgentResult`` from :meth:`run`, containing
                ``messages`` (full conversation history) used as context.
            language_directive: Optional language override directive.

        Yields:
            Incremental text chunks (tokens) of the synthesised answer.
        """
        # Build a synthesis context from the conversation messages.
        # Filter to tool calls and their results for a concise summary.
        context_parts: list[str] = []
        for msg in result.messages:
            if msg.role == "assistant" and msg.tool_calls:
                for tc in msg.tool_calls:
                    context_parts.append(
                        f"Tool call: {tc.name}({json.dumps(tc.arguments, ensure_ascii=False)})"
                    )
            elif msg.role == "tool":
                obs = msg.content or ""
                # Truncate very long tool results to keep the synthesis
                # prompt within reasonable token limits while preserving
                # enough content for structured data (JSON, tables, code).
                if len(obs) > _TOOL_OBS_TRUNCATION:
                    obs = obs[:_TOOL_OBS_TRUNCATION] + "... (truncated)"
                context_parts.append(f"Tool result: {obs}")
            elif msg.role == "assistant" and msg.content:
                # Final iteration content (the brief/fallback answer).
                context_parts.append(f"Assistant reasoning: {msg.content}")

        system_parts = [
            "You are FIM One, an AI-powered assistant. Never claim to be any "
            "other AI — you are FIM One. "
            "You synthesize a final answer from ReAct agent execution results. "
            "Provide a concise, coherent response that addresses the original "
            "question. Do NOT include meta-commentary like 'based on the results' "
            "or 'according to the tool output' -- just answer directly.",
        ]

        # Carry agent directive into synthesis so the agent's purpose is
        # honoured in the final answer (e.g. a translation agent should
        # output a translation, not a conversational reply).
        # Only the directive is injected — Skills/KB hints are irrelevant
        # for synthesis (tool execution is already complete).
        if self._agent_directive:
            system_parts.append("")
            system_parts.append(f"## Agent Directive\n{self._agent_directive}")

        system_parts.extend([
            "",
            "Guidelines:",
            "- Present key results clearly; use markdown formatting when helpful.",
            "- LANGUAGE: By default answer in the same language as the original "
            "question. If the Agent Directive specifies different language "
            "behaviour (e.g. translation), follow the Agent Directive.",
            "- FILE DELIVERY: If the agent wrote results to a file (e.g. via file_operations "
            "or python_exec writing to disk), do NOT repeat the full file content in your "
            "response. Instead, briefly summarize the key findings/conclusions (2-4 sentences) "
            "and mention the file name so the user knows where to find the details.",
        ])
        if language_directive:
            system_parts.append(f"- {language_directive}")

        system_content = "\n".join(system_parts)

        tool_context = "\n".join(context_parts) if context_parts else "(no tool calls)"
        user_content = (
            f"Question: {query}\n\n"
            f"Agent execution trace:\n{tool_context}"
        )

        messages = [
            ChatMessage(role="system", content=system_content),
            ChatMessage(role="user", content=user_content),
        ]

        async for chunk in self._llm.stream_chat(messages):
            if chunk.delta_content:
                yield chunk.delta_content

    # ------------------------------------------------------------------
    # Tool selection phase
    # ------------------------------------------------------------------

    # Common English stop-words excluded from keyword matching to reduce
    # false positives.  Keep this set small and focused on function words
    # that carry no domain meaning.
    _KEYWORD_STOP_WORDS: set[str] = frozenset({  # type: ignore[assignment]
        "a", "an", "the", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "shall", "can",
        "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "as", "into", "about", "between", "through", "during", "before",
        "after", "above", "below", "up", "down", "out", "off", "over",
        "under", "and", "but", "or", "nor", "not", "so", "yet", "both",
        "either", "neither", "each", "every", "all", "any", "few",
        "more", "most", "other", "some", "such", "no", "only", "own",
        "same", "than", "too", "very", "just", "because", "if", "when",
        "while", "how", "what", "which", "who", "whom", "this", "that",
        "these", "those", "i", "me", "my", "we", "our", "you", "your",
        "he", "him", "his", "she", "her", "it", "its", "they", "them",
        "their", "use", "using", "used", "get", "set", "make",
    })

    @staticmethod
    def _tokenize_for_keywords(text: str) -> set[str]:
        """Split *text* into a set of lowercase alphanumeric tokens.

        Underscores and hyphens are treated as word boundaries so that
        tool names like ``web_search`` yield ``{"web", "search"}``.
        Single-character tokens are discarded.
        """
        return {
            tok for tok in re.split(r"[^a-z0-9]+", text.lower())
            if len(tok) > 1
        }

    def _select_by_keywords(
        self,
        query: str,
        max_tools: int = _TOOL_SELECTION_MAX,
    ) -> list[str] | None:
        """Attempt to select tools via keyword overlap with the query.

        Tokenises the *query* and each tool's ``name`` + ``description``,
        then scores tools by the number of overlapping content words
        (stop-words excluded).

        Returns a list of tool names when the match is **unambiguous**:
        - The top-scoring tool has score >= 2, **and**
        - The top score is more than twice the second-highest score.

        When these conditions are not met, returns ``None`` to signal
        that the caller should fall back to LLM-based selection.

        Args:
            query: The user's current query.
            max_tools: Maximum number of tools to return.

        Returns:
            A list of tool names, or ``None`` if matching is ambiguous.
        """
        query_tokens = self._tokenize_for_keywords(query) - self._KEYWORD_STOP_WORDS
        if not query_tokens:
            return None

        scores: list[tuple[str, int]] = []
        for tool in self._tools.list_tools():
            tool_tokens = (
                self._tokenize_for_keywords(tool.name)
                | self._tokenize_for_keywords(tool.description)
            ) - self._KEYWORD_STOP_WORDS
            overlap = len(query_tokens & tool_tokens)
            if overlap > 0:
                scores.append((tool.name, overlap))

        if not scores:
            return None

        # Sort descending by score.
        scores.sort(key=lambda x: x[1], reverse=True)

        top_score = scores[0][1]
        second_score = scores[1][1] if len(scores) > 1 else 0

        # Confidence gate: need a meaningful match that clearly
        # dominates alternatives.
        if top_score < 2:
            logger.debug(
                "Keyword tool selection: top score %d < 2; skipping",
                top_score,
            )
            return None

        if second_score > 0 and top_score <= 2 * second_score:
            logger.debug(
                "Keyword tool selection: top=%d, second=%d — ambiguous; "
                "falling back to LLM",
                top_score,
                second_score,
            )
            return None

        # Collect all tools sharing the top score (there may be ties).
        selected = [name for name, sc in scores if sc == top_score]
        selected = selected[:max_tools]

        logger.debug(
            "Keyword tool selection: confident match — %s (score=%d, "
            "runner-up=%d)",
            selected,
            top_score,
            second_score,
        )
        return selected

    async def _select_relevant_tools(
        self,
        query: str,
        on_iteration: IterationCallback | None = None,
    ) -> ToolRegistry:
        """Run a lightweight LLM call to select the most relevant tools.

        Builds a compact catalog (name + one-line description, no parameter
        schemas) and asks the LLM to choose up to ``_TOOL_SELECTION_MAX``
        tools.  This keeps the main conversation context lean when many
        tools are available.

        If the selection call fails or returns unparseable output, the full
        tool set is returned as a safe fallback.

        Args:
            query: The user's current query.
            on_iteration: Optional callback to emit a ``selecting_tools``
                phase event to the SSE stream.

        Returns:
            A filtered ``ToolRegistry`` with only the selected tools.
        """
        # --- Fast path: keyword-based selection (no LLM call) ---
        keyword_result = self._select_by_keywords(query)
        if keyword_result is not None:
            filtered = self._tools.filter_by_names(keyword_result)
            if len(filtered) > 0:
                # Apply the same pinning logic as the LLM path.
                pin_names = {"read_skill", *self._pinned_tools}
                for pin_name in pin_names:
                    if pin_name not in [t.name for t in filtered.list_tools()]:
                        pin_tool = self._tools.get(pin_name)
                        if pin_tool is not None:
                            filtered.register(pin_tool)
                            logger.debug(
                                "Keyword tool selection: pinned '%s'",
                                pin_name,
                            )
                logger.info(
                    "Tool selection (keyword shortcut): %d/%d tools "
                    "selected: %s — LLM call skipped",
                    len(filtered),
                    len(self._tools),
                    [t.name for t in filtered.list_tools()],
                )
                return filtered

        # --- Slow path: LLM-based selection ---
        catalog = self._tools.to_compact_catalog()
        prompt = _TOOL_SELECTION_PROMPT.format(
            query=query,
            catalog=catalog,
            max_tools=_TOOL_SELECTION_MAX,
        )

        # Emit phase event so the frontend can show a brief indicator.
        if on_iteration is not None:
            on_iteration(
                0,
                Action(
                    type="tool_call",
                    reasoning="Selecting relevant tools from catalog",
                    tool_name="__selecting_tools__",
                    tool_args={"total": len(self._tools)},
                ),
                None,
                None,
                None,
            )

        try:
            selection_llm = self._fast_llm or self._llm
            call_result: StructuredCallResult[Any] = await structured_llm_call(
                selection_llm,
                [
                    ChatMessage(role="system", content="You are a tool selection assistant. Respond only with JSON."),
                    ChatMessage(role="user", content=prompt),
                ],
                schema=_TOOL_SELECTION_SCHEMA,
                function_name="select_tools",
                default_value=None,
            )

            if call_result.value is None:
                logger.warning(
                    "Tool selection: all extraction levels failed; "
                    "falling back to all tools"
                )
                return self._tools

            selected_names: list[str] = call_result.value.get("tools", [])
            if not isinstance(selected_names, list) or not selected_names:
                logger.warning(
                    "Tool selection returned empty or invalid list; "
                    "falling back to all tools"
                )
                return self._tools

            # Ensure names are strings and cap at max.
            selected_names = [
                str(n) for n in selected_names[:_TOOL_SELECTION_MAX]
            ]

            filtered = self._tools.filter_by_names(selected_names)

            # If filtering resulted in zero tools (all names were bogus),
            # fall back to the full set.
            if len(filtered) == 0:
                logger.warning(
                    "Tool selection produced 0 valid tools; "
                    "falling back to all tools"
                )
                return self._tools

            # Pin essential tools that must always be available when their
            # capabilities are needed.  read_skill is pinned when the agent
            # has skills configured (indicated by the tool being registered).
            # Caller-specified pinned_tools (e.g. web_search for domain
            # tasks) are also added here.
            pin_names = {"read_skill", *self._pinned_tools}
            for pin_name in pin_names:
                if pin_name not in [t.name for t in filtered.list_tools()]:
                    pin_tool = self._tools.get(pin_name)
                    if pin_tool is not None:
                        filtered.register(pin_tool)
                        logger.debug(
                            "Tool selection: pinned '%s' (always required)",
                            pin_name,
                        )

            logger.info(
                "Tool selection: %d/%d tools selected: %s",
                len(filtered),
                len(self._tools),
                [t.name for t in filtered.list_tools()],
            )
            return filtered

        except Exception:
            logger.warning(
                "Tool selection failed; falling back to all tools",
                exc_info=True,
            )
            return self._tools

    # ------------------------------------------------------------------
    # JSON mode (original ReAct loop)
    # ------------------------------------------------------------------

    async def _run_json(
        self,
        query: str,
        on_iteration: IterationCallback | None = None,
        image_urls: list[str] | None = None,
        interrupt_queue: Any | None = None,
        effective_tools: ToolRegistry | None = None,
    ) -> AgentResult:
        """Execute the JSON-based ReAct loop.

        Args:
            effective_tools: When provided, overrides ``self._tools`` for
                this run (used by the two-phase tool selection mechanism).
        """
        tools = effective_tools if effective_tools is not None else self._tools
        usage_tracker = UsageTracker()

        system_prompt = self._build_system_prompt(tools=tools)
        if image_urls:
            system_prompt += "\n" + _VISION_CONTEXT_HINT
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=system_prompt),
        ]

        # Load history from memory.
        if self._memory is not None:
            history = await self._memory.get_messages()
            for msg in history:
                if msg.role != "system":
                    messages.append(msg)

        # Build user message -- use vision content array when images are attached.
        user_content: str | list[dict[str, Any]] = query
        if image_urls:
            user_content = ChatMessage.build_vision_content(query, image_urls)
        messages.append(ChatMessage(role="user", content=user_content, pinned=True))

        steps: list[StepResult] = []
        response_format = self._json_response_format()
        tool_call_count = 0  # Track actual tool-call iterations for self-reflection
        cycle_tracker: dict[tuple[str, str], int] = {}  # (tool_name, args_hash) -> count
        completion_check_done = False  # One-shot flag for completion checklist
        tool_result_tokens = 0  # Cumulative token estimate for tool results (I.8)
        context_overflow_recovered = False  # One-shot flag for I.9 reactive compact

        for iteration in range(1, self._max_iterations + 1):
            logger.debug("ReAct iteration %d", iteration)

            # --- Per-turn token budget check ---
            if self._max_turn_tokens > 0:
                current_usage = usage_tracker.get_summary()
                if current_usage.total_tokens >= self._max_turn_tokens:
                    logger.warning(
                        "ReAct token budget exhausted: %d >= %d after %d iterations",
                        current_usage.total_tokens, self._max_turn_tokens, iteration - 1,
                    )
                    answer = (
                        f"I've reached the token budget ({self._max_turn_tokens:,} tokens) "
                        f"after {iteration - 1} iterations. Here is what I have so far:\n"
                        + self._summarise_steps(steps)
                    )
                    await self._save_to_memory(query, answer)
                    return AgentResult(
                        answer=answer,
                        steps=steps,
                        iterations=iteration - 1,
                        usage=usage_tracker.get_summary(),
                        messages=messages,
                    )

            # Signal thinking start before LLM call.
            if on_iteration is not None:
                on_iteration(
                    iteration,
                    Action(type="thinking", reasoning=""),
                    None, None, None,
                )

            messages = micro_compact(messages)
            if self._context_guard is not None:
                messages = await self._context_guard.check_and_compact(
                    messages, hint="react_iteration",
                )

            # Tool-decision iterations use the fast model (when available)
            # since choosing a tool + params doesn't need primary-tier reasoning.
            try:
                result: LLMResult = await self._tool_llm.chat(
                    messages,
                    response_format=response_format,
                    reasoning_effort=None,
                )
            except Exception as exc:
                if is_context_overflow(exc) and not context_overflow_recovered:
                    context_overflow_recovered = True
                    logger.warning(
                        "Context overflow detected in JSON mode "
                        "(iteration %d), forcing compact to 50%%",
                        iteration,
                    )
                    messages = await self._force_compact(
                        messages, target_ratio=0.5, hint="react_iteration",
                    )
                    result = await self._tool_llm.chat(
                        messages,
                        response_format=response_format,
                        reasoning_effort=None,
                    )
                else:
                    raise
            await usage_tracker.record(result.usage)

            raw_content = result.message.content
            assistant_content = raw_content if isinstance(raw_content, str) else ""
            action = self._parse_action(assistant_content)

            # Use API-level reasoning_content as fallback when the JSON
            # reasoning field is empty (extended thinking models like
            # DeepSeek R1 return reasoning outside the content body).
            if not action.reasoning and result.message.reasoning_content:
                action.reasoning = result.message.reasoning_content

            # If JSON parsing failed, ask the LLM to re-format as JSON
            # (one retry).  The ``continue`` naturally advances ``iteration``
            # so this counts against ``max_iterations``.
            if (
                action.type == "final_answer"
                and action.reasoning == "(could not parse LLM output as JSON)"
                and iteration < self._max_iterations
            ):
                logger.info(
                    "JSON parse failed, requesting LLM to re-format "
                    "(iteration %d)",
                    iteration,
                )
                # Append the raw reply so the LLM sees what it said.
                messages.append(
                    ChatMessage(role="assistant", content=assistant_content),
                )
                # Ask it to wrap the content in JSON.
                messages.append(
                    ChatMessage(
                        role="user",
                        content=(
                            "Your previous response was not valid JSON. "
                            "Please re-format your answer as a JSON object "
                            "with the structure: "
                            '{"type": "final_answer", "reasoning": "...", '
                            '"answer": "..."}. '
                            'Put your full answer inside the "answer" field '
                            "as a string."
                        ),
                    ),
                )
                continue  # Skip to next iteration, which will call LLM again

            # Append the raw assistant reply to the conversation.
            messages.append(
                ChatMessage(role="assistant", content=assistant_content),
            )

            # --- Interrupt check: drain queued user messages ---
            # Drain BEFORE checking final_answer so injections are never lost.
            # (In JSON mode there is no tool_use/tool_result pairing constraint,
            # so it is safe to insert a user message here.)
            injected_msgs = (await interrupt_queue.drain()) if interrupt_queue is not None else []
            self._emit_and_append_injections(
                injected_msgs, messages, iteration, on_iteration,
            )

            # -- Final answer path --
            if action.type == "final_answer":
                # If user injected messages, force another iteration so the
                # agent sees them instead of ending.
                if injected_msgs and iteration < self._max_iterations:
                    logger.info(
                        "Deferring final answer -- %d injected message(s) "
                        "pending (iteration %d)",
                        len(injected_msgs),
                        iteration,
                    )
                    continue

                # --- Completion checklist (I.12 lightweighted) ---
                # When the agent used tools and hasn't been verified yet,
                # inject a one-time verification prompt and let the LLM
                # re-evaluate before accepting the final answer.
                # Long answers (> ~200 tokens) are almost always substantive
                # enough to skip this extra round-trip.
                final_answer_text = action.answer or ""
                if (
                    self._completion_check
                    and tool_call_count >= _COMPLETION_CHECK_MIN_TOOLS
                    and not completion_check_done
                    and iteration < self._max_iterations
                    and len(final_answer_text) <= _COMPLETION_CHECK_SKIP_CHARS
                ):
                    completion_check_done = True
                    messages.append(
                        ChatMessage(role="user", content=_COMPLETION_CHECK_PROMPT),
                    )
                    logger.info(
                        "Injected completion checklist at iteration %d "
                        "(tool_call_count=%d, answer_len=%d)",
                        iteration, tool_call_count, len(final_answer_text),
                    )
                    continue

                if (
                    self._completion_check
                    and not completion_check_done
                    and len(final_answer_text) > _COMPLETION_CHECK_SKIP_CHARS
                ):
                    completion_check_done = True
                    logger.info(
                        "Skipped completion check — answer length %d "
                        "exceeds threshold %d",
                        len(final_answer_text), _COMPLETION_CHECK_SKIP_CHARS,
                    )

                steps.append(StepResult(action=action))
                if on_iteration is not None:
                    on_iteration(iteration, action, None, None, None)
                answer = final_answer_text
                await self._save_to_memory(query, answer)
                return AgentResult(
                    answer=answer,
                    steps=steps,
                    iterations=iteration,
                    usage=usage_tracker.get_summary(),
                    messages=messages,
                )

            # -- Tool call path --
            if on_iteration is not None:
                on_iteration(iteration, action, None, None, None)

            step = await self._execute_tool_call(action)
            steps.append(step)
            tool_call_count += 1

            # Feed the tool result/error back into the conversation so the LLM
            # can observe and adapt on the next iteration (Observe step of ReAct).
            observation = step.observation or (
                f"Tool '{action.tool_name or 'unknown'}' completed successfully "
                f"with no output. Do not retry with same arguments."
            )
            # Offload large outputs to workspace when available.
            if self._workspace is not None and step.observation and not step.error:
                observation = self._workspace.maybe_offload(
                    action.tool_name or "unknown", observation,
                )
            obs_content = (
                f"Observation: Error: {step.error}"
                if step.error
                else f"Observation: {observation}"
            )

            # --- Tool result aggregate budget (I.8) ---
            estimated_tokens = len(obs_content) // 4
            if tool_result_tokens + estimated_tokens > _TOOL_RESULT_BUDGET:
                max_chars = max(0, (_TOOL_RESULT_BUDGET - tool_result_tokens) * 4)
                obs_content = (
                    obs_content[:max_chars]
                    + f"\n\n[Truncated: tool result exceeded aggregate budget "
                    f"({tool_result_tokens}/{_TOOL_RESULT_BUDGET} tokens used)]"
                )
                estimated_tokens = len(obs_content) // 4
                logger.warning(
                    "Tool result budget exceeded: %d/%d tokens after truncation",
                    tool_result_tokens + estimated_tokens, _TOOL_RESULT_BUDGET,
                )
            tool_result_tokens += estimated_tokens

            messages.append(ChatMessage(role="user", content=obs_content))

            # --- Cycle detection ---
            # Track identical (tool_name, args) calls and inject a warning
            # when the threshold is reached.
            cycle_warning = self._check_cycle(
                action.tool_name or "", action.tool_args, cycle_tracker,
            )
            if cycle_warning is not None:
                messages.append(ChatMessage(role="user", content=cycle_warning))

            # --- Dynamic tool reload (request_tools) ---
            # When request_tools successfully loads new tools into the
            # effective registry, rebuild the system prompt so the LLM
            # sees updated tool descriptions on the next iteration.
            if (
                action.tool_name == "request_tools"
                and not step.error
            ):
                messages[0] = ChatMessage(
                    role="system",
                    content=self._build_system_prompt(tools=tools),
                )
                logger.info(
                    "Rebuilt system prompt after request_tools "
                    "(now %d tools)", len(tools),
                )

            # --- Mid-loop self-reflection ---
            # Every N tool calls, inject a lightweight goal-check prompt to
            # prevent drift in long reasoning chains.
            if (
                tool_call_count % _SELF_REFLECTION_INTERVAL == 0
                and iteration < self._max_iterations
            ):
                reflection = _SELF_REFLECTION_PROMPT.format(
                    iteration=tool_call_count,
                    goal=query if isinstance(query, str) else "(see original query)",
                )
                messages.append(ChatMessage(role="user", content=reflection))
                logger.info(
                    "Injected self-reflection at tool-call #%d (iteration %d)",
                    tool_call_count, iteration,
                )

            if on_iteration is not None:
                on_iteration(iteration, action, step.observation, step.error, step)

        # Max iterations exceeded -- synthesise a timeout answer.
        logger.warning(
            "ReAct loop exhausted after %d iterations", self._max_iterations,
        )
        answer = (
            f"I was unable to complete the task within {self._max_iterations} "
            "iterations.  Here is what I gathered so far:\n"
            + self._summarise_steps(steps)
        )
        await self._save_to_memory(query, answer)
        return AgentResult(
            answer=answer,
            steps=steps,
            iterations=self._max_iterations,
            usage=usage_tracker.get_summary(),
            messages=messages,
        )

    # ------------------------------------------------------------------
    # Native function-calling mode
    # ------------------------------------------------------------------

    async def _run_native(
        self,
        query: str,
        on_iteration: IterationCallback | None = None,
        image_urls: list[str] | None = None,
        interrupt_queue: Any | None = None,
        effective_tools: ToolRegistry | None = None,
    ) -> AgentResult:
        """Execute the native function-calling loop.

        All iterations use non-streaming ``chat()`` for speed.  The final
        answer is generated by a separate ``stream_answer()`` call (like DAG).

        Args:
            effective_tools: When provided, overrides ``self._tools`` for
                building the tools payload (used by two-phase selection).
        """
        usage_tracker = UsageTracker()

        system_prompt = self._build_system_prompt_native()
        if image_urls:
            system_prompt += "\n" + _VISION_CONTEXT_HINT
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=system_prompt),
        ]

        # Load history from memory.
        if self._memory is not None:
            history = await self._memory.get_messages()
            for msg in history:
                if msg.role != "system":
                    messages.append(msg)

        # Build user message -- use vision content array when images are attached.
        user_content: str | list[dict[str, Any]] = query
        if image_urls:
            user_content = ChatMessage.build_vision_content(query, image_urls)
        messages.append(ChatMessage(role="user", content=user_content, pinned=True))

        steps: list[StepResult] = []
        tool_call_count = 0  # Track actual tool-call iterations for self-reflection
        cycle_tracker: dict[tuple[str, str], int] = {}  # (tool_name, args_hash) -> count
        completion_check_done = False  # One-shot flag for completion checklist
        tool_result_tokens = 0  # Cumulative token estimate for tool results (I.8)
        context_overflow_recovered = False  # One-shot flag for I.9 reactive compact

        # Build OpenAI-format tool definitions using the effective (possibly
        # filtered) tool set for context efficiency.
        tools_payload = self._build_tools_payload(tools=effective_tools)
        tool_choice: str | None = "auto" if tools_payload else None

        for iteration in range(1, self._max_iterations + 1):
            logger.debug("Native ReAct iteration %d", iteration)

            # --- Per-turn token budget check ---
            if self._max_turn_tokens > 0:
                current_usage = usage_tracker.get_summary()
                if current_usage.total_tokens >= self._max_turn_tokens:
                    logger.warning(
                        "Native ReAct token budget exhausted: %d >= %d after %d iterations",
                        current_usage.total_tokens, self._max_turn_tokens, iteration - 1,
                    )
                    answer = (
                        f"I've reached the token budget ({self._max_turn_tokens:,} tokens) "
                        f"after {iteration - 1} iterations. Here is what I have so far:\n"
                        + self._summarise_steps(steps)
                    )
                    await self._save_to_memory(query, answer)
                    return AgentResult(
                        answer=answer,
                        steps=steps,
                        iterations=iteration - 1,
                        usage=usage_tracker.get_summary(),
                        messages=messages,
                    )

            # Signal thinking start before LLM call.
            if on_iteration is not None:
                on_iteration(
                    iteration,
                    Action(type="thinking", reasoning=""),
                    None, None, None,
                )

            messages = micro_compact(messages)
            if self._context_guard is not None:
                messages = await self._context_guard.check_and_compact(
                    messages, hint="react_iteration",
                )

            # Tool-decision iterations use the fast model (when available).
            # The final answer is streamed separately via stream_answer()
            # using the primary model.
            try:
                result: LLMResult = await self._tool_llm.chat(
                    messages,
                    tools=tools_payload,
                    tool_choice=tool_choice,
                    reasoning_effort=None,
                )
            except Exception as exc:
                if is_context_overflow(exc) and not context_overflow_recovered:
                    context_overflow_recovered = True
                    logger.warning(
                        "Context overflow detected in native mode "
                        "(iteration %d), forcing compact to 50%%",
                        iteration,
                    )
                    messages = await self._force_compact(
                        messages, target_ratio=0.5, hint="react_iteration",
                    )
                    result = await self._tool_llm.chat(
                        messages,
                        tools=tools_payload,
                        tool_choice=tool_choice,
                        reasoning_effort=None,
                    )
                else:
                    raise
            await usage_tracker.record(result.usage)

            assistant_msg = result.message

            # Append the full assistant message (may contain tool_calls).
            messages.append(assistant_msg)

            # -- Tool call path --
            # In native mode, tool_result blocks MUST immediately follow the
            # assistant's tool_use blocks.  Drain the interrupt queue only
            # AFTER tool results are appended to preserve this ordering.
            if assistant_msg.tool_calls:
                tool_results = await self._execute_native_tool_calls(
                    assistant_msg.tool_calls,
                    iteration,
                    steps,
                    on_iteration,
                    reasoning=assistant_msg.reasoning_content or "",
                )

                # --- Tool result aggregate budget (I.8) ---
                for tr_msg in tool_results:
                    raw_content = tr_msg.content
                    # Tool results are always strings; skip vision arrays.
                    if not isinstance(raw_content, str):
                        continue
                    content_str: str = raw_content or ""
                    estimated_tokens = len(content_str) // 4
                    if tool_result_tokens + estimated_tokens > _TOOL_RESULT_BUDGET:
                        max_chars = max(
                            0, (_TOOL_RESULT_BUDGET - tool_result_tokens) * 4,
                        )
                        truncated = (
                            content_str[:max_chars]
                            + f"\n\n[Truncated: tool result exceeded aggregate "
                            f"budget ({tool_result_tokens}/"
                            f"{_TOOL_RESULT_BUDGET} tokens used)]"
                        )
                        tr_msg.content = truncated
                        estimated_tokens = len(truncated) // 4
                        logger.warning(
                            "Tool result budget exceeded: %d/%d tokens "
                            "after truncation",
                            tool_result_tokens + estimated_tokens,
                            _TOOL_RESULT_BUDGET,
                        )
                    tool_result_tokens += estimated_tokens

                messages.extend(tool_results)
                tool_call_count += 1

                # --- Cycle detection ---
                # Check each tool call in this batch for repetition.
                for tc in assistant_msg.tool_calls:
                    cycle_warning = self._check_cycle(
                        tc.name, dict(tc.arguments), cycle_tracker,
                    )
                    if cycle_warning is not None:
                        messages.append(
                            ChatMessage(role="user", content=cycle_warning),
                        )

                # --- Dynamic tool reload (request_tools) ---
                # If request_tools was among the tool calls and succeeded,
                # rebuild the tools payload so the LLM can see the newly
                # loaded tools on the next iteration.
                if any(tc.name == "request_tools" for tc in assistant_msg.tool_calls):
                    tools_payload = self._build_tools_payload(tools=effective_tools)
                    tool_choice = "auto" if tools_payload else None
                    logger.info(
                        "Rebuilt tools payload after request_tools "
                        "(now %d tools)",
                        len(effective_tools) if effective_tools else 0,
                    )

                # Now safe to drain -- tool_use/tool_result pairing is intact.
                injected_msgs = (await interrupt_queue.drain()) if interrupt_queue is not None else []
                self._emit_and_append_injections(
                    injected_msgs, messages, iteration, on_iteration,
                )

                # --- Mid-loop self-reflection ---
                if (
                    tool_call_count % _SELF_REFLECTION_INTERVAL == 0
                    and iteration < self._max_iterations
                ):
                    reflection = _SELF_REFLECTION_PROMPT.format(
                        iteration=tool_call_count,
                        goal=query if isinstance(query, str) else "(see original query)",
                    )
                    messages.append(ChatMessage(role="user", content=reflection))
                    logger.info(
                        "Injected self-reflection at tool-call #%d (iteration %d)",
                        tool_call_count, iteration,
                    )
                continue

            # -- Final answer path (no tool calls) --
            # Drain before returning so injections are never lost.
            injected_msgs = (await interrupt_queue.drain()) if interrupt_queue is not None else []
            self._emit_and_append_injections(
                injected_msgs, messages, iteration, on_iteration,
            )
            if injected_msgs and iteration < self._max_iterations:
                continue

            # --- Completion checklist (I.12 lightweighted) ---
            # When the agent used tools and hasn't been verified yet,
            # inject a one-time verification prompt and let the LLM
            # re-evaluate before accepting the final answer.
            # Long answers (> ~200 tokens) are almost always substantive
            # enough to skip this extra round-trip.
            raw_answer = assistant_msg.content
            native_answer_text = raw_answer if isinstance(raw_answer, str) else ""
            if (
                self._completion_check
                and tool_call_count >= _COMPLETION_CHECK_MIN_TOOLS
                and not completion_check_done
                and iteration < self._max_iterations
                and len(native_answer_text) <= _COMPLETION_CHECK_SKIP_CHARS
            ):
                completion_check_done = True
                messages.append(
                    ChatMessage(role="user", content=_COMPLETION_CHECK_PROMPT),
                )
                logger.info(
                    "Injected completion checklist at iteration %d "
                    "(tool_call_count=%d, answer_len=%d)",
                    iteration, tool_call_count, len(native_answer_text),
                )
                continue

            if (
                self._completion_check
                and not completion_check_done
                and len(native_answer_text) > _COMPLETION_CHECK_SKIP_CHARS
            ):
                completion_check_done = True
                logger.info(
                    "Skipped completion check — answer length %d "
                    "exceeds threshold %d",
                    len(native_answer_text), _COMPLETION_CHECK_SKIP_CHARS,
                )

            answer = native_answer_text
            action = Action(
                type="final_answer",
                reasoning=assistant_msg.reasoning_content or "",
                answer=answer,
            )
            steps.append(StepResult(action=action))
            if on_iteration is not None:
                on_iteration(iteration, action, None, None, None)
            await self._save_to_memory(query, answer)
            return AgentResult(
                answer=answer,
                steps=steps,
                iterations=iteration,
                usage=usage_tracker.get_summary(),
                messages=messages,
            )

        # Max iterations exceeded.
        logger.warning(
            "Native ReAct loop exhausted after %d iterations",
            self._max_iterations,
        )
        answer = (
            f"I was unable to complete the task within {self._max_iterations} "
            "iterations.  Here is what I gathered so far:\n"
            + self._summarise_steps(steps)
        )
        await self._save_to_memory(query, answer)
        return AgentResult(
            answer=answer,
            steps=steps,
            iterations=self._max_iterations,
            usage=usage_tracker.get_summary(),
            messages=messages,
        )

    async def _execute_native_tool_calls(
        self,
        tool_calls: list[ToolCallRequest],
        iteration: int,
        steps: list[StepResult],
        on_iteration: IterationCallback | None,
        reasoning: str = "",
    ) -> list[ChatMessage]:
        """Execute one or more native tool calls in parallel.

        Args:
            tool_calls: The tool call requests from the assistant message.
            iteration: The current iteration number.
            steps: The running list of step results (mutated in-place).
            on_iteration: Optional callback.
            reasoning: LLM reasoning/thinking content for this turn.

        Returns:
            A list of ``ChatMessage`` objects with role ``"tool"`` to append
            to the conversation.
        """
        async def _run_single(tc: ToolCallRequest) -> tuple[StepResult, ChatMessage]:
            from fim_one.core.tool.base import ToolResult

            tool_args = dict(tc.arguments)
            action = Action(
                type="tool_call",
                reasoning="",
                tool_name=tc.name,
                tool_args=tool_args,
            )

            # --- PRE_TOOL_USE hooks ---
            if self._hook_registry is not None:
                pre_ctx = HookContext(
                    hook_point=HookPoint.PRE_TOOL_USE,
                    tool_name=tc.name,
                    tool_args=tool_args,
                )
                pre_result = await self._hook_registry.run_pre_tool(pre_ctx)
                if not pre_result.allow:
                    error_msg = pre_result.error or "Tool call blocked by hook"
                    logger.info("Hook blocked tool '%s': %s", tc.name, error_msg)
                    step = StepResult(action=action, error=error_msg)
                    msg = ChatMessage(
                        role="tool",
                        content=f"Error: {error_msg}",
                        tool_call_id=tc.id,
                    )
                    return step, msg
                if pre_result.modified_args is not None:
                    tool_args = pre_result.modified_args

            tool = self._tools.get(tc.name)
            if tool is None:
                error_msg = (
                    f"Unknown tool '{tc.name}'. "
                    f"Available tools: {[t.name for t in self._tools.list_tools()]}"
                )
                step = StepResult(action=action, error=error_msg)
                msg = ChatMessage(
                    role="tool",
                    content=f"Error: {error_msg}",
                    tool_call_id=tc.id,
                )
                return step, msg

            try:
                raw_result: str | ToolResult = await tool.run(**tool_args)

                # Determine observation for POST hooks.
                if isinstance(raw_result, ToolResult):
                    observation = raw_result.content
                else:
                    observation = raw_result

                # --- POST_TOOL_USE hooks ---
                if self._hook_registry is not None:
                    post_ctx = HookContext(
                        hook_point=HookPoint.POST_TOOL_USE,
                        tool_name=tc.name,
                        tool_args=tool_args,
                        tool_result=observation,
                    )
                    post_result = await self._hook_registry.run_post_tool(post_ctx)
                    if post_result.modified_result is not None:
                        observation = post_result.modified_result
                        if isinstance(raw_result, ToolResult):
                            raw_result = ToolResult(
                                content=observation,
                                content_type=raw_result.content_type,
                                artifacts=raw_result.artifacts,
                            )
                        else:
                            raw_result = observation

                if isinstance(raw_result, ToolResult):
                    step = StepResult(
                        action=action,
                        observation=raw_result.content,
                        content_type=raw_result.content_type,
                        artifacts=[
                            {"name": a.name, "path": a.path, "mime_type": a.mime_type, "size": a.size}
                            for a in raw_result.artifacts
                        ] if raw_result.artifacts else None,
                    )
                    # For rich content types, give the LLM a short summary
                    # instead of the full content (which the frontend renders
                    # via iframe / markdown).  This prevents the LLM from
                    # echoing large HTML blobs in its final answer.
                    llm_content = raw_result.content or (
                        f"Tool '{tc.name}' completed successfully "
                        f"with no output. Do not retry with same arguments."
                    )
                    if raw_result.content_type in ("html", "markdown") and raw_result.artifacts:
                        names = ", ".join(a.name for a in raw_result.artifacts)
                        llm_content = (
                            f"[Artifact generated: {names}] "
                            "The content is rendered as a preview in the UI "
                            "and available for download. "
                            "Do NOT paste the raw source in your answer."
                        )
                    # Offload large ToolResult content to workspace.
                    if self._workspace is not None:
                        llm_content = self._workspace.maybe_offload(
                            tc.name, llm_content,
                        )
                    msg = ChatMessage(
                        role="tool",
                        content=llm_content,
                        tool_call_id=tc.id,
                    )
                    return step, msg
                # Offload large plain-string results to workspace.
                llm_result = raw_result or (
                    f"Tool '{tc.name}' completed successfully "
                    f"with no output. Do not retry with same arguments."
                )
                if self._workspace is not None:
                    llm_result = self._workspace.maybe_offload(
                        tc.name, raw_result,
                    )
                step = StepResult(action=action, observation=raw_result)
                msg = ChatMessage(
                    role="tool",
                    content=llm_result,
                    tool_call_id=tc.id,
                )
                return step, msg
            except Exception as exc:
                error_msg = f"{type(exc).__name__}: {exc}"
                logger.exception("Tool '%s' raised an exception", tc.name)
                step = StepResult(action=action, error=error_msg)
                msg = ChatMessage(
                    role="tool",
                    content=f"Error: {error_msg}",
                    tool_call_id=tc.id,
                )
                return step, msg

        # Notify all tool calls starting before parallel execution.
        if on_iteration is not None:
            for i, tc in enumerate(tool_calls):
                start_action = Action(
                    type="tool_call",
                    reasoning=reasoning if i == 0 else "",
                    tool_name=tc.name,
                    tool_args=tc.arguments,
                )
                on_iteration(iteration, start_action, None, None, None)

        results = await asyncio.gather(*[_run_single(tc) for tc in tool_calls])

        tool_messages: list[ChatMessage] = []
        for step_result, tool_msg in results:
            steps.append(step_result)
            tool_messages.append(tool_msg)

            if on_iteration is not None:
                on_iteration(
                    iteration,
                    step_result.action,
                    step_result.observation,
                    step_result.error,
                    step_result,
                )

        return tool_messages

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _emit_and_append_injections(
        injected_msgs: list[Any],
        messages: list[ChatMessage],
        iteration: int,
        on_iteration: IterationCallback | None,
    ) -> None:
        """Emit SSE inject events and append a combined user message.

        This is shared by both ``_run_json`` and ``_run_native`` to avoid
        duplicating the drain -> emit -> append logic.
        """
        if not injected_msgs:
            return

        # Emit individual SSE inject events for frontend rendering.
        for injected in injected_msgs:
            if on_iteration:
                on_iteration(iteration, Action(
                    type="tool_call", reasoning="",
                    tool_name="__inject__",
                    tool_args={"content": injected.content, "id": injected.id},
                ), injected.content, None, None)

        # Append as a SINGLE combined message so the LLM addresses ALL
        # injected messages, not just the last one.
        if len(injected_msgs) == 1:
            combined_content = (
                f"[USER INTERRUPT]: {injected_msgs[0].content}"
                "\n\nAcknowledge and adjust if needed."
            )
        else:
            parts = [f"{i+1}. {m.content}" for i, m in enumerate(injected_msgs)]
            combined_content = (
                f"[USER INTERRUPT]: The user sent {len(injected_msgs)} "
                "messages while you were working:\n"
                + "\n".join(parts)
                + "\n\nAcknowledge ALL of them and adjust your response if needed."
            )
        messages.append(ChatMessage(
            role="user",
            content=combined_content,
            pinned=True,
        ))

    def _get_localized_time(self) -> tuple[datetime, str, int]:
        """Return ``(datetime_obj, formatted_str, year)`` in the user's tz."""
        import zoneinfo

        utc_now = datetime.now(timezone.utc)
        tz_name = self._user_timezone
        if tz_name:
            try:
                tz = zoneinfo.ZoneInfo(tz_name)
                local_now = utc_now.astimezone(tz)
                formatted = local_now.strftime(f"%Y-%m-%d %H:%M ({tz_name})")
                return local_now, formatted, local_now.year
            except (KeyError, zoneinfo.ZoneInfoNotFoundError):
                pass  # invalid tz name, fall through to UTC
        formatted = utc_now.strftime("%Y-%m-%d %H:%M UTC")
        return utc_now, formatted, utc_now.year

    def _build_system_prompt(
        self, tools: ToolRegistry | None = None,
    ) -> str:
        """Build the system prompt, including descriptions of available tools.

        Args:
            tools: Optional tool registry override.  When ``None``,
                ``self._tools`` is used.

        Returns:
            The full system prompt string.
        """
        if self._system_prompt_override is not None:
            return self._system_prompt_override

        now_dt, now, year = self._get_localized_time()
        tool_descriptions = self._format_tool_descriptions(tools=tools)
        prompt = _SYSTEM_PROMPT_TEMPLATE.format(
            tool_descriptions=tool_descriptions,
            current_datetime=now,
            current_year=year,
        )
        if self._extra_instructions:
            prompt += f"\n\nAdditional instructions:\n{self._extra_instructions}"
        prompt = self._inject_handoff_context(prompt)
        return prompt

    def _build_system_prompt_native(self) -> str:
        """Build the system prompt for native function-calling mode.

        Returns:
            The system prompt string (tool descriptions are passed via the
            ``tools`` parameter instead of being embedded in the prompt).
        """
        if self._system_prompt_override is not None:
            return self._system_prompt_override

        now_dt, now, year = self._get_localized_time()
        prompt = _NATIVE_TOOLS_SYSTEM_PROMPT_TEMPLATE.format(
            current_datetime=now,
            current_year=year,
        )
        if self._extra_instructions:
            prompt += f"\n\nAdditional instructions:\n{self._extra_instructions}"
        prompt = self._inject_handoff_context(prompt)
        return prompt

    def _inject_handoff_context(self, prompt: str) -> str:
        """Append the latest handoff note to a system prompt, if available.

        This allows the agent to pick up context from a previous session
        or from a context-compression event.

        Args:
            prompt: The system prompt to augment.

        Returns:
            The prompt with handoff context appended, or unchanged if no
            handoff is available.
        """
        if self._workspace is None:
            return prompt
        handoff = self._workspace.read_latest_handoff()
        if not handoff:
            return prompt
        return (
            prompt
            + "\n\n## Previous Session Context (Handoff Note)\n"
            + handoff
        )

    def _format_tool_descriptions(
        self, tools: ToolRegistry | None = None,
    ) -> str:
        """Format tool descriptions for inclusion in the system prompt.

        Args:
            tools: Optional tool registry override.  When ``None``,
                ``self._tools`` is used.

        Returns:
            A human-readable listing of every registered tool with its
            name, description, and parameter schema.
        """
        registry = tools if tools is not None else self._tools
        tool_list = registry.list_tools()
        if not tool_list:
            return "(no tools available)"

        lines: list[str] = []
        for tool in tool_list:
            schema_str = json.dumps(tool.parameters_schema, indent=2)
            lines.append(
                f"- **{tool.name}**: {tool.description}\n"
                f"  Parameters: {schema_str}"
            )
        return "\n".join(lines)

    def _build_tools_payload(
        self, tools: ToolRegistry | None = None,
    ) -> list[dict[str, Any]] | None:
        """Build OpenAI-format tool definitions for native mode.

        Args:
            tools: Optional tool registry override.  When ``None``,
                ``self._tools`` is used.

        Returns:
            A list of tool definition dicts, or ``None`` if no tools are
            registered.
        """
        registry = tools if tools is not None else self._tools
        tool_list = registry.list_tools()
        if not tool_list:
            return None

        payload: list[dict[str, Any]] = []
        for tool in tool_list:
            payload.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters_schema,
                },
            })
        return payload

    def _json_response_format(self) -> dict[str, Any] | None:
        """Return a JSON-mode response format dict if the LLM supports it.

        Returns:
            ``{"type": "json_object"}`` when the model advertises
            ``json_mode`` support, otherwise ``None``.
        """
        if self._tool_llm.abilities.get("json_mode", False):
            return {"type": "json_object"}
        return None

    def _parse_action(self, content: str) -> Action:
        """Parse an LLM response into an ``Action``.

        Handles malformed JSON gracefully by wrapping the raw content in a
        ``final_answer`` action so the agent never crashes on unexpected
        output.

        Args:
            content: The raw string content from the assistant message.

        Returns:
            A parsed ``Action`` instance.
        """
        data = extract_json(content)
        if data is None:
            logger.warning("LLM returned non-JSON content, treating as final answer")
            return Action(
                type="final_answer",
                reasoning="(could not parse LLM output as JSON)",
                answer=content,
            )

        action_type = data.get("type", "final_answer")
        reasoning = data.get("reasoning", "")

        if action_type == "tool_call":
            return Action(
                type="tool_call",
                reasoning=reasoning,
                tool_name=data.get("tool_name"),
                tool_args=data.get("tool_args") or {},
            )

        # Default to final_answer for any unrecognised type.
        return Action(
            type="final_answer",
            reasoning=reasoning,
            answer=data.get("answer", content),
        )

    async def _execute_tool_call(self, action: Action) -> StepResult:
        """Look up and execute a tool, returning a ``StepResult``.

        When a ``HookRegistry`` is configured, PRE_TOOL_USE hooks run
        before the tool and POST_TOOL_USE hooks run after.  PRE hooks
        can block the call or modify args; POST hooks can modify the result.

        Args:
            action: A ``tool_call`` action specifying the tool name and args.

        Returns:
            A ``StepResult`` with either an observation or an error.
        """
        from fim_one.core.tool.base import ToolResult

        tool_name = action.tool_name or ""
        tool_args = dict(action.tool_args or {})

        # --- PRE_TOOL_USE hooks ---
        if self._hook_registry is not None:
            pre_ctx = HookContext(
                hook_point=HookPoint.PRE_TOOL_USE,
                tool_name=tool_name,
                tool_args=tool_args,
            )
            pre_result = await self._hook_registry.run_pre_tool(pre_ctx)
            if not pre_result.allow:
                error_msg = pre_result.error or "Tool call blocked by hook"
                logger.info("Hook blocked tool '%s': %s", tool_name, error_msg)
                return StepResult(action=action, error=error_msg)
            if pre_result.modified_args is not None:
                tool_args = pre_result.modified_args

        tool = self._tools.get(tool_name)

        if tool is None:
            error_msg = (
                f"Unknown tool '{tool_name}'. "
                f"Available tools: {[t.name for t in self._tools.list_tools()]}"
            )
            logger.warning(error_msg)
            return StepResult(action=action, error=error_msg)

        try:
            raw_result: str | ToolResult = await tool.run(**tool_args)

            # Determine the observation string for POST hooks.
            if isinstance(raw_result, ToolResult):
                observation = raw_result.content
            else:
                observation = raw_result

            # --- POST_TOOL_USE hooks ---
            if self._hook_registry is not None:
                post_ctx = HookContext(
                    hook_point=HookPoint.POST_TOOL_USE,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    tool_result=observation,
                )
                post_result = await self._hook_registry.run_post_tool(post_ctx)
                if post_result.modified_result is not None:
                    observation = post_result.modified_result
                    if isinstance(raw_result, ToolResult):
                        raw_result = ToolResult(
                            content=observation,
                            content_type=raw_result.content_type,
                            artifacts=raw_result.artifacts,
                        )
                    else:
                        raw_result = observation

            if isinstance(raw_result, ToolResult):
                return StepResult(
                    action=action,
                    observation=raw_result.content,
                    content_type=raw_result.content_type,
                    artifacts=[
                        {"name": a.name, "path": a.path, "mime_type": a.mime_type, "size": a.size}
                        for a in raw_result.artifacts
                    ] if raw_result.artifacts else None,
                )
            return StepResult(action=action, observation=raw_result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.exception("Tool '%s' raised an exception", tool_name)
            return StepResult(action=action, error=error_msg)

    async def _save_to_memory(self, query: str, answer: str) -> None:
        """Save the user query and final answer to memory, if configured.

        Args:
            query: The original user query.
            answer: The agent's final answer.
        """
        if self._memory is None:
            return
        await self._memory.add_message(ChatMessage(role="user", content=query))
        await self._memory.add_message(
            ChatMessage(role="assistant", content=answer),
        )

    async def _force_compact(
        self,
        messages: list[ChatMessage],
        target_ratio: float = 0.5,
        hint: str = "react_iteration",
    ) -> list[ChatMessage]:
        """Force-compact messages to a fraction of the context budget.

        Used by reactive compact (I.9) when a context overflow exception is
        caught.  If no ``ContextGuard`` is configured, falls back to
        heuristic truncation via ``CompactUtils.smart_truncate``.

        Args:
            messages: The current message list.
            target_ratio: Target budget as a fraction of the default budget
                (e.g. 0.5 means 50%).
            hint: Compact prompt variant.

        Returns:
            A compacted message list.
        """
        from fim_one.core.memory.compact import CompactUtils

        if self._context_guard is not None:
            target_budget = int(self._context_guard._default_budget * target_ratio)
            return await self._context_guard.check_and_compact(
                messages, budget=target_budget, hint=hint,
            )

        # No context guard — use heuristic truncation with a conservative
        # estimate (assume 32k default budget).
        target_budget = int(32000 * target_ratio)
        return CompactUtils.smart_truncate(messages, target_budget)

    @staticmethod
    def _summarise_steps(steps: list[StepResult]) -> str:
        """Produce a short textual summary of the steps taken so far.

        Args:
            steps: The list of completed steps.

        Returns:
            A newline-separated summary string.
        """
        lines: list[str] = []
        for i, step in enumerate(steps, 1):
            if step.action.type == "tool_call":
                status = "OK" if step.error is None else f"ERROR: {step.error}"
                lines.append(
                    f"  Step {i}: called {step.action.tool_name} -> {status}"
                )
            else:
                lines.append(f"  Step {i}: final answer")
        return "\n".join(lines) if lines else "(no steps taken)"

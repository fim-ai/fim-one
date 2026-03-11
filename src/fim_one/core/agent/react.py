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
import json
import logging
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from typing import Any

from fim_one.core.memory.base import BaseMemory
from fim_one.core.memory.context_guard import ContextGuard
from fim_one.core.model import BaseLLM, ChatMessage, LLMResult
from fim_one.core.model.structured import structured_llm_call
from fim_one.core.model.types import ToolCallRequest
from fim_one.core.model.usage import UsageTracker
from fim_one.core.tool import ToolRegistry
from fim_one.core.utils import extract_json

from .types import Action, AgentResult, StepResult

# Callback invoked after each ReAct iteration.
# Signature: (iteration, action, observation, error, step_result)
IterationCallback = Callable[[int, Action, str | None, str | None, "StepResult | None"], Any]

logger = logging.getLogger(__name__)

# When the total number of available tools exceeds this threshold, the agent
# runs a lightweight "selection" LLM call first to pick the most relevant
# tools for the current query.  This avoids injecting dozens of full schemas
# into the main conversation context.
TOOL_SELECTION_THRESHOLD = 12

# Maximum number of tools the selection phase may pick.
_TOOL_SELECTION_MAX = 6

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
You are an intelligent assistant that solves tasks by reasoning step-by-step \
and using tools when necessary.

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
  "answer": "done"
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
- IMPORTANT: The "answer" field MUST be literally "done". Do NOT write any \
answer content — a separate synthesis step will produce the full answer.
- Do NOT generate charts, plots, or images (e.g. matplotlib) unless the user \
explicitly asks for visualisation. Prefer text tables and formatted output.
- LANGUAGE: Always respond in the same language as the user's query. If the \
user writes in Chinese, your reasoning and final_answer must be in Chinese. \
If the user writes in English, respond in English. Match the user's language.
- CRITICAL: Your ENTIRE response must be a single JSON object. No markdown, no plain text, no code fences.
"""

_NATIVE_TOOLS_SYSTEM_PROMPT_TEMPLATE = """\
You are an intelligent assistant that solves tasks by reasoning step-by-step \
and using tools when necessary.

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
respond with just a brief text (e.g. "done"). Do NOT write any answer content \
— a separate synthesis step will produce the full answer.
- LANGUAGE: Always respond in the same language as the user's query. If the \
user writes in Chinese, respond in Chinese. If in English, respond in English.
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
    """

    def __init__(
        self,
        llm: BaseLLM,
        tools: ToolRegistry,
        system_prompt: str | None = None,
        extra_instructions: str | None = None,
        max_iterations: int = 50,
        use_native_tools: bool = True,
        memory: BaseMemory | None = None,
        context_guard: ContextGuard | None = None,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._system_prompt_override = system_prompt
        self._extra_instructions = extra_instructions
        self._max_iterations = max_iterations
        self._use_native_tools = use_native_tools
        self._memory = memory
        self._context_guard = context_guard

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
    def _native_mode_active(self) -> bool:
        """Whether native function-calling mode is currently active."""
        return (
            self._use_native_tools
            and self._llm.abilities.get("tool_call", False)
        )

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

        Mirrors ``PlanAnalyzer.stream_synthesize()`` — tool iterations use
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
                # Truncate very long tool results to keep prompt lean.
                if len(obs) > 2000:
                    obs = obs[:2000] + "... (truncated)"
                context_parts.append(f"Tool result: {obs}")
            elif msg.role == "assistant" and msg.content:
                # Final iteration content (the brief/fallback answer).
                context_parts.append(f"Assistant reasoning: {msg.content}")

        system_parts = [
            "You synthesize a final answer from ReAct agent execution results. "
            "Provide a concise, coherent response that addresses the original "
            "question. Do NOT include meta-commentary like 'based on the results' "
            "or 'according to the tool output' — just answer directly.",
            "",
            "Guidelines:",
            "- Present key results clearly; use markdown formatting when helpful.",
            "- LANGUAGE: The answer must be in the same language as the original "
            "question. If the question is in Chinese, respond in Chinese.",
        ]
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
            call_result = await structured_llm_call(
                self._llm,
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

        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=self._build_system_prompt(tools=tools)),
        ]

        # Load history from memory.
        if self._memory is not None:
            history = await self._memory.get_messages()
            for msg in history:
                if msg.role != "system":
                    messages.append(msg)

        # Build user message — use vision content array when images are attached.
        user_content: str | list[dict[str, Any]] = query
        if image_urls:
            user_content = ChatMessage.build_vision_content(query, image_urls)
        messages.append(ChatMessage(role="user", content=user_content, pinned=True))

        steps: list[StepResult] = []
        response_format = self._json_response_format()

        for iteration in range(1, self._max_iterations + 1):
            logger.debug("ReAct iteration %d", iteration)

            # Signal thinking start before LLM call.
            if on_iteration is not None:
                on_iteration(
                    iteration,
                    Action(type="thinking", reasoning=""),
                    None, None, None,
                )

            if self._context_guard is not None:
                messages = await self._context_guard.check_and_compact(
                    messages, hint="react_iteration",
                )

            result: LLMResult = await self._llm.chat(
                messages,
                response_format=response_format,
            )
            await usage_tracker.record(result.usage)

            assistant_content = result.message.content or ""
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
                        "Deferring final answer — %d injected message(s) "
                        "pending (iteration %d)",
                        len(injected_msgs),
                        iteration,
                    )
                    continue

                steps.append(StepResult(action=action))
                if on_iteration is not None:
                    on_iteration(iteration, action, None, None, None)
                answer = action.answer or ""
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

        messages: list[ChatMessage] = [
            ChatMessage(
                role="system",
                content=self._build_system_prompt_native(),
            ),
        ]

        # Load history from memory.
        if self._memory is not None:
            history = await self._memory.get_messages()
            for msg in history:
                if msg.role != "system":
                    messages.append(msg)

        # Build user message — use vision content array when images are attached.
        user_content: str | list[dict[str, Any]] = query
        if image_urls:
            user_content = ChatMessage.build_vision_content(query, image_urls)
        messages.append(ChatMessage(role="user", content=user_content, pinned=True))

        steps: list[StepResult] = []

        # Build OpenAI-format tool definitions using the effective (possibly
        # filtered) tool set for context efficiency.
        tools_payload = self._build_tools_payload(tools=effective_tools)
        tool_choice: str | None = "auto" if tools_payload else None

        for iteration in range(1, self._max_iterations + 1):
            logger.debug("Native ReAct iteration %d", iteration)

            # Signal thinking start before LLM call.
            if on_iteration is not None:
                on_iteration(
                    iteration,
                    Action(type="thinking", reasoning=""),
                    None, None, None,
                )

            if self._context_guard is not None:
                messages = await self._context_guard.check_and_compact(
                    messages, hint="react_iteration",
                )

            # Use non-streaming chat() for all iterations — fast tool loops.
            # The final answer is streamed separately via stream_answer().
            result: LLMResult = await self._llm.chat(
                messages,
                tools=tools_payload,
                tool_choice=tool_choice,
            )
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
                messages.extend(tool_results)

                # Now safe to drain — tool_use/tool_result pairing is intact.
                injected_msgs = (await interrupt_queue.drain()) if interrupt_queue is not None else []
                self._emit_and_append_injections(
                    injected_msgs, messages, iteration, on_iteration,
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

            answer = assistant_msg.content or ""
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

            action = Action(
                type="tool_call",
                reasoning="",
                tool_name=tc.name,
                tool_args=tc.arguments,
            )

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
                raw_result = await tool.run(**tc.arguments)
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
                    llm_content = raw_result.content
                    if raw_result.content_type in ("html", "markdown") and raw_result.artifacts:
                        names = ", ".join(a.name for a in raw_result.artifacts)
                        llm_content = (
                            f"[Artifact generated: {names}] "
                            "The content is rendered as a preview in the UI "
                            "and available for download. "
                            "Do NOT paste the raw source in your answer."
                        )
                    msg = ChatMessage(
                        role="tool",
                        content=llm_content,
                        tool_call_id=tc.id,
                    )
                    return step, msg
                step = StepResult(action=action, observation=raw_result)
                msg = ChatMessage(
                    role="tool",
                    content=raw_result,
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
        injected_msgs: list,
        messages: list[ChatMessage],
        iteration: int,
        on_iteration: IterationCallback | None,
    ) -> None:
        """Emit SSE inject events and append a combined user message.

        This is shared by both ``_run_json`` and ``_run_native`` to avoid
        duplicating the drain → emit → append logic.
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

        now_dt = datetime.now(timezone.utc)
        now = now_dt.strftime("%Y-%m-%d %H:%M UTC")
        tool_descriptions = self._format_tool_descriptions(tools=tools)
        prompt = _SYSTEM_PROMPT_TEMPLATE.format(
            tool_descriptions=tool_descriptions,
            current_datetime=now,
            current_year=now_dt.year,
        )
        if self._extra_instructions:
            prompt += f"\n\nAdditional instructions:\n{self._extra_instructions}"
        return prompt

    def _build_system_prompt_native(self) -> str:
        """Build the system prompt for native function-calling mode.

        Returns:
            The system prompt string (tool descriptions are passed via the
            ``tools`` parameter instead of being embedded in the prompt).
        """
        if self._system_prompt_override is not None:
            return self._system_prompt_override

        now_dt = datetime.now(timezone.utc)
        now = now_dt.strftime("%Y-%m-%d %H:%M UTC")
        prompt = _NATIVE_TOOLS_SYSTEM_PROMPT_TEMPLATE.format(
            current_datetime=now,
            current_year=now_dt.year,
        )
        if self._extra_instructions:
            prompt += f"\n\nAdditional instructions:\n{self._extra_instructions}"
        return prompt

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
        if self._llm.abilities.get("json_mode", False):
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

        Args:
            action: A ``tool_call`` action specifying the tool name and args.

        Returns:
            A ``StepResult`` with either an observation or an error.
        """
        from fim_one.core.tool.base import ToolResult

        tool_name = action.tool_name or ""
        tool = self._tools.get(tool_name)

        if tool is None:
            error_msg = (
                f"Unknown tool '{tool_name}'. "
                f"Available tools: {[t.name for t in self._tools.list_tools()]}"
            )
            logger.warning(error_msg)
            return StepResult(action=action, error=error_msg)

        try:
            raw_result = await tool.run(**(action.tool_args or {}))
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

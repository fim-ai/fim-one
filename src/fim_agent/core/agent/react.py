"""ReAct (Reasoning + Acting) agent implementation.

This module provides a ``ReActAgent`` that uses structured JSON output from an
LLM to drive an iterative tool-use loop.  It also supports an optional *native*
function-calling mode where the LLM produces ``tool_calls`` directly (OpenAI
style) instead of emitting JSON action objects.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from fim_agent.core.memory.base import BaseMemory
from fim_agent.core.model import BaseLLM, ChatMessage, LLMResult
from fim_agent.core.model.types import ToolCallRequest
from fim_agent.core.model.usage import UsageTracker
from fim_agent.core.tool import ToolRegistry
from fim_agent.core.utils import extract_json

from .types import Action, AgentResult, StepResult

# Callback invoked after each ReAct iteration.
# Signature: (iteration, action, observation_or_error)
IterationCallback = Callable[[int, Action, str | None, str | None], Any]

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_TEMPLATE = """\
You are an intelligent assistant that solves tasks by reasoning step-by-step \
and using tools when necessary.

You MUST respond with a single JSON object (no markdown, no extra text) in \
one of the following two formats:

1. To call a tool:
{{
  "type": "tool_call",
  "reasoning": "<your step-by-step reasoning>",
  "tool_name": "<name of the tool>",
  "tool_args": {{<arguments as key-value pairs>}}
}}

2. To give the final answer:
{{
  "type": "final_answer",
  "reasoning": "<your step-by-step reasoning>",
  "answer": "<your final answer>"
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
- Keep your final_answer concise and focused -- present key results, not \
lengthy commentary or step-by-step narration of what you did.
- LANGUAGE: Always respond in the same language as the user's query. If the \
user writes in Chinese, your reasoning and final_answer must be in Chinese. \
If the user writes in English, respond in English. Match the user's language.
"""

_NATIVE_TOOLS_SYSTEM_PROMPT_TEMPLATE = """\
You are an intelligent assistant that solves tasks by reasoning step-by-step \
and using tools when necessary.

Guidelines:
- Always think carefully before acting.
- Use tools only when the task requires external information or computation.
- When you have enough information, respond with a direct textual answer.
- If a tool call fails, analyse the error and decide whether to retry with \
different arguments or respond with the information you have.
- Be EFFICIENT: try to accomplish as much as possible in each tool call. \
Write a single comprehensive script rather than making many small calls.
- Keep your answers concise and focused -- present key results, not lengthy \
commentary or step-by-step narration of what you did.
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
        max_iterations: Safety limit on reasoning iterations.
        use_native_tools: Whether to prefer the LLM's native function-calling
            interface.  The feature is only activated when the LLM also
            advertises ``tool_call`` capability.
        memory: Optional conversation memory for multi-turn sessions.
    """

    def __init__(
        self,
        llm: BaseLLM,
        tools: ToolRegistry,
        system_prompt: str | None = None,
        max_iterations: int = 50,
        use_native_tools: bool = False,
        memory: BaseMemory | None = None,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._system_prompt_override = system_prompt
        self._max_iterations = max_iterations
        self._use_native_tools = use_native_tools
        self._memory = memory

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
    ) -> AgentResult:
        """Execute the ReAct loop for a given user query.

        Args:
            query: The user question or task description.
            on_iteration: Optional callback invoked after each iteration with
                ``(iteration, action, observation, error)``.

        Returns:
            An ``AgentResult`` containing the final answer and full step trace.
        """
        if self._native_mode_active:
            return await self._run_native(query, on_iteration)
        return await self._run_json(query, on_iteration)

    # ------------------------------------------------------------------
    # JSON mode (original ReAct loop)
    # ------------------------------------------------------------------

    async def _run_json(
        self,
        query: str,
        on_iteration: IterationCallback | None = None,
    ) -> AgentResult:
        """Execute the JSON-based ReAct loop."""
        usage_tracker = UsageTracker()

        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=self._build_system_prompt()),
        ]

        # Load history from memory.
        if self._memory is not None:
            history = await self._memory.get_messages()
            for msg in history:
                if msg.role != "system":
                    messages.append(msg)

        messages.append(ChatMessage(role="user", content=query))

        steps: list[StepResult] = []
        response_format = self._json_response_format()

        for iteration in range(1, self._max_iterations + 1):
            logger.debug("ReAct iteration %d", iteration)

            result: LLMResult = await self._llm.chat(
                messages,
                response_format=response_format,
            )
            await usage_tracker.record(result.usage)

            assistant_content = result.message.content or ""
            action = self._parse_action(assistant_content)

            # Append the raw assistant reply to the conversation.
            messages.append(
                ChatMessage(role="assistant", content=assistant_content),
            )

            # -- Final answer path --
            if action.type == "final_answer":
                steps.append(StepResult(action=action))
                if on_iteration is not None:
                    on_iteration(iteration, action, None, None)
                answer = action.answer or ""
                await self._save_to_memory(query, answer)
                return AgentResult(
                    answer=answer,
                    steps=steps,
                    iterations=iteration,
                    usage=usage_tracker.get_summary(),
                )

            # -- Tool call path --
            step = await self._execute_tool_call(action)
            steps.append(step)

            if on_iteration is not None:
                on_iteration(iteration, action, step.observation, step.error)

            # Feed the observation (or error) back as a user message so the
            # LLM can reason about the result in the next iteration.
            if step.error is not None:
                observation_text = (
                    f"Tool `{action.tool_name}` raised an error:\n{step.error}"
                )
            else:
                observation_text = (
                    f"Tool `{action.tool_name}` returned:\n{step.observation}"
                )

            messages.append(
                ChatMessage(role="user", content=observation_text),
            )

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
        )

    # ------------------------------------------------------------------
    # Native function-calling mode
    # ------------------------------------------------------------------

    async def _run_native(
        self,
        query: str,
        on_iteration: IterationCallback | None = None,
    ) -> AgentResult:
        """Execute the native function-calling loop."""
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

        messages.append(ChatMessage(role="user", content=query))

        steps: list[StepResult] = []

        # Build OpenAI-format tool definitions.
        tools_payload = self._build_tools_payload()
        tool_choice: str | None = "auto" if tools_payload else None

        for iteration in range(1, self._max_iterations + 1):
            logger.debug("Native ReAct iteration %d", iteration)

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
            if assistant_msg.tool_calls:
                tool_results = await self._execute_native_tool_calls(
                    assistant_msg.tool_calls,
                    iteration,
                    steps,
                    on_iteration,
                )
                messages.extend(tool_results)
                continue

            # -- Final answer path (no tool calls) --
            answer = assistant_msg.content or ""
            action = Action(
                type="final_answer",
                reasoning="",
                answer=answer,
            )
            steps.append(StepResult(action=action))
            if on_iteration is not None:
                on_iteration(iteration, action, None, None)
            await self._save_to_memory(query, answer)
            return AgentResult(
                answer=answer,
                steps=steps,
                iterations=iteration,
                usage=usage_tracker.get_summary(),
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
        )

    async def _execute_native_tool_calls(
        self,
        tool_calls: list[ToolCallRequest],
        iteration: int,
        steps: list[StepResult],
        on_iteration: IterationCallback | None,
    ) -> list[ChatMessage]:
        """Execute one or more native tool calls in parallel.

        Args:
            tool_calls: The tool call requests from the assistant message.
            iteration: The current iteration number.
            steps: The running list of step results (mutated in-place).
            on_iteration: Optional callback.

        Returns:
            A list of ``ChatMessage`` objects with role ``"tool"`` to append
            to the conversation.
        """
        async def _run_single(tc: ToolCallRequest) -> tuple[StepResult, ChatMessage]:
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
                observation = await tool.run(**tc.arguments)
                step = StepResult(action=action, observation=observation)
                msg = ChatMessage(
                    role="tool",
                    content=observation,
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
                )

        return tool_messages

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        """Build the system prompt, including descriptions of available tools.

        Returns:
            The full system prompt string.
        """
        if self._system_prompt_override is not None:
            return self._system_prompt_override

        tool_descriptions = self._format_tool_descriptions()
        return _SYSTEM_PROMPT_TEMPLATE.format(tool_descriptions=tool_descriptions)

    def _build_system_prompt_native(self) -> str:
        """Build the system prompt for native function-calling mode.

        Returns:
            The system prompt string (tool descriptions are passed via the
            ``tools`` parameter instead of being embedded in the prompt).
        """
        if self._system_prompt_override is not None:
            return self._system_prompt_override

        return _NATIVE_TOOLS_SYSTEM_PROMPT_TEMPLATE

    def _format_tool_descriptions(self) -> str:
        """Format tool descriptions for inclusion in the system prompt.

        Returns:
            A human-readable listing of every registered tool with its
            name, description, and parameter schema.
        """
        tools = self._tools.list_tools()
        if not tools:
            return "(no tools available)"

        lines: list[str] = []
        for tool in tools:
            schema_str = json.dumps(tool.parameters_schema, indent=2)
            lines.append(
                f"- **{tool.name}**: {tool.description}\n"
                f"  Parameters: {schema_str}"
            )
        return "\n".join(lines)

    def _build_tools_payload(self) -> list[dict[str, Any]] | None:
        """Build OpenAI-format tool definitions for native mode.

        Returns:
            A list of tool definition dicts, or ``None`` if no tools are
            registered.
        """
        tools = self._tools.list_tools()
        if not tools:
            return None

        payload: list[dict[str, Any]] = []
        for tool in tools:
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
            observation = await tool.run(**(action.tool_args or {}))
            return StepResult(action=action, observation=observation)
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

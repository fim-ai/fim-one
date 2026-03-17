"""Tests for the ReAct mid-loop self-reflection feature.

The self-reflection mechanism injects a lightweight goal-check user message
every ``_SELF_REFLECTION_INTERVAL`` (6) tool-call iterations.  This prevents
goal drift in long reasoning chains.

Tests cover both JSON mode (_run_json) and native function-calling mode
(_run_native).
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import pytest

from fim_one.core.agent import ReActAgent
from fim_one.core.agent.react import _SELF_REFLECTION_INTERVAL, _SELF_REFLECTION_PROMPT
from fim_one.core.model import BaseLLM, ChatMessage, LLMResult, StreamChunk
from fim_one.core.model.types import ToolCallRequest
from fim_one.core.tool import BaseTool, ToolRegistry

from .conftest import EchoTool


# ======================================================================
# Helpers -- LLMs that capture messages for inspection
# ======================================================================


class CapturingFakeLLM(BaseLLM):
    """FakeLLM that records all message lists passed to ``chat()``.

    Used for JSON-mode tests where we need to inspect what messages the
    agent sends to the LLM on each call.
    """

    def __init__(self, responses: list[LLMResult]) -> None:
        self._responses = responses
        self._call_count = 0
        self.all_messages: list[list[ChatMessage]] = []

    @property
    def call_count(self) -> int:
        return self._call_count

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        reasoning_effort: Any = None,
    ) -> LLMResult:
        self.all_messages.append(list(messages))
        idx = min(self._call_count, len(self._responses) - 1)
        self._call_count += 1
        return self._responses[idx]

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(delta_content="fake", finish_reason="stop")

    @property
    def abilities(self) -> dict[str, bool]:
        return {
            "tool_call": False,
            "json_mode": False,
            "vision": False,
            "streaming": False,
        }


class CapturingNativeFakeLLM(BaseLLM):
    """FakeLLM with native tool_call capability that records messages.

    Used for native-mode tests where the agent uses ``_run_native()``.
    """

    def __init__(self, responses: list[LLMResult]) -> None:
        self._responses = responses
        self._call_count = 0
        self.all_messages: list[list[ChatMessage]] = []

    @property
    def call_count(self) -> int:
        return self._call_count

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        reasoning_effort: Any = None,
    ) -> LLMResult:
        self.all_messages.append(list(messages))
        idx = min(self._call_count, len(self._responses) - 1)
        self._call_count += 1
        return self._responses[idx]

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        self.all_messages.append(list(messages))
        idx = min(self._call_count, len(self._responses) - 1)
        self._call_count += 1
        resp = self._responses[idx]
        if resp.message.content:
            yield StreamChunk(delta_content=resp.message.content)
        if resp.message.tool_calls:
            yield StreamChunk(
                tool_calls=resp.message.tool_calls,
                finish_reason="tool_calls",
                usage=resp.usage or None,
            )
        else:
            yield StreamChunk(finish_reason="stop", usage=resp.usage or None)

    @property
    def abilities(self) -> dict[str, bool]:
        return {
            "tool_call": True,
            "json_mode": True,
            "vision": False,
            "streaming": True,
        }


# ======================================================================
# Response builders
# ======================================================================


def _json_tool_call(
    tool_name: str = "echo",
    tool_args: dict[str, Any] | None = None,
    reasoning: str = "calling tool",
) -> LLMResult:
    """Create an LLMResult whose content is a tool_call JSON action."""
    return LLMResult(
        message=ChatMessage(
            role="assistant",
            content=json.dumps({
                "type": "tool_call",
                "reasoning": reasoning,
                "tool_name": tool_name,
                "tool_args": tool_args or {"text": "ok"},
            }),
        ),
    )


def _json_final_answer(answer: str = "done") -> LLMResult:
    """Create an LLMResult whose content is a final_answer JSON action."""
    return LLMResult(
        message=ChatMessage(
            role="assistant",
            content=json.dumps({
                "type": "final_answer",
                "reasoning": "finished",
                "answer": answer,
            }),
        ),
    )


def _native_tool_call(
    calls: list[tuple[str, str, dict[str, Any]]],
    content: str | None = None,
) -> LLMResult:
    """Create an LLMResult with native tool_calls."""
    tool_calls = [
        ToolCallRequest(id=tc_id, name=name, arguments=args)
        for tc_id, name, args in calls
    ]
    return LLMResult(
        message=ChatMessage(
            role="assistant",
            content=content,
            tool_calls=tool_calls,
        ),
    )


def _native_final_answer(content: str = "done") -> LLMResult:
    """Create an LLMResult that represents a final answer (no tool_calls)."""
    return LLMResult(
        message=ChatMessage(role="assistant", content=content),
    )


# ======================================================================
# Helpers for extracting reflection messages
# ======================================================================


def _find_reflection_messages(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Return all user messages that contain the self-reflection marker."""
    return [
        m for m in messages
        if m.role == "user" and m.content and "[Self-check]" in str(m.content)
    ]


# ======================================================================
# Tests -- JSON mode (_run_json)
# ======================================================================


class TestSelfReflectionJsonMode:
    """Self-reflection injection in JSON mode (tool_call=False LLM)."""

    async def test_no_reflection_for_short_runs(self) -> None:
        """When tool calls < 6, no reflection message should be injected."""
        # 3 tool calls then a final answer = 4 LLM calls total.
        responses = [_json_tool_call() for _ in range(3)]
        responses.append(_json_final_answer())

        llm = CapturingFakeLLM(responses)
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(llm=llm, tools=registry, max_iterations=10)

        result = await agent.run("short task")

        assert result.answer == "done"

        # Inspect all messages sent to the LLM across all calls.
        for call_messages in llm.all_messages:
            reflections = _find_reflection_messages(call_messages)
            assert len(reflections) == 0, (
                "No self-reflection should be injected with only 3 tool calls"
            )

    async def test_no_reflection_at_5_tool_calls(self) -> None:
        """Exactly 5 tool calls (just under threshold) should not trigger reflection."""
        responses = [_json_tool_call() for _ in range(5)]
        responses.append(_json_final_answer())

        llm = CapturingFakeLLM(responses)
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(llm=llm, tools=registry, max_iterations=10)

        result = await agent.run("almost there")

        assert result.answer == "done"

        for call_messages in llm.all_messages:
            reflections = _find_reflection_messages(call_messages)
            assert len(reflections) == 0

    async def test_reflection_injected_at_iteration_6(self) -> None:
        """After exactly 6 tool calls, one self-reflection message should appear."""
        # 6 tool calls then a final answer = 7 LLM calls total.
        responses = [_json_tool_call() for _ in range(6)]
        responses.append(_json_final_answer())

        llm = CapturingFakeLLM(responses)
        registry = ToolRegistry()
        registry.register(EchoTool())
        # max_iterations must be > 7 so the reflection condition
        # `iteration < self._max_iterations` is satisfied at tool call #6.
        agent = ReActAgent(llm=llm, tools=registry, max_iterations=15)

        result = await agent.run("find the answer to everything")

        assert result.answer == "done"

        # The 7th LLM call (after 6 tool calls) should have the reflection
        # message in its conversation history.
        last_call_messages = llm.all_messages[-1]
        reflections = _find_reflection_messages(last_call_messages)
        assert len(reflections) == 1, (
            f"Expected exactly 1 reflection after 6 tool calls, "
            f"found {len(reflections)}"
        )

    async def test_reflection_injected_at_iteration_12(self) -> None:
        """After 12 tool calls, two self-reflection messages should appear."""
        responses = [_json_tool_call() for _ in range(12)]
        responses.append(_json_final_answer())

        llm = CapturingFakeLLM(responses)
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(llm=llm, tools=registry, max_iterations=20)

        result = await agent.run("long investigation")

        assert result.answer == "done"

        # The last LLM call should contain both reflection messages in history.
        last_call_messages = llm.all_messages[-1]
        reflections = _find_reflection_messages(last_call_messages)
        assert len(reflections) == 2, (
            f"Expected 2 reflections after 12 tool calls, "
            f"found {len(reflections)}"
        )

    async def test_reflection_contains_original_goal(self) -> None:
        """The injected reflection message should contain the original query text."""
        original_query = "analyze the quarterly revenue report for Q3"
        responses = [_json_tool_call() for _ in range(6)]
        responses.append(_json_final_answer())

        llm = CapturingFakeLLM(responses)
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(llm=llm, tools=registry, max_iterations=15)

        await agent.run(original_query)

        last_call_messages = llm.all_messages[-1]
        reflections = _find_reflection_messages(last_call_messages)
        assert len(reflections) == 1

        reflection_text = str(reflections[0].content)
        assert original_query in reflection_text, (
            f"Reflection should contain the original goal. "
            f"Got: {reflection_text}"
        )

    async def test_reflection_not_injected_at_max_iteration_boundary(self) -> None:
        """Reflection should NOT be injected when iteration == max_iterations.

        The condition requires ``iteration < self._max_iterations`` to avoid
        injecting a reflection right before the agent would stop anyway.
        With max_iterations=6, the 6th tool call occurs at iteration 6
        (the last allowed), so the reflection is skipped.
        """
        # All 6 iterations are tool calls; the agent hits the max_iterations
        # limit and produces a timeout answer.  The FakeLLM reuses the last
        # response, so it keeps returning tool calls.
        llm = CapturingFakeLLM([_json_tool_call()])
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(llm=llm, tools=registry, max_iterations=6)

        result = await agent.run("edge case")

        # Agent should have hit the max iterations cap.
        assert result.iterations == 6

        # Even though we had 6 tool calls (tool_call_count % 6 == 0),
        # reflection is skipped because iteration == max_iterations.
        for call_messages in llm.all_messages:
            reflections = _find_reflection_messages(call_messages)
            assert len(reflections) == 0, (
                "Reflection should not be injected at the max iteration boundary"
            )


# ======================================================================
# Tests -- Native function-calling mode (_run_native)
# ======================================================================


class TestSelfReflectionNativeMode:
    """Self-reflection injection in native function-calling mode."""

    async def test_no_reflection_for_short_runs_native(self) -> None:
        """When tool calls < 6, no reflection message should be injected."""
        responses = [
            _native_tool_call([(f"c{i}", "echo", {"text": "ok"})]) for i in range(3)
        ]
        responses.append(_native_final_answer())

        llm = CapturingNativeFakeLLM(responses)
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, use_native_tools=True, max_iterations=10,
        )

        result = await agent.run("short native task")

        assert result.answer == "done"

        for call_messages in llm.all_messages:
            reflections = _find_reflection_messages(call_messages)
            assert len(reflections) == 0

    async def test_reflection_injected_at_iteration_6_native(self) -> None:
        """After 6 tool-call rounds, one reflection should appear in native mode."""
        responses = [
            _native_tool_call([(f"c{i}", "echo", {"text": "ok"})]) for i in range(6)
        ]
        responses.append(_native_final_answer())

        llm = CapturingNativeFakeLLM(responses)
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, use_native_tools=True, max_iterations=15,
        )

        result = await agent.run("native long task")

        assert result.answer == "done"

        last_call_messages = llm.all_messages[-1]
        reflections = _find_reflection_messages(last_call_messages)
        assert len(reflections) == 1

    async def test_reflection_injected_at_iteration_12_native(self) -> None:
        """After 12 tool-call rounds, two reflections should appear in native mode."""
        responses = [
            _native_tool_call([(f"c{i}", "echo", {"text": "ok"})]) for i in range(12)
        ]
        responses.append(_native_final_answer())

        llm = CapturingNativeFakeLLM(responses)
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, use_native_tools=True, max_iterations=20,
        )

        result = await agent.run("very long native task")

        assert result.answer == "done"

        last_call_messages = llm.all_messages[-1]
        reflections = _find_reflection_messages(last_call_messages)
        assert len(reflections) == 2

    async def test_reflection_contains_original_goal_native(self) -> None:
        """The injected reflection in native mode should contain the original query."""
        original_query = "deploy the production build to staging environment"
        responses = [
            _native_tool_call([(f"c{i}", "echo", {"text": "ok"})]) for i in range(6)
        ]
        responses.append(_native_final_answer())

        llm = CapturingNativeFakeLLM(responses)
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, use_native_tools=True, max_iterations=15,
        )

        await agent.run(original_query)

        last_call_messages = llm.all_messages[-1]
        reflections = _find_reflection_messages(last_call_messages)
        assert len(reflections) == 1

        reflection_text = str(reflections[0].content)
        assert original_query in reflection_text


# ======================================================================
# Tests -- Constants and prompt template
# ======================================================================


class TestSelfReflectionConstants:
    """Verify the self-reflection constants and prompt template."""

    def test_interval_is_6(self) -> None:
        """The self-reflection interval should be 6."""
        assert _SELF_REFLECTION_INTERVAL == 6

    def test_prompt_template_has_placeholders(self) -> None:
        """The prompt template must contain {iteration} and {goal} placeholders."""
        assert "{iteration}" in _SELF_REFLECTION_PROMPT
        assert "{goal}" in _SELF_REFLECTION_PROMPT

    def test_prompt_template_renders_correctly(self) -> None:
        """The prompt template should render without errors."""
        rendered = _SELF_REFLECTION_PROMPT.format(
            iteration=6,
            goal="find the answer",
        )
        assert "[Self-check]" in rendered
        assert "6" in rendered
        assert "find the answer" in rendered
        assert "on track" in rendered

    def test_prompt_contains_key_reflection_questions(self) -> None:
        """The prompt should ask about goal tracking and circular behavior."""
        assert "on track" in _SELF_REFLECTION_PROMPT
        assert "circles" in _SELF_REFLECTION_PROMPT or "repeating" in _SELF_REFLECTION_PROMPT
        assert "final answer" in _SELF_REFLECTION_PROMPT

"""Tests for native function calling mode in the ReAct agent."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import pytest

from fim_agent.core.agent import ReActAgent
from fim_agent.core.agent.types import Action, StepResult
from fim_agent.core.model import BaseLLM, ChatMessage, LLMResult, StreamChunk
from fim_agent.core.model.types import ToolCallRequest
from fim_agent.core.tool import BaseTool, ToolRegistry

from .conftest import EchoTool, FakeLLM


# ======================================================================
# Helpers -- FakeLLM with native tool_call support
# ======================================================================


class NativeToolFakeLLM(BaseLLM):
    """A fake LLM that advertises ``tool_call`` capability.

    Returns pre-configured responses in sequence.  When the queue is
    exhausted the last response is reused.

    Attributes:
        received_tools: The ``tools`` argument from the most recent call.
        received_tool_choice: The ``tool_choice`` argument from the most
            recent call.
    """

    def __init__(self, responses: list[LLMResult] | None = None) -> None:
        self._responses: list[LLMResult] = responses or []
        self._call_count: int = 0
        self.received_tools: list[dict[str, Any]] | None = None
        self.received_tool_choice: str | dict[str, Any] | None = None
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
    ) -> LLMResult:
        self.received_tools = tools
        self.received_tool_choice = tool_choice
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
            "tool_call": True,
            "json_mode": True,
            "vision": False,
            "streaming": True,
        }


def _native_final_answer(content: str) -> LLMResult:
    """Create an ``LLMResult`` that represents a final answer (no tool_calls)."""
    return LLMResult(
        message=ChatMessage(role="assistant", content=content),
    )


def _native_tool_call(
    calls: list[tuple[str, str, dict[str, Any]]],
    content: str | None = None,
) -> LLMResult:
    """Create an ``LLMResult`` with one or more tool calls.

    Args:
        calls: List of ``(id, name, arguments)`` tuples.
        content: Optional textual content alongside tool calls.
    """
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


# ======================================================================
# A slow tool to verify parallel execution
# ======================================================================


class AddTool(BaseTool):
    """A tool that adds two numbers."""

    @property
    def name(self) -> str:
        return "add"

    @property
    def description(self) -> str:
        return "Adds two numbers."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        }

    async def run(self, **kwargs: Any) -> str:
        return str(kwargs.get("a", 0) + kwargs.get("b", 0))


class FailingTool(BaseTool):
    """A tool that always raises an exception."""

    @property
    def name(self) -> str:
        return "fail"

    @property
    def description(self) -> str:
        return "Always fails."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs: Any) -> str:
        raise RuntimeError("intentional failure")


# ======================================================================
# Tests
# ======================================================================


class TestNativeModeDetection:
    """Verify that native mode is activated (or not) based on config."""

    def test_native_mode_active_when_both_flag_and_ability(self) -> None:
        llm = NativeToolFakeLLM(responses=[])
        registry = ToolRegistry()
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)
        assert agent._native_mode_active is True

    def test_native_mode_inactive_when_flag_false(self) -> None:
        llm = NativeToolFakeLLM(responses=[])
        registry = ToolRegistry()
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=False)
        assert agent._native_mode_active is False

    def test_native_mode_inactive_when_llm_lacks_ability(self) -> None:
        llm = FakeLLM(responses=[])  # tool_call=False
        registry = ToolRegistry()
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)
        assert agent._native_mode_active is False

    def test_fallback_to_json_mode_when_llm_lacks_ability(self) -> None:
        """use_native_tools=True but LLM does not support tool_call."""
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(
                        role="assistant",
                        content=json.dumps({
                            "type": "final_answer",
                            "reasoning": "done",
                            "answer": "json fallback",
                        }),
                    ),
                ),
            ]
        )
        registry = ToolRegistry()
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)
        # Should still work -- falls back to JSON mode.
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(agent.run("test"))
        assert result.answer == "json fallback"


class TestNativeImmediateFinalAnswer:
    """LLM returns a final answer without making any tool calls."""

    async def test_simple_final_answer(self) -> None:
        llm = NativeToolFakeLLM(
            responses=[_native_final_answer("The answer is 42.")]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)

        result = await agent.run("What is 42?")

        assert result.answer == "The answer is 42."
        assert result.iterations == 1
        assert len(result.steps) == 1
        assert result.steps[0].action.type == "final_answer"

    async def test_tools_passed_to_llm(self) -> None:
        """Verify that OpenAI tool definitions are forwarded to the LLM."""
        llm = NativeToolFakeLLM(
            responses=[_native_final_answer("done")]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)

        await agent.run("test")

        assert llm.received_tools is not None
        assert len(llm.received_tools) == 1
        assert llm.received_tools[0]["function"]["name"] == "echo"

    async def test_tool_choice_auto(self) -> None:
        """Verify tool_choice='auto' is passed to the LLM."""
        llm = NativeToolFakeLLM(
            responses=[_native_final_answer("done")]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)

        await agent.run("test")

        assert llm.received_tool_choice == "auto"


class TestNativeSingleToolCall:
    """LLM makes a single tool call, then answers."""

    async def test_single_tool_call_then_answer(self) -> None:
        llm = NativeToolFakeLLM(
            responses=[
                _native_tool_call([("call-1", "echo", {"text": "ping"})]),
                _native_final_answer("Got: ping"),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)

        result = await agent.run("echo test")

        assert result.answer == "Got: ping"
        assert result.iterations == 2
        assert len(result.steps) == 2
        # First step: tool call
        assert result.steps[0].action.type == "tool_call"
        assert result.steps[0].action.tool_name == "echo"
        assert result.steps[0].observation == "ping"
        assert result.steps[0].error is None
        # Second step: final answer
        assert result.steps[1].action.type == "final_answer"

    async def test_tool_response_message_in_history(self) -> None:
        """Verify tool result is sent as role='tool' with correct tool_call_id."""
        llm = NativeToolFakeLLM(
            responses=[
                _native_tool_call([("call-abc", "echo", {"text": "hello"})]),
                _native_final_answer("done"),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)

        await agent.run("test")

        # The second LLM call should have the tool response in messages.
        second_call_messages = llm.all_messages[1]
        tool_msg = [m for m in second_call_messages if m.role == "tool"]
        assert len(tool_msg) == 1
        assert tool_msg[0].content == "hello"
        assert tool_msg[0].tool_call_id == "call-abc"


class TestNativeParallelToolCalls:
    """LLM requests multiple tool calls in a single response."""

    async def test_two_parallel_calls(self) -> None:
        llm = NativeToolFakeLLM(
            responses=[
                _native_tool_call([
                    ("call-1", "echo", {"text": "first"}),
                    ("call-2", "echo", {"text": "second"}),
                ]),
                _native_final_answer("Both done"),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)

        result = await agent.run("do two things")

        assert result.answer == "Both done"
        assert result.iterations == 2
        # Two tool call steps + one final answer step = 3
        assert len(result.steps) == 3
        assert result.steps[0].observation == "first"
        assert result.steps[1].observation == "second"
        assert result.steps[2].action.type == "final_answer"

    async def test_parallel_calls_messages_in_history(self) -> None:
        """Each parallel tool call should produce its own tool response message."""
        llm = NativeToolFakeLLM(
            responses=[
                _native_tool_call([
                    ("call-a", "echo", {"text": "aaa"}),
                    ("call-b", "add", {"a": 1, "b": 2}),
                ]),
                _native_final_answer("done"),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        registry.register(AddTool())
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)

        await agent.run("test parallel")

        second_call_messages = llm.all_messages[1]
        tool_msgs = [m for m in second_call_messages if m.role == "tool"]
        assert len(tool_msgs) == 2
        assert tool_msgs[0].tool_call_id == "call-a"
        assert tool_msgs[0].content == "aaa"
        assert tool_msgs[1].tool_call_id == "call-b"
        assert tool_msgs[1].content == "3"

    async def test_three_parallel_calls(self) -> None:
        """Three parallel tool calls should all execute."""
        llm = NativeToolFakeLLM(
            responses=[
                _native_tool_call([
                    ("c1", "echo", {"text": "a"}),
                    ("c2", "echo", {"text": "b"}),
                    ("c3", "echo", {"text": "c"}),
                ]),
                _native_final_answer("all done"),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)

        result = await agent.run("three things")

        assert result.answer == "all done"
        assert result.steps[0].observation == "a"
        assert result.steps[1].observation == "b"
        assert result.steps[2].observation == "c"


class TestNativeErrorHandling:
    """Error handling in native tool calling mode."""

    async def test_unknown_tool_produces_error(self) -> None:
        llm = NativeToolFakeLLM(
            responses=[
                _native_tool_call([("call-1", "nonexistent", {})]),
                _native_final_answer("fallback"),
            ]
        )
        registry = ToolRegistry()
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)

        result = await agent.run("bad tool")

        assert result.answer == "fallback"
        assert result.steps[0].error is not None
        assert "Unknown tool" in result.steps[0].error

    async def test_tool_exception_produces_error(self) -> None:
        llm = NativeToolFakeLLM(
            responses=[
                _native_tool_call([("call-1", "fail", {})]),
                _native_final_answer("recovered"),
            ]
        )
        registry = ToolRegistry()
        registry.register(FailingTool())
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)

        result = await agent.run("fail test")

        assert result.answer == "recovered"
        assert result.steps[0].error is not None
        assert "intentional failure" in result.steps[0].error

    async def test_error_message_sent_to_llm(self) -> None:
        """Tool error should appear in the tool response message."""
        llm = NativeToolFakeLLM(
            responses=[
                _native_tool_call([("call-err", "nonexistent", {})]),
                _native_final_answer("ok"),
            ]
        )
        registry = ToolRegistry()
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)

        await agent.run("error test")

        second_call_messages = llm.all_messages[1]
        tool_msgs = [m for m in second_call_messages if m.role == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].tool_call_id == "call-err"
        assert "Error:" in tool_msgs[0].content

    async def test_parallel_calls_with_mixed_success_and_failure(self) -> None:
        """One tool succeeds, another fails -- both results reported."""
        llm = NativeToolFakeLLM(
            responses=[
                _native_tool_call([
                    ("call-ok", "echo", {"text": "success"}),
                    ("call-bad", "fail", {}),
                ]),
                _native_final_answer("handled"),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        registry.register(FailingTool())
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)

        result = await agent.run("mixed")

        assert result.answer == "handled"
        assert result.steps[0].observation == "success"
        assert result.steps[0].error is None
        assert result.steps[1].error is not None
        assert "intentional failure" in result.steps[1].error


class TestNativeMaxIterations:
    """Max iteration protection in native mode."""

    async def test_max_iterations_reached(self) -> None:
        """Agent should stop after max_iterations even if LLM keeps calling tools."""
        llm = NativeToolFakeLLM(
            responses=[
                _native_tool_call([("call-loop", "echo", {"text": "again"})]),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, use_native_tools=True, max_iterations=3,
        )

        result = await agent.run("infinite loop")

        assert result.iterations == 3
        assert "unable to complete" in result.answer.lower()
        assert len(result.steps) == 3


class TestNativeOnIterationCallback:
    """Verify the on_iteration callback fires correctly in native mode."""

    async def test_callback_on_final_answer(self) -> None:
        llm = NativeToolFakeLLM(
            responses=[_native_final_answer("answer")]
        )
        registry = ToolRegistry()
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)

        callbacks: list[tuple] = []

        def on_iter(iteration: int, action: Action, obs: str | None, err: str | None) -> None:
            callbacks.append((iteration, action.type, obs, err))

        await agent.run("test", on_iteration=on_iter)

        assert len(callbacks) == 1
        assert callbacks[0] == (1, "final_answer", None, None)

    async def test_callback_on_tool_call_and_answer(self) -> None:
        llm = NativeToolFakeLLM(
            responses=[
                _native_tool_call([("c1", "echo", {"text": "hi"})]),
                _native_final_answer("done"),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)

        callbacks: list[tuple] = []

        def on_iter(iteration: int, action: Action, obs: str | None, err: str | None) -> None:
            callbacks.append((iteration, action.type, obs, err))

        await agent.run("test", on_iteration=on_iter)

        assert len(callbacks) == 2
        # First: tool call in iteration 1
        assert callbacks[0] == (1, "tool_call", "hi", None)
        # Second: final answer in iteration 2
        assert callbacks[1] == (2, "final_answer", None, None)

    async def test_callback_on_parallel_tool_calls(self) -> None:
        """Each parallel tool call should trigger its own callback."""
        llm = NativeToolFakeLLM(
            responses=[
                _native_tool_call([
                    ("c1", "echo", {"text": "a"}),
                    ("c2", "echo", {"text": "b"}),
                ]),
                _native_final_answer("done"),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)

        callbacks: list[tuple] = []

        def on_iter(iteration: int, action: Action, obs: str | None, err: str | None) -> None:
            callbacks.append((iteration, action.type, obs, err))

        await agent.run("test", on_iteration=on_iter)

        # Two tool callbacks (both in iteration 1) + one final answer (iteration 2)
        assert len(callbacks) == 3
        assert callbacks[0] == (1, "tool_call", "a", None)
        assert callbacks[1] == (1, "tool_call", "b", None)
        assert callbacks[2] == (2, "final_answer", None, None)


class TestNativeCustomSystemPrompt:
    """Verify that a custom system prompt overrides the native default."""

    async def test_custom_system_prompt_used_in_native_mode(self) -> None:
        llm = NativeToolFakeLLM(
            responses=[_native_final_answer("custom answer")]
        )
        registry = ToolRegistry()
        agent = ReActAgent(
            llm=llm,
            tools=registry,
            system_prompt="You are a custom bot.",
            use_native_tools=True,
        )

        result = await agent.run("test")

        assert result.answer == "custom answer"
        # Verify the system prompt was our custom one.
        first_call_messages = llm.all_messages[0]
        assert first_call_messages[0].content == "You are a custom bot."


class TestNativeEmptyToolRegistry:
    """Behaviour with no tools registered."""

    async def test_no_tools_gives_immediate_answer(self) -> None:
        """With no tools, the LLM should still work and give an answer."""
        llm = NativeToolFakeLLM(
            responses=[_native_final_answer("no tools needed")]
        )
        registry = ToolRegistry()
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)

        result = await agent.run("test")

        assert result.answer == "no tools needed"
        # With no tools registered, tools should be None.
        assert llm.received_tools is None
        assert llm.received_tool_choice is None


class TestNativeMultiStepToolCalls:
    """LLM makes multiple sequential tool calls across iterations."""

    async def test_two_sequential_tool_calls(self) -> None:
        llm = NativeToolFakeLLM(
            responses=[
                _native_tool_call([("c1", "echo", {"text": "step1"})]),
                _native_tool_call([("c2", "add", {"a": 3, "b": 4})]),
                _native_final_answer("step1 and 7"),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        registry.register(AddTool())
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)

        result = await agent.run("multi-step")

        assert result.answer == "step1 and 7"
        assert result.iterations == 3
        assert len(result.steps) == 3
        assert result.steps[0].action.tool_name == "echo"
        assert result.steps[0].observation == "step1"
        assert result.steps[1].action.tool_name == "add"
        assert result.steps[1].observation == "7"
        assert result.steps[2].action.type == "final_answer"


class TestNativeAssistantMessageWithContent:
    """LLM returns both content and tool_calls in the same message."""

    async def test_content_plus_tool_calls(self) -> None:
        """Content alongside tool_calls should not be treated as final answer."""
        llm = NativeToolFakeLLM(
            responses=[
                _native_tool_call(
                    [("c1", "echo", {"text": "data"})],
                    content="Let me look that up.",
                ),
                _native_final_answer("Here is the result: data"),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)

        result = await agent.run("test")

        # Should NOT stop at the first message even though it has content,
        # because it also has tool_calls.
        assert result.answer == "Here is the result: data"
        assert result.iterations == 2
        assert result.steps[0].action.type == "tool_call"


class TestBackwardCompatibility:
    """Ensure default behaviour (JSON mode) is unchanged."""

    async def test_default_json_mode_unchanged(self) -> None:
        """Without use_native_tools, behaviour is exactly as before."""
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(
                        role="assistant",
                        content=json.dumps({
                            "type": "tool_call",
                            "reasoning": "need echo",
                            "tool_name": "echo",
                            "tool_args": {"text": "hello"},
                        }),
                    ),
                ),
                LLMResult(
                    message=ChatMessage(
                        role="assistant",
                        content=json.dumps({
                            "type": "final_answer",
                            "reasoning": "got it",
                            "answer": "hello back",
                        }),
                    ),
                ),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(llm=llm, tools=registry)

        result = await agent.run("echo test")

        assert result.answer == "hello back"
        assert result.iterations == 2
        assert result.steps[0].action.type == "tool_call"
        assert result.steps[0].observation == "hello"

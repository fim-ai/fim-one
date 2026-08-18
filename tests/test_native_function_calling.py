"""Tests for native function calling mode in the ReAct agent."""

from __future__ import annotations

from typing import Any

import pytest

from fim_one.core.agent import ReActAgent
from fim_one.core.agent.types import Action, StepResult
from fim_one.core.tool import BaseTool, ToolRegistry

from .conftest import EchoTool
from .fake_llm import (
    NATIVE_TOOLS,
    FakeLLM,
    answer,
    react_final_answer,
    react_tool_call,
    tool_calls,
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
        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=[])
        registry = ToolRegistry()
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)
        assert agent._native_mode_active is True

    def test_native_mode_inactive_when_flag_false(self) -> None:
        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=[])
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
            responses=[react_final_answer("json fallback", reasoning="done")]
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
        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=[answer("The answer is 42.")])
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
        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=[answer("done")])
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)

        await agent.run("test")

        assert llm.received_tools is not None
        # update_plan is auto-registered by default alongside user tools.
        names = {t["function"]["name"] for t in llm.received_tools}
        assert names == {"echo", "update_plan"}

    async def test_tool_choice_auto(self) -> None:
        """Verify tools are passed to the LLM (stream_chat uses auto by default)."""
        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=[answer("done")])
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)

        await agent.run("test")

        # stream_chat() doesn't take tool_choice — the OpenAI API
        # defaults to "auto" when tools are present.
        assert llm.received_tools is not None


class TestNativeSingleToolCall:
    """LLM makes a single tool call, then answers."""

    async def test_single_tool_call_then_answer(self) -> None:
        llm = FakeLLM(abilities=NATIVE_TOOLS, 
            responses=[
                tool_calls([("call-1", "echo", {"text": "ping"})]),
                answer("Got: ping"),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, use_native_tools=True,
            completion_check=False,
        )

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
        llm = FakeLLM(abilities=NATIVE_TOOLS, 
            responses=[
                tool_calls([("call-abc", "echo", {"text": "hello"})]),
                answer("done"),
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
        llm = FakeLLM(abilities=NATIVE_TOOLS, 
            responses=[
                tool_calls(
                    [
                        ("call-1", "echo", {"text": "first"}),
                        ("call-2", "echo", {"text": "second"}),
                    ]
                ),
                answer("Both done"),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, use_native_tools=True,
            completion_check=False,
        )

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
        llm = FakeLLM(abilities=NATIVE_TOOLS, 
            responses=[
                tool_calls(
                    [
                        ("call-a", "echo", {"text": "aaa"}),
                        ("call-b", "add", {"a": 1, "b": 2}),
                    ]
                ),
                answer("done"),
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
        llm = FakeLLM(abilities=NATIVE_TOOLS, 
            responses=[
                tool_calls(
                    [
                        ("c1", "echo", {"text": "a"}),
                        ("c2", "echo", {"text": "b"}),
                        ("c3", "echo", {"text": "c"}),
                    ]
                ),
                answer("all done"),
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
        llm = FakeLLM(abilities=NATIVE_TOOLS, 
            responses=[
                tool_calls([("call-1", "nonexistent", {})]),
                answer("fallback"),
            ]
        )
        registry = ToolRegistry()
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)

        result = await agent.run("bad tool")

        assert result.answer == "fallback"
        assert result.steps[0].error is not None
        assert "Unknown tool" in result.steps[0].error

    async def test_tool_exception_produces_error(self) -> None:
        llm = FakeLLM(abilities=NATIVE_TOOLS, 
            responses=[
                tool_calls([("call-1", "fail", {})]),
                answer("recovered"),
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
        llm = FakeLLM(abilities=NATIVE_TOOLS, 
            responses=[
                tool_calls([("call-err", "nonexistent", {})]),
                answer("ok"),
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
        llm = FakeLLM(abilities=NATIVE_TOOLS, 
            responses=[
                tool_calls(
                    [
                        ("call-ok", "echo", {"text": "success"}),
                        ("call-bad", "fail", {}),
                    ]
                ),
                answer("handled"),
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
        llm = FakeLLM(abilities=NATIVE_TOOLS, 
            responses=[
                tool_calls([("call-loop", "echo", {"text": "again"})]),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm,
            tools=registry,
            use_native_tools=True,
            max_iterations=3,
        )

        result = await agent.run("infinite loop")

        assert result.iterations == 3
        assert "unable to complete" in result.answer.lower()
        assert len(result.steps) == 3


class TestNativeOnIterationCallback:
    """Verify the on_iteration callback fires correctly in native mode."""

    async def test_callback_on_final_answer(self) -> None:
        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=[answer("answer")])
        registry = ToolRegistry()
        agent = ReActAgent(llm=llm, tools=registry, use_native_tools=True)

        callbacks: list[tuple] = []

        def on_iter(
            iteration: int,
            action: Action,
            obs: str | None,
            err: str | None,
            step: Any = None,
        ) -> None:
            callbacks.append((iteration, action.type, obs, err))

        await agent.run("test", on_iteration=on_iter)

        assert len(callbacks) == 2
        assert callbacks[0] == (1, "thinking", None, None)
        assert callbacks[1] == (1, "final_answer", None, None)

    async def test_callback_on_tool_call_and_answer(self) -> None:
        llm = FakeLLM(abilities=NATIVE_TOOLS, 
            responses=[
                tool_calls([("c1", "echo", {"text": "hi"})]),
                answer("done"),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, use_native_tools=True,
            completion_check=False,
        )

        callbacks: list[tuple] = []

        def on_iter(
            iteration: int,
            action: Action,
            obs: str | None,
            err: str | None,
            step: Any = None,
        ) -> None:
            callbacks.append((iteration, action.type, obs, err))

        await agent.run("test", on_iteration=on_iter)

        assert len(callbacks) == 5
        # First: thinking start for iteration 1
        assert callbacks[0] == (1, "thinking", None, None)
        # Second: tool_start (obs=None, err=None)
        assert callbacks[1] == (1, "tool_call", None, None)
        # Third: tool result in iteration 1
        assert callbacks[2] == (1, "tool_call", "hi", None)
        # Fourth: thinking start for iteration 2
        assert callbacks[3] == (2, "thinking", None, None)
        # Fifth: final answer in iteration 2
        assert callbacks[4] == (2, "final_answer", None, None)

    async def test_callback_on_parallel_tool_calls(self) -> None:
        """Each parallel tool call should trigger its own callback."""
        llm = FakeLLM(abilities=NATIVE_TOOLS, 
            responses=[
                tool_calls(
                    [
                        ("c1", "echo", {"text": "a"}),
                        ("c2", "echo", {"text": "b"}),
                    ]
                ),
                answer("done"),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, use_native_tools=True,
            completion_check=False,
        )

        callbacks: list[tuple] = []

        def on_iter(
            iteration: int,
            action: Action,
            obs: str | None,
            err: str | None,
            step: Any = None,
        ) -> None:
            callbacks.append((iteration, action.type, obs, err))

        await agent.run("test", on_iteration=on_iter)

        # thinking + two tool_start + two tool results (all iteration 1)
        # + thinking + final answer (iteration 2)
        assert len(callbacks) == 7
        assert callbacks[0] == (1, "thinking", None, None)  # thinking iter 1
        assert callbacks[1] == (1, "tool_call", None, None)  # tool_start a
        assert callbacks[2] == (1, "tool_call", None, None)  # tool_start b
        assert callbacks[3] == (1, "tool_call", "a", None)  # result a
        assert callbacks[4] == (1, "tool_call", "b", None)  # result b
        assert callbacks[5] == (2, "thinking", None, None)  # thinking iter 2
        assert callbacks[6] == (2, "final_answer", None, None)


class TestNativeCustomSystemPrompt:
    """Verify that a custom system prompt overrides the native default."""

    async def test_custom_system_prompt_used_in_native_mode(self) -> None:
        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=[answer("custom answer")])
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
        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=[answer("no tools needed")])
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
        llm = FakeLLM(abilities=NATIVE_TOOLS, 
            responses=[
                tool_calls([("c1", "echo", {"text": "step1"})]),
                tool_calls([("c2", "add", {"a": 3, "b": 4})]),
                answer("step1 and 7"),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        registry.register(AddTool())
        agent = ReActAgent(
            llm=llm, tools=registry, use_native_tools=True,
            completion_check=False,
        )

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
        llm = FakeLLM(abilities=NATIVE_TOOLS, 
            responses=[
                tool_calls(
                    [("c1", "echo", {"text": "data"})],
                    content="Let me look that up.",
                ),
                answer("Here is the result: data"),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, use_native_tools=True,
            completion_check=False,
        )

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
                react_tool_call("echo", {"text": "hello"}, reasoning="need echo"),
                react_final_answer("hello back", reasoning="got it"),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(llm=llm, tools=registry, completion_check=False)

        result = await agent.run("echo test")

        assert result.answer == "hello back"
        assert result.iterations == 2
        assert result.steps[0].action.type == "tool_call"
        assert result.steps[0].observation == "hello"


class TestEmptyToolOutputWithWorkspace:
    async def test_fallback_text_survives_workspace_offload(
        self, tmp_path: Any
    ) -> None:
        """A tool returning "" must yield the 'no output' fallback in the
        tool message even when a workspace is configured — the offload pass
        previously overwrote the fallback with the raw empty string."""
        from fim_one.core.agent.workspace import AgentWorkspace

        llm = FakeLLM(abilities=NATIVE_TOOLS, 
            responses=[
                tool_calls([("call-1", "echo", {"text": ""})]),
                answer("done"),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm,
            tools=registry,
            completion_check=False,
            workspace=AgentWorkspace("conv-empty", base_dir=str(tmp_path)),
        )

        result = await agent.run("q")

        tool_msgs = [m for m in result.messages if m.role == "tool"]
        assert len(tool_msgs) == 1
        assert isinstance(tool_msgs[0].content, str)
        assert tool_msgs[0].content != ""
        assert "completed successfully with no output" in tool_msgs[0].content

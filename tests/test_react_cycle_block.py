"""Tests for the enforcing half of the cycle guard.

Warning alone never broke a loop: the duplicate call still ran, because
the guard counted *after* execution, so a model stuck repeating itself
burned an iteration and a real tool invocation every round until
``max_iterations`` finally stopped it.  The guard now counts before the
call and refuses once repetition is established.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from fim_one.core.agent import ReActAgent
from fim_one.core.agent.react import (
    _CYCLE_BLOCK_THRESHOLD,
    _CYCLE_DETECTION_THRESHOLD,
)
from fim_one.core.model import ChatMessage, LLMResult
from fim_one.core.model.types import ToolCallRequest
from fim_one.core.tool import BaseTool, ToolRegistry

from .test_react_harness import (
    CapturingFakeLLM,
    CapturingNativeFakeLLM,
    _json_final_answer,
    _json_tool_call,
    _native_final_answer,
    _native_tool_call,
)


class CountingTool(BaseTool):
    """Echo tool that records how many times it actually executed."""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echoes the input text back."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"text": {"type": "string"}},
        }

    async def run(self, **kwargs: Any) -> str:
        self.calls += 1
        return str(kwargs.get("text", ""))


class TestThresholds:
    def test_block_threshold_is_above_the_warning_threshold(self) -> None:
        """The first repeat still runs; only established repetition is refused."""
        assert _CYCLE_BLOCK_THRESHOLD > _CYCLE_DETECTION_THRESHOLD


class TestJsonModeBlocking:
    @pytest.mark.asyncio
    async def test_tool_stops_executing_once_refused(self) -> None:
        args = {"text": "same"}
        repeats = _CYCLE_BLOCK_THRESHOLD + 3
        responses = [_json_tool_call("echo", args) for _ in range(repeats)]
        responses.append(_json_final_answer())

        tool = CountingTool()
        registry = ToolRegistry()
        registry.register(tool)
        agent = ReActAgent(
            llm=CapturingFakeLLM(responses),
            tools=registry,
            max_iterations=repeats + 5,
            completion_check=False,
            enable_plan_tool=False,
        )

        await agent.run("loop please")

        # Execution stops at the block threshold no matter how many more
        # identical calls the model makes.
        assert tool.calls == _CYCLE_BLOCK_THRESHOLD - 1
        assert tool.calls < repeats

    @pytest.mark.asyncio
    async def test_refusal_reaches_the_model(self) -> None:
        args = {"text": "same"}
        responses = [
            _json_tool_call("echo", args)
            for _ in range(_CYCLE_BLOCK_THRESHOLD + 1)
        ]
        responses.append(_json_final_answer())

        llm = CapturingFakeLLM(responses)
        registry = ToolRegistry()
        registry.register(CountingTool())
        agent = ReActAgent(
            llm=llm,
            tools=registry,
            max_iterations=20,
            completion_check=False,
            enable_plan_tool=False,
        )

        await agent.run("loop please")

        seen = [
            str(m.content)
            for call in llm.all_messages
            for m in call
            if "Refused" in str(m.content)
        ]
        assert seen
        assert "cannot produce new information" in seen[0]

    @pytest.mark.asyncio
    async def test_distinct_arguments_are_never_refused(self) -> None:
        responses = [
            _json_tool_call("echo", {"text": f"different_{i}"})
            for i in range(_CYCLE_BLOCK_THRESHOLD + 4)
        ]
        responses.append(_json_final_answer())

        tool = CountingTool()
        registry = ToolRegistry()
        registry.register(tool)
        agent = ReActAgent(
            llm=CapturingFakeLLM(responses),
            tools=registry,
            max_iterations=20,
            completion_check=False,
            enable_plan_tool=False,
        )

        await agent.run("vary the args")

        assert tool.calls == _CYCLE_BLOCK_THRESHOLD + 4


class TestNativeModeBlocking:
    @pytest.mark.asyncio
    async def test_tool_stops_executing_once_refused(self) -> None:
        args = {"text": "same"}
        repeats = _CYCLE_BLOCK_THRESHOLD + 3
        responses = [
            _native_tool_call([(f"c{i}", "echo", args)]) for i in range(repeats)
        ]
        responses.append(_native_final_answer())

        tool = CountingTool()
        registry = ToolRegistry()
        registry.register(tool)
        agent = ReActAgent(
            llm=CapturingNativeFakeLLM(responses),
            tools=registry,
            use_native_tools=True,
            max_iterations=repeats + 5,
            completion_check=False,
            enable_plan_tool=False,
        )

        await agent.run("loop please")

        assert tool.calls == _CYCLE_BLOCK_THRESHOLD - 1

    @pytest.mark.asyncio
    async def test_every_tool_use_still_gets_a_reply(self) -> None:
        """A refused call must still answer its ``tool_use`` block.

        Anthropic rejects a request whose ``tool_use`` has no matching
        ``tool_result``, so skipping execution must not skip the reply.
        """
        tool = CountingTool()
        registry = ToolRegistry()
        registry.register(tool)
        agent = ReActAgent(
            llm=CapturingNativeFakeLLM([_native_final_answer()]),
            tools=registry,
            use_native_tools=True,
            enable_plan_tool=False,
        )
        steps: list[Any] = []

        calls = [
            ToolCallRequest(id="c1", name="echo", arguments={"text": "a"}),
            ToolCallRequest(id="c2", name="echo", arguments={"text": "b"}),
        ]
        messages = await agent._execute_native_tool_calls(
            calls,
            iteration=1,
            steps=steps,
            on_iteration=None,
            blocked_call_ids={"c1"},
        )

        assert {m.tool_call_id for m in messages} == {"c1", "c2"}
        assert "Refused" in str(next(m for m in messages if m.tool_call_id == "c1").content)
        # Only the unblocked call reached the tool.
        assert tool.calls == 1

    @pytest.mark.asyncio
    async def test_refused_call_skips_pre_hooks(self) -> None:
        """Nothing runs for a refused call — including hook side effects."""
        from fim_one.core.agent.hooks import HookRegistry

        seen: list[str] = []

        class RecordingHooks(HookRegistry):
            async def run_pre_tool(self, ctx: Any) -> Any:  # type: ignore[override]
                seen.append(ctx.tool_name)
                return await super().run_pre_tool(ctx)

        tool = CountingTool()
        registry = ToolRegistry()
        registry.register(tool)
        agent = ReActAgent(
            llm=CapturingNativeFakeLLM([_native_final_answer()]),
            tools=registry,
            use_native_tools=True,
            hook_registry=RecordingHooks(),
            enable_plan_tool=False,
        )

        await agent._execute_native_tool_calls(
            [ToolCallRequest(id="c1", name="echo", arguments={"text": "a"})],
            iteration=1,
            steps=[],
            on_iteration=None,
            blocked_call_ids={"c1"},
        )

        assert seen == []
        assert tool.calls == 0


class TestCheckCycleContract:
    def test_first_call_is_unremarkable(self) -> None:
        agent = ReActAgent(
            llm=CapturingFakeLLM([]), tools=ToolRegistry(), enable_plan_tool=False,
        )
        tracker: dict[tuple[str, str], int] = {}
        message, blocked = agent._check_cycle("echo", {"text": "x"}, tracker)
        assert message is None
        assert blocked is False

    def test_escalates_from_warning_to_refusal(self) -> None:
        agent = ReActAgent(
            llm=CapturingFakeLLM([]), tools=ToolRegistry(), enable_plan_tool=False,
        )
        tracker: dict[tuple[str, str], int] = {}
        outcomes = [
            agent._check_cycle("echo", {"text": "x"}, tracker)
            for _ in range(_CYCLE_BLOCK_THRESHOLD)
        ]

        assert outcomes[0] == (None, False)
        assert outcomes[_CYCLE_DETECTION_THRESHOLD - 1][0] is not None
        assert outcomes[_CYCLE_DETECTION_THRESHOLD - 1][1] is False
        assert outcomes[-1][1] is True

    def test_counts_are_per_argument_set(self) -> None:
        agent = ReActAgent(
            llm=CapturingFakeLLM([]), tools=ToolRegistry(), enable_plan_tool=False,
        )
        tracker: dict[tuple[str, str], int] = {}
        for i in range(_CYCLE_BLOCK_THRESHOLD + 2):
            message, blocked = agent._check_cycle("echo", {"text": str(i)}, tracker)
            assert message is None
            assert blocked is False

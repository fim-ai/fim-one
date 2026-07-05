"""Tests for background tool execution in the native ReAct loop."""

from __future__ import annotations

import asyncio
from typing import Any

from fim_one.core.agent import ReActAgent
from fim_one.core.model import ChatMessage, LLMResult
from fim_one.core.model.types import ToolCallRequest
from fim_one.core.tool import BaseTool, ToolRegistry

from .conftest import EchoTool
from .test_native_function_calling import NativeToolFakeLLM


class SlowTool(BaseTool):
    """A whitelisted-for-background tool that sleeps briefly."""

    def __init__(self, delay: float = 0.05, result: str = "slow done") -> None:
        self._delay = delay
        self._result = result
        self.calls = 0

    @property
    def name(self) -> str:
        return "slow_tool"

    @property
    def description(self) -> str:
        return "A slow operation."

    @property
    def supports_background(self) -> bool:
        return True

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs: Any) -> str:
        self.calls += 1
        await asyncio.sleep(self._delay)
        return self._result


def _tool_call(name: str, args: dict[str, Any], call_id: str = "tc1") -> LLMResult:
    return LLMResult(
        message=ChatMessage(
            role="assistant",
            content=None,
            tool_calls=[ToolCallRequest(id=call_id, name=name, arguments=args)],
        ),
    )


def _final(answer: str) -> LLMResult:
    return LLMResult(message=ChatMessage(role="assistant", content=answer))


def _make_agent(llm: NativeToolFakeLLM, *tools: Any) -> ReActAgent:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return ReActAgent(
        llm=llm,
        tools=registry,
        use_native_tools=True,
        completion_check=False,
        enable_plan_tool=False,
    )


def _notifications(result: Any) -> list[str]:
    return [
        m.content
        for m in result.messages
        if m.role == "user"
        and isinstance(m.content, str)
        and m.content.startswith("<task_notification>")
    ]


class TestSchemaAdvertisement:
    async def test_run_in_background_only_on_whitelisted_tools(self) -> None:
        llm = NativeToolFakeLLM(responses=[_final("done")])
        agent = _make_agent(llm, SlowTool(), EchoTool())

        await agent.run("q")

        assert llm.received_tools is not None
        by_name = {t["function"]["name"]: t for t in llm.received_tools}
        assert (
            "run_in_background"
            in by_name["slow_tool"]["function"]["parameters"]["properties"]
        )
        assert (
            "run_in_background"
            not in by_name["echo"]["function"]["parameters"]["properties"]
        )


class TestBackgroundDispatch:
    async def test_result_arrives_as_notification(self) -> None:
        # 1: dispatch slow_tool to background; 2: echo (bg completes during
        # the sleep); 3: final answer.
        llm = NativeToolFakeLLM(
            responses=[
                _tool_call("slow_tool", {"run_in_background": True}, "tc1"),
                _tool_call("echo", {"text": "hi"}, "tc2"),
                _final("done"),
            ]
        )
        slow = SlowTool(delay=0.01)
        agent = _make_agent(llm, slow, EchoTool())

        result = await agent.run("q")

        assert result.answer == "done"
        assert slow.calls == 1
        # The placeholder tool message answered the original call id.
        placeholders = [
            m
            for m in result.messages
            if m.role == "tool"
            and isinstance(m.content, str)
            and m.content.startswith("[Background task")
        ]
        assert len(placeholders) == 1
        notes = _notifications(result)
        assert len(notes) == 1
        assert "slow done" in notes[0]
        assert "<tool>slow_tool</tool>" in notes[0]

    async def test_finalize_waits_for_pending_background(self) -> None:
        # 1: dispatch bg; 2: immediate final answer while bg pending →
        # gate waits + injects the notification → 3: real final answer.
        llm = NativeToolFakeLLM(
            responses=[
                _tool_call("slow_tool", {"run_in_background": True}),
                _final("premature"),
                _final("informed answer"),
            ]
        )
        agent = _make_agent(llm, SlowTool(delay=0.01))

        result = await agent.run("q")

        assert result.answer == "informed answer"
        assert llm.call_count == 3
        notes = _notifications(result)
        assert len(notes) == 1
        assert "slow done" in notes[0]

    async def test_non_whitelisted_tool_runs_foreground(self) -> None:
        # run_in_background on echo (not whitelisted) is stripped and the
        # tool runs synchronously.
        llm = NativeToolFakeLLM(
            responses=[
                _tool_call("echo", {"text": "hi", "run_in_background": True}),
                _final("done"),
            ]
        )
        agent = _make_agent(llm, EchoTool())

        result = await agent.run("q")

        assert result.answer == "done"
        assert _notifications(result) == []
        tool_msgs = [m for m in result.messages if m.role == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].content == "hi"

    async def test_background_error_becomes_notification(self) -> None:
        class FailingSlowTool(SlowTool):
            async def run(self, **kwargs: Any) -> str:
                self.calls += 1
                raise RuntimeError("boom")

        llm = NativeToolFakeLLM(
            responses=[
                _tool_call("slow_tool", {"run_in_background": True}),
                _final("handled"),
            ]
        )
        agent = _make_agent(llm, FailingSlowTool())

        result = await agent.run("q")

        # The task fails instantly, so the notification is drained BEFORE
        # the second LLM call — the model answers with it in context.
        assert result.answer == "handled"
        notes = _notifications(result)
        assert len(notes) == 1
        assert "Error: RuntimeError: boom" in notes[0]
        second_call_msgs = llm.all_messages[1]
        assert any(
            isinstance(m.content, str) and "RuntimeError: boom" in m.content
            for m in second_call_msgs
        )


class TestBackgroundResultVisibility:
    async def test_drained_result_emitted_via_on_iteration(self) -> None:
        """The real background output must reach on_iteration (SSE stream /
        DAG evidence), not just the <task_notification> user message."""
        llm = NativeToolFakeLLM(
            responses=[
                _tool_call("slow_tool", {"run_in_background": True}, "tc1"),
                _tool_call("echo", {"text": "hi"}, "tc2"),
                _final("done"),
            ]
        )
        agent = _make_agent(llm, SlowTool(delay=0.01), EchoTool())

        events: list[tuple[int, Any, str | None, str | None]] = []

        def on_iteration(
            iteration: int,
            action: Any,
            observation: str | None,
            error: str | None,
            step_result: Any = None,
        ) -> None:
            events.append((iteration, action, observation, error))

        result = await agent.run("q", on_iteration=on_iteration)

        assert result.answer == "done"
        bg_events = [
            e
            for e in events
            if getattr(e[1], "tool_args", None)
            and e[1].tool_args.get("background") is True
        ]
        # Standard lifecycle: one start (no observation) + one done.
        assert len(bg_events) == 2
        start, done = bg_events
        assert start[1].tool_name == "slow_tool"
        assert start[2] is None and start[3] is None
        assert done[2] == "slow done"
        assert done[3] is None
        assert done[1].tool_args["task_id"].startswith("bg_")

    async def test_cancelled_background_task_reports_error_event(self) -> None:
        """A background task that errors surfaces via the error field so
        evidence collection never records the failure text as source data."""
        llm = NativeToolFakeLLM(
            responses=[
                _tool_call("boom_tool", {"run_in_background": True}, "tc1"),
                _tool_call("echo", {"text": "hi"}, "tc2"),
                _final("done"),
            ]
        )

        class BoomTool(SlowTool):
            @property
            def name(self) -> str:
                return "boom_tool"

            async def run(self, **kwargs: Any) -> str:
                await asyncio.sleep(0.01)
                raise RuntimeError("kaput")

        agent = _make_agent(llm, BoomTool(), EchoTool())

        events: list[tuple[Any, str | None, str | None]] = []

        def on_iteration(
            iteration: int,
            action: Any,
            observation: str | None,
            error: str | None,
            step_result: Any = None,
        ) -> None:
            events.append((action, observation, error))

        result = await agent.run("q", on_iteration=on_iteration)

        assert result.answer == "done"
        done_events = [
            e
            for e in events
            if getattr(e[0], "tool_args", None)
            and e[0].tool_args.get("background") is True
            and (e[1] is not None or e[2] is not None)
        ]
        assert len(done_events) == 1
        assert done_events[0][1] is None  # no observation
        assert done_events[0][2] is not None
        assert "kaput" in done_events[0][2]

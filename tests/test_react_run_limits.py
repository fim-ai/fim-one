"""Tests for the run-level limits: stall breaker and wall-clock budget.

The cycle guard refuses a call that has already been repeated, but a model
can keep issuing refused calls round after round; before the stall breaker
the only bound on that was ``max_iterations``.  The wall-clock limit stops
a run that is still making calls but has run past its time allowance.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from fim_one.core.agent import ReActAgent
from fim_one.core.agent import react as react_module
from fim_one.core.agent.react import _CYCLE_BLOCK_THRESHOLD, _STALL_ROUNDS
from fim_one.core.tool import BaseTool, ToolRegistry

from .fake_llm import (
    NATIVE_TOOLS,
    FakeLLM,
    answer,
    react_final_answer,
    react_tool_call,
    tool_calls,
)


class CountingTool(BaseTool):
    """Echo tool that records how many times it executed."""

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
        return {"type": "object", "properties": {"text": {"type": "string"}}}

    async def run(self, **kwargs: Any) -> str:
        self.calls += 1
        return str(kwargs.get("text", ""))


def _registry() -> tuple[ToolRegistry, CountingTool]:
    tool = CountingTool()
    registry = ToolRegistry()
    registry.register(tool)
    return registry, tool


# Rounds the model needs before the stall breaker fires: the first
# BLOCK-1 calls run, then STALL_ROUNDS calls are refused in a row.
_ROUNDS_TO_STALL = _CYCLE_BLOCK_THRESHOLD - 1 + _STALL_ROUNDS


class TestStallBreakerJson:
    @pytest.mark.asyncio
    async def test_stops_after_consecutive_refused_rounds(self) -> None:
        args = {"text": "same"}
        responses = [react_tool_call("echo", args) for _ in range(30)]
        responses.append(react_final_answer())
        llm = FakeLLM(responses)
        registry, tool = _registry()
        agent = ReActAgent(
            llm=llm,
            tools=registry,
            max_iterations=40,
            completion_check=False,
            enable_plan_tool=False,
        )

        result = await agent.run("loop please")

        assert result.iterations == _ROUNDS_TO_STALL
        assert "no progress" in result.answer
        assert "how to proceed" in result.answer
        assert tool.calls == _CYCLE_BLOCK_THRESHOLD - 1
        # The model was not asked again once the run stopped.
        assert len(llm.calls) == _ROUNDS_TO_STALL

    @pytest.mark.asyncio
    async def test_a_round_that_runs_resets_the_count(self) -> None:
        """Refused rounds broken by real work never trip the breaker."""
        stuck = {"text": "same"}
        responses = [react_tool_call("echo", stuck) for _ in range(_CYCLE_BLOCK_THRESHOLD)]
        # Alternate a refused repeat with a fresh call, well past STALL_ROUNDS.
        for i in range(_STALL_ROUNDS * 3):
            responses.append(react_tool_call("echo", stuck))
            responses.append(react_tool_call("echo", {"text": f"new-{i}"}))
        responses.append(react_final_answer("finished"))
        registry, _ = _registry()
        agent = ReActAgent(
            llm=FakeLLM(responses),
            tools=registry,
            max_iterations=100,
            completion_check=False,
            enable_plan_tool=False,
        )

        result = await agent.run("keep working")

        assert "no progress" not in result.answer

    @pytest.mark.asyncio
    async def test_disabled_when_zero(self) -> None:
        args = {"text": "same"}
        responses = [react_tool_call("echo", args) for _ in range(_ROUNDS_TO_STALL + 2)]
        responses.append(react_final_answer("finished"))
        registry, _ = _registry()
        agent = ReActAgent(
            llm=FakeLLM(responses),
            tools=registry,
            max_iterations=40,
            completion_check=False,
            enable_plan_tool=False,
        )

        with patch.object(react_module, "_STALL_ROUNDS", 0):
            result = await agent.run("loop please")

        assert "no progress" not in result.answer
        assert result.iterations == _ROUNDS_TO_STALL + 3


    @pytest.mark.asyncio
    async def test_a_round_without_tool_calls_ends_the_streak(self) -> None:
        """Refusals split by a text round are not consecutive."""
        stuck = {"text": "same"}
        before = _CYCLE_BLOCK_THRESHOLD - 1 + _STALL_ROUNDS - 1
        responses = [react_tool_call("echo", stuck) for _ in range(before)]
        # A text answer that the completion check turns into another round.
        responses.append(react_final_answer("draft"))
        responses.append(react_tool_call("echo", stuck))
        responses.append(react_final_answer("finished"))
        registry, _ = _registry()
        agent = ReActAgent(
            llm=FakeLLM(responses),
            tools=registry,
            max_iterations=40,
            completion_check=True,
            enable_plan_tool=False,
        )

        with patch.object(react_module, "_COMPLETION_CHECK_MIN_TOOLS", 1):
            result = await agent.run("loop please")

        # The draft did not end the run: the refused call after it ran.
        assert result.iterations > before + 1
        assert "no progress" not in result.answer

    @pytest.mark.asyncio
    async def test_stop_answer_reads_as_sentences(self) -> None:
        args = {"text": "same"}
        responses = [react_tool_call("echo", args) for _ in range(30)]
        registry, _ = _registry()
        agent = ReActAgent(
            llm=FakeLLM(responses),
            tools=registry,
            max_iterations=40,
            completion_check=False,
            enable_plan_tool=False,
        )

        result = await agent.run("loop please")

        assert "how to proceed. Stopped after" in result.answer
        assert "refused as a repeat" in result.answer


class TestStallBreakerNative:
    @pytest.mark.asyncio
    async def test_stops_after_consecutive_refused_rounds(self) -> None:
        args = {"text": "same"}
        responses = [tool_calls([(f"c{i}", "echo", args)]) for i in range(30)]
        responses.append(answer("done"))
        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=responses)
        registry, tool = _registry()
        agent = ReActAgent(
            llm=llm,
            tools=registry,
            use_native_tools=True,
            max_iterations=40,
            completion_check=False,
            enable_plan_tool=False,
        )

        result = await agent.run("loop please")

        assert result.iterations == _ROUNDS_TO_STALL
        assert "no progress" in result.answer
        assert tool.calls == _CYCLE_BLOCK_THRESHOLD - 1

    @pytest.mark.asyncio
    async def test_partly_refused_batch_is_not_a_stall(self) -> None:
        """A batch where one call still runs counts as progress."""
        stuck = {"text": "same"}
        responses = [
            tool_calls([(f"s{i}", "echo", stuck)]) for i in range(_CYCLE_BLOCK_THRESHOLD)
        ]
        for i in range(_STALL_ROUNDS * 2):
            responses.append(
                tool_calls([
                    (f"r{i}", "echo", stuck),
                    (f"n{i}", "echo", {"text": f"new-{i}"}),
                ])
            )
        responses.append(answer("finished"))
        registry, _ = _registry()
        agent = ReActAgent(
            llm=FakeLLM(abilities=NATIVE_TOOLS, responses=responses),
            tools=registry,
            use_native_tools=True,
            max_iterations=100,
            completion_check=False,
            enable_plan_tool=False,
        )

        result = await agent.run("keep working")

        assert "no progress" not in result.answer


class TestEnvWiring:
    def test_time_limit_is_read_per_agent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry, _ = _registry()
        monkeypatch.setenv("REACT_MAX_TURN_SECONDS", "42")
        agent = ReActAgent(llm=FakeLLM([]), tools=registry, enable_plan_tool=False)
        assert agent._max_turn_seconds == 42.0

    def test_deps_getter_reads_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fim_one.web.deps import get_react_max_turn_seconds

        monkeypatch.setenv("REACT_MAX_TURN_SECONDS", "7.5")
        assert get_react_max_turn_seconds() == 7.5
        monkeypatch.delenv("REACT_MAX_TURN_SECONDS")
        assert get_react_max_turn_seconds() == 0.0


class TestWallClockLimit:
    @pytest.mark.asyncio
    async def test_json_run_stops_past_the_time_limit(self) -> None:
        responses = [react_tool_call("echo", {"text": f"t{i}"}) for i in range(10)]
        responses.append(react_final_answer())
        registry, tool = _registry()
        agent = ReActAgent(
            llm=FakeLLM(responses),
            tools=registry,
            max_iterations=20,
            max_turn_seconds=5,
            completion_check=False,
            enable_plan_tool=False,
        )
        # Each _now() read advances 2s: loop start reads 0, the check
        # before iteration 1 sees 2s, before iteration 2 sees 4s, then 6s.
        clock = iter(float(t) for t in range(0, 1000, 2))

        with patch.object(react_module, "_now", lambda: next(clock)):
            result = await agent.run("work")

        assert "time limit" in result.answer and "of 5s" in result.answer
        assert result.iterations == 2
        assert tool.calls == 2

    @pytest.mark.asyncio
    async def test_native_run_stops_past_the_time_limit(self) -> None:
        responses = [tool_calls([(f"c{i}", "echo", {"text": f"t{i}"})]) for i in range(10)]
        responses.append(answer("done"))
        registry, _ = _registry()
        agent = ReActAgent(
            llm=FakeLLM(abilities=NATIVE_TOOLS, responses=responses),
            tools=registry,
            use_native_tools=True,
            max_iterations=20,
            max_turn_seconds=5,
            completion_check=False,
            enable_plan_tool=False,
        )
        clock = iter(float(t) for t in range(0, 1000, 2))

        with patch.object(react_module, "_now", lambda: next(clock)):
            result = await agent.run("work")

        assert "time limit" in result.answer and "of 5s" in result.answer
        assert result.iterations == 2

    @pytest.mark.asyncio
    async def test_unlimited_by_default(self) -> None:
        registry, _ = _registry()
        agent = ReActAgent(
            llm=FakeLLM([react_tool_call("echo", {"text": "a"}), react_final_answer("ok")]),
            tools=registry,
            completion_check=False,
            enable_plan_tool=False,
        )

        result = await agent.run("work")

        assert "time limit" not in result.answer

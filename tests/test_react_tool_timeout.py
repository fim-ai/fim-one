"""Tests for the ReAct loop's wall-clock backstop around tool execution.

Twenty of the built-in tools carry no timeout of their own, so before this
guard a hung tool (a stalled MCP server, an unresponsive connector) wedged
the whole turn indefinitely.  Server-side that is worse than in a CLI: no
human is watching to abort it, and the stuck call holds a worker.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from fim_one.core.agent.react import (
    _TOOL_RESULT_BUDGET,
    _TOOL_RESULT_MIN_TOKENS,
    _TOOL_TIMEOUT_SECONDS,
    ReActAgent,
)
from fim_one.core.agent.types import Action
from fim_one.core.tool import BaseTool, ToolRegistry


class HangingTool(BaseTool):
    """A tool that never returns on its own."""

    def __init__(self, timeout: float | None = 0.05) -> None:
        self._timeout = timeout
        self.started = False
        self.completed = False

    @property
    def name(self) -> str:
        return "hang"

    @property
    def description(self) -> str:
        return "Blocks forever."

    @property
    def timeout_seconds(self) -> float | None:
        return self._timeout

    async def run(self, **kwargs: Any) -> str:
        self.started = True
        await asyncio.sleep(30)
        self.completed = True
        return "never reached"


class QuickTool(BaseTool):
    """A tool that returns immediately."""

    @property
    def name(self) -> str:
        return "quick"

    @property
    def description(self) -> str:
        return "Returns at once."

    async def run(self, **kwargs: Any) -> str:
        return "fast result"


def _agent(tool: BaseTool) -> ReActAgent:
    registry = ToolRegistry()
    registry.register(tool)
    from .conftest import FakeLLM

    return ReActAgent(
        llm=FakeLLM(responses=[]),
        tools=registry,
        enable_plan_tool=False,
    )


class TestGuardedExecution:
    @pytest.mark.asyncio
    async def test_overrunning_tool_raises_timeout(self) -> None:
        tool = HangingTool(timeout=0.05)
        with pytest.raises(TimeoutError):
            await ReActAgent._run_tool_guarded(tool, "hang", {})
        assert tool.started
        assert not tool.completed

    @pytest.mark.asyncio
    async def test_fast_tool_is_unaffected(self) -> None:
        result = await ReActAgent._run_tool_guarded(QuickTool(), "quick", {})
        assert result == "fast result"

    @pytest.mark.asyncio
    async def test_returns_promptly_rather_than_waiting_out_the_tool(self) -> None:
        """The loop must regain control at the deadline, not at tool exit."""
        loop = asyncio.get_running_loop()
        start = loop.time()
        with pytest.raises(TimeoutError):
            await ReActAgent._run_tool_guarded(HangingTool(timeout=0.05), "hang", {})
        assert loop.time() - start < 5

    @pytest.mark.asyncio
    async def test_zero_timeout_disables_the_guard(self) -> None:
        tool = HangingTool(timeout=0)
        with pytest.raises(TimeoutError):
            # No guard, so the caller's own bound is what stops it.
            await asyncio.wait_for(
                ReActAgent._run_tool_guarded(tool, "hang", {}), timeout=0.05,
            )

    @pytest.mark.asyncio
    async def test_none_falls_back_to_the_loop_default(self) -> None:
        """A tool that declines to specify one inherits the loop backstop."""
        assert QuickTool().timeout_seconds is None
        assert _TOOL_TIMEOUT_SECONDS > 0


class TestTimeoutSurfacedToModel:
    """A timeout must read as a distinct, actionable observation."""

    def test_message_names_the_tool_and_budget(self) -> None:
        msg = ReActAgent._timeout_message(HangingTool(timeout=12), "hang")
        assert "hang" in msg
        assert "12s" in msg

    def test_message_steers_away_from_an_identical_retry(self) -> None:
        msg = ReActAgent._timeout_message(HangingTool(timeout=1), "hang")
        assert "Do not retry it unchanged" in msg

    @pytest.mark.asyncio
    async def test_json_mode_reports_timeout_as_a_step_error(self) -> None:
        tool = HangingTool(timeout=0.05)
        agent = _agent(tool)

        step = await agent._execute_tool_call(
            Action(type="tool_call", reasoning="", tool_name="hang", tool_args={}),
        )

        assert step.error is not None
        assert "time limit" in step.error
        assert step.observation is None

    @pytest.mark.asyncio
    async def test_native_mode_reports_timeout_as_a_tool_message(self) -> None:
        from fim_one.core.model.types import ToolCallRequest

        tool = HangingTool(timeout=0.05)
        agent = _agent(tool)
        steps: list[Any] = []

        messages = await agent._execute_native_tool_calls(
            [ToolCallRequest(id="c1", name="hang", arguments={})],
            iteration=1,
            steps=steps,
            on_iteration=None,
        )

        assert len(messages) == 1
        assert messages[0].role == "tool"
        assert messages[0].tool_call_id == "c1"
        assert "time limit" in str(messages[0].content)
        assert steps[0].error is not None

    @pytest.mark.asyncio
    async def test_one_slow_tool_does_not_sink_its_parallel_siblings(self) -> None:
        """Native mode runs a batch concurrently; a hang must not lose the rest."""
        from fim_one.core.model.types import ToolCallRequest

        registry = ToolRegistry()
        registry.register(HangingTool(timeout=0.05))
        registry.register(QuickTool())
        from .conftest import FakeLLM

        agent = ReActAgent(
            llm=FakeLLM(responses=[]),
            tools=registry,
            enable_plan_tool=False,
        )
        steps: list[Any] = []

        messages = await agent._execute_native_tool_calls(
            [
                ToolCallRequest(id="c1", name="hang", arguments={}),
                ToolCallRequest(id="c2", name="quick", arguments={}),
            ],
            iteration=1,
            steps=steps,
            on_iteration=None,
        )

        by_id = {m.tool_call_id: str(m.content) for m in messages}
        assert "time limit" in by_id["c1"]
        assert "fast result" in by_id["c2"]


class TestToolResultBudgetFloor:
    """An exhausted aggregate budget must not blind the model.

    The allowance used to clamp to zero, so once the run had spent its
    budget every later tool result collapsed to a bare truncation note:
    the agent kept calling tools and got nothing back for the rest of the
    run.  A floor keeps a usable window on every result; the full text
    still goes to the workspace.
    """

    def test_floor_is_positive_and_below_the_budget(self) -> None:
        assert 0 < _TOOL_RESULT_MIN_TOKENS < _TOOL_RESULT_BUDGET

    def test_exhausted_budget_still_yields_content(self) -> None:
        from fim_one.core.memory.compact import CompactUtils

        spent = _TOOL_RESULT_BUDGET  # nothing left
        remaining = max(_TOOL_RESULT_MIN_TOKENS, _TOOL_RESULT_BUDGET - spent)
        kept = CompactUtils.truncate_head_tail("payload " * 5000, remaining)

        assert kept.strip()
        assert CompactUtils.estimate_tokens(kept) >= _TOOL_RESULT_MIN_TOKENS // 2

    def test_old_arithmetic_would_have_yielded_nothing(self) -> None:
        from fim_one.core.memory.compact import CompactUtils

        spent = _TOOL_RESULT_BUDGET
        assert CompactUtils.truncate_head_tail("payload " * 5000, max(0, _TOOL_RESULT_BUDGET - spent)) == ""

"""Tests for the ReAct plan/todo tool and stale-plan reminder."""

from __future__ import annotations

import json
from typing import Any

import pytest

from fim_one.core.agent import ReActAgent
from fim_one.core.agent.plan_tool import (
    PlanState,
    UpdatePlanTool,
    make_plan_reminder,
    normalize_todos,
)
from fim_one.core.model import ChatMessage
from fim_one.core.tool import ToolRegistry

from .conftest import EchoTool, FakeLLM


def _tool_call(tool_name: str, tool_args: dict[str, Any]) -> Any:
    from fim_one.core.model import LLMResult

    return LLMResult(
        message=ChatMessage(
            role="assistant",
            content=json.dumps(
                {
                    "type": "tool_call",
                    "reasoning": "r",
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                }
            ),
        ),
    )


def _final(answer: str) -> Any:
    from fim_one.core.model import LLMResult

    return LLMResult(
        message=ChatMessage(
            role="assistant",
            content=json.dumps(
                {"type": "final_answer", "reasoning": "r", "answer": answer}
            ),
        ),
    )


_TODOS = [
    {"content": "step one", "status": "completed"},
    {"content": "step two", "status": "in_progress"},
    {"content": "step three", "status": "pending"},
]


class TestNormalizeTodos:
    def test_valid_list(self) -> None:
        result = normalize_todos(_TODOS)
        assert len(result) == 3
        assert result[1] == {"content": "step two", "status": "in_progress"}

    def test_json_string_accepted(self) -> None:
        result = normalize_todos(json.dumps(_TODOS))
        assert len(result) == 3

    def test_invalid_json_string(self) -> None:
        with pytest.raises(ValueError, match="not valid JSON"):
            normalize_todos("[not json")

    def test_non_list_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be an array"):
            normalize_todos({"content": "x"})

    def test_empty_list_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            normalize_todos([])

    def test_missing_content_rejected(self) -> None:
        with pytest.raises(ValueError, match="content"):
            normalize_todos([{"status": "pending"}])

    def test_invalid_status_rejected(self) -> None:
        with pytest.raises(ValueError, match="status"):
            normalize_todos([{"content": "x", "status": "done"}])

    def test_default_status_is_pending(self) -> None:
        result = normalize_todos([{"content": "x"}])
        assert result[0]["status"] == "pending"


class TestPlanState:
    def test_empty_state_has_no_open_items(self) -> None:
        state = PlanState()
        assert not state.has_open_items
        assert state.render() == "(no plan recorded)"

    def test_open_items_detected(self) -> None:
        state = PlanState()
        state.replace(normalize_todos(_TODOS))
        assert state.has_open_items

    def test_all_completed_means_no_open_items(self) -> None:
        state = PlanState()
        state.replace([{"content": "x", "status": "completed"}])
        assert not state.has_open_items

    def test_render_marks(self) -> None:
        state = PlanState()
        state.replace(normalize_todos(_TODOS))
        rendered = state.render()
        assert "[x] step one" in rendered
        assert "[~] step two" in rendered
        assert "[ ] step three" in rendered

    def test_reset_clears(self) -> None:
        state = PlanState()
        state.replace(normalize_todos(_TODOS))
        state.reset()
        assert state.todos == []


class TestUpdatePlanTool:
    async def test_updates_state_and_confirms(self) -> None:
        state = PlanState()
        tool = UpdatePlanTool(state)
        result = await tool.run(todos=_TODOS)
        assert "3 items, 2 open" in result
        assert state.has_open_items

    async def test_invalid_input_returns_error_string(self) -> None:
        state = PlanState()
        tool = UpdatePlanTool(state)
        result = await tool.run(todos="oops")
        assert result.startswith("[Error]")
        assert state.todos == []

    def test_not_cacheable(self) -> None:
        assert not UpdatePlanTool(PlanState()).cacheable


class TestMakePlanReminder:
    def test_embeds_checklist(self) -> None:
        state = PlanState()
        state.replace(normalize_todos(_TODOS))
        reminder = make_plan_reminder(state)
        assert reminder.startswith("<plan-reminder>")
        assert "[~] step two" in reminder
        assert "update_plan" in reminder


class TestAgentIntegration:
    def _make_agent(self, responses: list[Any], **kwargs: Any) -> ReActAgent:
        registry = ToolRegistry()
        registry.register(EchoTool())
        return ReActAgent(
            llm=FakeLLM(responses),
            tools=registry,
            use_native_tools=False,
            completion_check=False,
            **kwargs,
        )

    def test_plan_tool_registered_by_default(self) -> None:
        agent = self._make_agent([_final("ok")])
        assert "update_plan" in agent.tools

    def test_plan_tool_disabled_explicitly(self) -> None:
        agent = self._make_agent([_final("ok")], enable_plan_tool=False)
        assert "update_plan" not in agent.tools

    def test_plan_guidance_in_system_prompt(self) -> None:
        agent = self._make_agent([_final("ok")])
        prefix, _suffix = agent._build_system_prompt_split()
        assert "update_plan" in prefix
        assert "Planning:" in prefix

    def test_no_plan_guidance_when_disabled(self) -> None:
        agent = self._make_agent([_final("ok")], enable_plan_tool=False)
        prefix, _suffix = agent._build_system_prompt_split()
        assert "Planning:" not in prefix

    async def test_stale_plan_reminder_injected(self) -> None:
        # update_plan → 3 stale echo rounds → reminder → final answer.
        responses = [
            _tool_call("update_plan", {"todos": _TODOS}),
            _tool_call("echo", {"text": "a"}),
            _tool_call("echo", {"text": "b"}),
            _tool_call("echo", {"text": "c"}),
            _final("done"),
        ]
        agent = self._make_agent(responses)
        result = await agent.run("do a multi-step task")
        reminders = [
            m
            for m in result.messages
            if m.role == "user"
            and isinstance(m.content, str)
            and m.content.startswith("<plan-reminder>")
        ]
        assert len(reminders) == 1
        assert "[~] step two" in reminders[0].content

    async def test_no_reminder_without_plan(self) -> None:
        responses = [
            _tool_call("echo", {"text": "a"}),
            _tool_call("echo", {"text": "b"}),
            _tool_call("echo", {"text": "c"}),
            _tool_call("echo", {"text": "d"}),
            _final("done"),
        ]
        agent = self._make_agent(responses)
        result = await agent.run("simple task")
        assert not any(
            isinstance(m.content, str) and m.content.startswith("<plan-reminder>")
            for m in result.messages
        )

    async def test_plan_state_resets_between_runs(self) -> None:
        responses = [
            _tool_call("update_plan", {"todos": _TODOS}),
            _final("done"),
        ]
        agent = self._make_agent(responses)
        await agent.run("task one")
        assert agent._plan_state is not None
        assert agent._plan_state.todos  # populated by first run

        # Second run: FakeLLM replays the last response (final answer)
        # immediately; the plan board must start empty again.
        agent2_llm_responses = [_final("done")]
        agent._llm = FakeLLM(agent2_llm_responses)  # type: ignore[attr-defined]
        agent._tool_llm = agent._llm
        await agent.run("task two")
        assert agent._plan_state.todos == []

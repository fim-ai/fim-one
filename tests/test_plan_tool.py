"""Tests for the ReAct plan/todo tool and stale-plan reminder."""

from __future__ import annotations

import json
from typing import Any

import pytest

from fim_one.core.agent import ReActAgent
from fim_one.core.agent.plan_tool import (
    PlanReminderTracker,
    PlanState,
    UpdatePlanTool,
    make_open_plan_note,
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


class TestPlanReminderTracker:
    def _tracker(self, state: PlanState | None = None) -> PlanReminderTracker:
        return PlanReminderTracker(
            state or PlanState(),
            stale_interval=3,
            repeat_threshold=4,
            no_plan_after=5,
        )

    def test_stale_fires_after_interval(self) -> None:
        state = PlanState()
        state.replace(normalize_todos(_TODOS))
        tracker = self._tracker(state)
        assert tracker.observe_round(["a"]) is None
        assert tracker.observe_round(["b"]) is None
        reminder = tracker.observe_round(["c"])
        assert reminder is not None and reminder.kind == "stale"

    def test_update_plan_resets_all_counters(self) -> None:
        state = PlanState()
        state.replace(normalize_todos(_TODOS))
        tracker = self._tracker(state)
        tracker.observe_round(["search"])
        tracker.observe_round(["search"])
        assert tracker.observe_round(["update_plan"]) is None
        # Counters restarted: two more rounds stay silent.
        assert tracker.observe_round(["search"]) is None
        assert tracker.observe_round(["search"]) is None

    def test_repetition_fires_at_threshold_and_multiples(self) -> None:
        tracker = self._tracker()
        kinds = []
        for _ in range(8):
            r = tracker.observe_round(["web_search"])
            kinds.append(r.kind if r else None)
        assert kinds[3] == "repetition"
        assert kinds[7] == "repetition"
        assert kinds[4] is None  # no spam between multiples

    def test_mixed_round_breaks_streak(self) -> None:
        # Completed plan keeps the stale/no-plan branches quiet so only
        # the repetition streak is under test.
        state = PlanState()
        state.replace([{"content": "x", "status": "completed"}])
        tracker = self._tracker(state)
        for _ in range(3):
            tracker.observe_round(["web_search"])
        tracker.observe_round(["web_search", "web_fetch"])  # mixed round
        # Streak restarted — the 4th consecutive single-tool round after
        # the break is round 4 of a new streak.
        assert tracker.observe_round(["web_search"]) is None

    def test_no_plan_nudge_is_one_shot(self) -> None:
        tracker = self._tracker()
        results = [
            # Alternate tools so the repetition streak never forms.
            tracker.observe_round(["a" if i % 2 else "b"])
            for i in range(12)
        ]
        nudges = [r for r in results if r is not None]
        assert len(nudges) == 1
        assert nudges[0].kind == "no_plan"
        assert results[4] is not None  # fired exactly at round 5

    def test_quiet_when_plan_fully_completed(self) -> None:
        state = PlanState()
        state.replace([{"content": "x", "status": "completed"}])
        tracker = self._tracker(state)
        results = [
            tracker.observe_round(["a" if i % 2 else "b"]) for i in range(10)
        ]
        assert all(r is None for r in results)


class TestMakeOpenPlanNote:
    def test_embeds_checklist(self) -> None:
        state = PlanState()
        state.replace(normalize_todos(_TODOS))
        note = make_open_plan_note(state)
        assert "unfinished items" in note
        assert "[~] step two" in note
        assert "update_plan" in note


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
        # 3 rounds: below both the repetition threshold (4) and the
        # no-plan nudge threshold (5) — the loop must stay quiet.
        responses = [
            _tool_call("echo", {"text": "a"}),
            _tool_call("echo", {"text": "b"}),
            _tool_call("echo", {"text": "c"}),
            _final("done"),
        ]
        agent = self._make_agent(responses)
        result = await agent.run("simple task")
        assert not any(
            isinstance(m.content, str) and m.content.startswith("<plan-reminder>")
            for m in result.messages
        )

    async def test_repetition_reminder_after_grinding(self) -> None:
        # update_plan → 4 same-tool rounds: stale fires after round 3,
        # repetition escalation fires at round 4.
        responses = [
            _tool_call("update_plan", {"todos": _TODOS}),
            _tool_call("echo", {"text": "a"}),
            _tool_call("echo", {"text": "b"}),
            _tool_call("echo", {"text": "c"}),
            _tool_call("echo", {"text": "d"}),
            _final("done"),
        ]
        agent = self._make_agent(responses)
        result = await agent.run("grinding task")
        reminders = [
            m.content
            for m in result.messages
            if m.role == "user"
            and isinstance(m.content, str)
            and m.content.startswith("<plan-reminder>")
        ]
        assert len(reminders) == 2
        assert "has not been updated" in reminders[0]
        assert "4 rounds in a row" in reminders[1]
        assert "'echo'" in reminders[1]

    async def test_open_plan_forces_completion_check(self) -> None:
        # A long answer normally skips the completion check; open plan
        # items must force it anyway, with the checklist embedded.
        long_answer = "x" * 900
        responses = [
            _tool_call("update_plan", {"todos": _TODOS}),
            _final(long_answer),
            _final("verified, step two was not actually needed"),
            _final("real answer"),
        ]
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=FakeLLM(responses),
            tools=registry,
            use_native_tools=False,
            completion_check=True,
            max_iterations=20,
        )
        result = await agent.run("task with open plan")
        assert result.answer == "real answer"
        check_msgs = [
            m.content
            for m in result.messages
            if m.role == "user"
            and isinstance(m.content, str)
            and "unfinished items" in m.content
        ]
        assert len(check_msgs) == 1
        assert "[~] step two" in check_msgs[0]

    async def test_completed_plan_does_not_force_check(self) -> None:
        # All items completed → the long-answer skip applies as before.
        done_todos = [{"content": "only step", "status": "completed"}]
        long_answer = "y" * 900
        responses = [
            _tool_call("update_plan", {"todos": done_todos}),
            _final(long_answer),
        ]
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=FakeLLM(responses),
            tools=registry,
            use_native_tools=False,
            completion_check=True,
            max_iterations=20,
        )
        result = await agent.run("task fully done")
        assert result.answer == long_answer
        assert not any(
            isinstance(m.content, str) and "unfinished items" in m.content
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

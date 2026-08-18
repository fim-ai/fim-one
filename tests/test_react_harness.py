"""Tests for ReAct agent harness improvements: cycle detection and completion checklist.

Cycle detection injects a deterministic warning when the same tool is called
with identical arguments repeatedly.  The completion checklist injects a
one-time verification prompt before accepting a final answer when the agent
has used at least one tool.

Tests cover both JSON mode (_run_json) and native function-calling mode
(_run_native).
"""

from __future__ import annotations

import pytest

from fim_one.core.agent import ReActAgent
from fim_one.core.agent.react import (
    _COMPLETION_CHECK_PROMPT,
    _CYCLE_DETECTION_THRESHOLD,
    _CYCLE_WARNING_TEMPLATE,
)
from fim_one.core.memory.compact import SUMMARY_PREFIX
from fim_one.core.model import ChatMessage
from fim_one.core.tool import BaseTool, ToolRegistry

from .conftest import EchoTool
from .fake_llm import (
    NATIVE_TOOLS,
    FakeLLM,
    FakeResponse,
    answer,
    react_final_answer,
    react_tool_call,
    tool_call,
)


# ======================================================================
# Helpers for finding injected messages
# ======================================================================


def _find_cycle_warnings(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Return all user messages that contain a cycle detection warning."""
    return [
        m for m in messages
        if m.role == "user" and m.content and "\u26a0" in str(m.content)
        and "identical arguments" in str(m.content)
    ]


def _find_completion_checks(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Return all user messages that contain the completion checklist."""
    return [
        m for m in messages
        if m.role == "user" and m.content
        and "Before your answer is delivered" in str(m.content)
    ]


def _find_answer_requests(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Return all user messages asking for the post-check user-facing answer."""
    return [
        m for m in messages
        if m.role == "user" and m.content
        and "Now write the final answer for the user" in str(m.content)
    ]


# ======================================================================
# Tests -- Cycle Detection (JSON mode)
# ======================================================================


class TestCycleDetectionJsonMode:
    """Cycle detection in JSON mode (tool_call=False LLM)."""

    async def test_cycle_triggers_after_threshold(self) -> None:
        """Identical tool calls should trigger a warning after the threshold."""
        # Call echo with the same args _CYCLE_DETECTION_THRESHOLD times,
        # then produce a final answer.
        identical_args = {"text": "same_input"}
        responses: list[FakeResponse] = [
            react_tool_call("echo", identical_args)
            for _ in range(_CYCLE_DETECTION_THRESHOLD)
        ]
        responses.append(react_final_answer())

        llm = FakeLLM(responses)
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, max_iterations=20,
            completion_check=False,
        )

        result = await agent.run("test cycle")

        assert result.answer == "done"

        # The LLM call after the 3rd identical tool call should see
        # the cycle warning in its message history.
        last_call_messages = llm.all_messages[-1]
        warnings = _find_cycle_warnings(last_call_messages)
        assert len(warnings) >= 1, (
            f"Expected at least 1 cycle warning after {_CYCLE_DETECTION_THRESHOLD} "
            f"identical calls, found {len(warnings)}"
        )

    async def test_no_cycle_for_different_args(self) -> None:
        """Tool calls with different arguments should NOT trigger a warning."""
        responses: list[FakeResponse] = [
            react_tool_call("echo", {"text": f"input_{i}"})
            for i in range(_CYCLE_DETECTION_THRESHOLD + 1)
        ]
        responses.append(react_final_answer())

        llm = FakeLLM(responses)
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, max_iterations=20,
            completion_check=False,
        )

        result = await agent.run("test no cycle")

        assert result.answer == "done"

        # No cycle warning should appear because all args are different.
        for call_messages in llm.all_messages:
            warnings = _find_cycle_warnings(call_messages)
            assert len(warnings) == 0, (
                "No cycle warning should appear with different arguments"
            )

    async def test_cycle_below_threshold_no_warning(self) -> None:
        """Fewer identical calls than the threshold should not trigger a warning."""
        identical_args = {"text": "repeat"}
        responses: list[FakeResponse] = [
            react_tool_call("echo", identical_args)
            for _ in range(_CYCLE_DETECTION_THRESHOLD - 1)
        ]
        responses.append(react_final_answer())

        llm = FakeLLM(responses)
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, max_iterations=20,
            completion_check=False,
        )

        result = await agent.run("test below threshold")

        assert result.answer == "done"

        for call_messages in llm.all_messages:
            warnings = _find_cycle_warnings(call_messages)
            assert len(warnings) == 0

    async def test_cycle_resets_between_runs(self) -> None:
        """Cycle tracking state should reset on each run() call."""
        identical_args = {"text": "repeat"}

        # First run: 2 identical calls (below threshold), then answer.
        responses1: list[FakeResponse] = [
            react_tool_call("echo", identical_args)
            for _ in range(_CYCLE_DETECTION_THRESHOLD - 1)
        ]
        responses1.append(react_final_answer("first"))

        llm1 = FakeLLM(responses1)
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm1, tools=registry, max_iterations=20,
            completion_check=False,
        )

        result1 = await agent.run("run 1")
        assert result1.answer == "first"

        # Second run (same agent, new LLM responses): 2 identical calls again.
        # If tracking persisted, this would be 4 total and trigger.
        # But tracking should reset, so no warning.
        responses2: list[FakeResponse] = [
            react_tool_call("echo", identical_args)
            for _ in range(_CYCLE_DETECTION_THRESHOLD - 1)
        ]
        responses2.append(react_final_answer("second"))

        # Replace the LLM responses (agent uses the same LLM object).
        llm2 = FakeLLM(responses2)
        agent._llm = llm2
        agent._tool_llm = llm2

        result2 = await agent.run("run 2")
        assert result2.answer == "second"

        for call_messages in llm2.all_messages:
            warnings = _find_cycle_warnings(call_messages)
            assert len(warnings) == 0, (
                "Cycle tracker should reset between runs"
            )


# ======================================================================
# Tests -- Cycle Detection (Native mode)
# ======================================================================


class TestCycleDetectionNativeMode:
    """Cycle detection in native function-calling mode."""

    async def test_cycle_triggers_after_threshold_native(self) -> None:
        """Identical native tool calls should trigger a warning after threshold."""
        identical_args = {"text": "same_input"}
        responses: list[FakeResponse] = [
            tool_call("echo", identical_args, call_id=f"c{i}")
            for i in range(_CYCLE_DETECTION_THRESHOLD)
        ]
        responses.append(answer("done"))

        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=responses)
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, use_native_tools=True,
            max_iterations=20, completion_check=False,
        )

        result = await agent.run("test native cycle")

        assert result.answer == "done"

        # Check that a cycle warning was seen by the LLM.
        last_call_messages = llm.all_messages[-1]
        warnings = _find_cycle_warnings(last_call_messages)
        assert len(warnings) >= 1

    async def test_no_cycle_for_different_args_native(self) -> None:
        """Different arguments in native mode should not trigger a warning."""
        responses: list[FakeResponse] = [
            tool_call("echo", {"text": f"input_{i}"}, call_id=f"c{i}")
            for i in range(_CYCLE_DETECTION_THRESHOLD + 1)
        ]
        responses.append(answer("done"))

        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=responses)
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, use_native_tools=True,
            max_iterations=20, completion_check=False,
        )

        result = await agent.run("test native no cycle")

        assert result.answer == "done"

        for call_messages in llm.all_messages:
            warnings = _find_cycle_warnings(call_messages)
            assert len(warnings) == 0


# ======================================================================
# Tests -- Completion Checklist (JSON mode)
# ======================================================================


class TestCompletionChecklistJsonMode:
    """Completion checklist in JSON mode."""

    async def test_checklist_triggers_when_tools_used(self) -> None:
        """Completion checklist should trigger when tool_call_count >= threshold."""
        # 3 tool calls (meets default threshold), then final answer
        # (which triggers checklist), then the verification turn, then the
        # real user-facing answer.
        responses: list[FakeResponse] = [
            react_tool_call("echo", {"text": "a"}),
            react_tool_call("echo", {"text": "b"}),
            react_tool_call("echo", {"text": "c"}),
            react_final_answer("first attempt"),
            react_final_answer("Let me verify: 1. yes 2. yes 3. no contradictions"),
            react_final_answer("verified answer"),
        ]

        llm = FakeLLM(responses)
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, max_iterations=20,
            completion_check=True,
        )

        result = await agent.run("test completion check")

        # The verification turn is internal — it must never surface as the
        # user-facing answer.
        assert result.answer == "verified answer"

        # 3 tool calls + final answer + checklist + answer round = 6 calls.
        assert llm.call_count == 6
        assert len(_find_completion_checks(llm.all_messages[4])) == 1
        assert len(_find_answer_requests(llm.all_messages[4])) == 0
        assert len(_find_answer_requests(llm.all_messages[5])) == 1

    async def test_verification_turn_never_becomes_the_answer(self) -> None:
        """A checklist-narrating turn must not be returned to the user."""
        narration = (
            "Let me verify:\n1. Fully addressed? Yes\n"
            "2. Facts verified? Yes\n3. Contradictions? None\n"
            "Final answer verified. ✓"
        )
        responses: list[FakeResponse] = [
            react_tool_call("echo", {"text": "a"}),
            react_tool_call("echo", {"text": "b"}),
            react_tool_call("echo", {"text": "c"}),
            react_final_answer("there are 5 admins"),
            react_final_answer(narration),
            react_final_answer("There are 5 admins."),
        ]

        llm = FakeLLM(responses)
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, max_iterations=20,
            completion_check=True,
        )

        result = await agent.run("how many admins")

        assert result.answer == "There are 5 admins."
        assert "Let me verify" not in result.answer

    async def test_verification_with_tool_calls_still_consumed(self) -> None:
        """A verify turn that re-runs tools is still the verify turn.

        Prod bug 2026-08-04: the verification turn re-called a tool to
        double-check a fact; the tool-call path used to clear
        verification_pending, so the following narration ("核查完成…
        可以交付最终答案") leaked to the user as the final answer.
        """
        responses: list[FakeResponse] = [
            react_tool_call("echo", {"text": "a"}),
            react_tool_call("echo", {"text": "b"}),
            react_tool_call("echo", {"text": "c"}),
            react_final_answer("first attempt"),
            # Verification turn double-checks a fact with a tool call...
            react_tool_call("echo", {"text": "double-check"}),
            # ...then narrates the verification outcome.
            react_final_answer("核查完成，所有关键事实均已验证。可以交付最终答案。"),
            react_final_answer("The top repo is build-your-own-x with 535,848 stars."),
        ]

        llm = FakeLLM(responses)
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, max_iterations=20,
            completion_check=True,
        )

        result = await agent.run("top starred repo")

        assert result.answer == "The top repo is build-your-own-x with 535,848 stars."
        assert "核查完成" not in result.answer
        assert llm.call_count == 7
        # The answer request lands right after the narration turn.
        assert len(_find_answer_requests(llm.all_messages[6])) == 1

    async def test_checklist_skipped_without_room_for_answer_round(self) -> None:
        """No checklist when the budget cannot also cover the answer round."""
        # max_iterations=5: iteration 4 is the final answer; injecting the
        # checklist would leave only iteration 5 for the verification turn
        # and none for the real answer, so the check must not fire.
        responses: list[FakeResponse] = [
            react_tool_call("echo", {"text": "a"}),
            react_tool_call("echo", {"text": "b"}),
            react_tool_call("echo", {"text": "c"}),
            react_final_answer("answer without check"),
        ]

        llm = FakeLLM(responses)
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, max_iterations=5,
            completion_check=True,
        )

        result = await agent.run("tight budget")

        assert result.answer == "answer without check"
        assert llm.call_count == 4
        for call_messages in llm.all_messages:
            assert len(_find_completion_checks(call_messages)) == 0

    async def test_checklist_not_triggered_below_threshold(self) -> None:
        """No checklist when tool_call_count is below the min threshold."""
        # Only 1 tool call (below default threshold of 3).
        responses: list[FakeResponse] = [
            react_tool_call("echo", {"text": "data"}),
            react_final_answer("quick answer"),
        ]

        llm = FakeLLM(responses)
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, max_iterations=20,
            completion_check=True,
        )

        result = await agent.run("simple question")

        assert result.answer == "quick answer"
        assert llm.call_count == 2

        for call_messages in llm.all_messages:
            checks = _find_completion_checks(call_messages)
            assert len(checks) == 0

    async def test_checklist_not_triggered_for_simple_conversation(self) -> None:
        """No checklist when no tools were used (simple conversational response)."""
        responses: list[FakeResponse] = [
            react_final_answer("hello there"),
        ]

        llm = FakeLLM(responses)
        registry = ToolRegistry()
        agent = ReActAgent(
            llm=llm, tools=registry, max_iterations=20,
            completion_check=True,
        )

        result = await agent.run("greet me")

        assert result.answer == "hello there"
        assert result.iterations == 1
        assert llm.call_count == 1

        # No completion check should have been injected.
        for call_messages in llm.all_messages:
            checks = _find_completion_checks(call_messages)
            assert len(checks) == 0

    async def test_checklist_only_triggers_once(self) -> None:
        """Completion checklist should only trigger once per run."""
        # 3 tool calls, final answer (triggers checklist), tool call
        # (agent decided to continue after checklist), text turn (still
        # consumed as the verification — the flag survives tool calls),
        # then the real user-facing answer.
        responses: list[FakeResponse] = [
            react_tool_call("echo", {"text": "a"}),
            react_tool_call("echo", {"text": "b"}),
            react_tool_call("echo", {"text": "c"}),
            react_final_answer("first attempt"),
            # After checklist, LLM continues investigating
            react_tool_call("echo", {"text": "more data"}),
            react_final_answer("verification summary after investigation"),
            react_final_answer("final answer after investigation"),
        ]

        llm = FakeLLM(responses)
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, max_iterations=20,
            completion_check=True,
        )

        result = await agent.run("test checklist once")

        assert result.answer == "final answer after investigation"

        # The checklist prompt should appear exactly once across all calls.
        # It persists in history after injection, so later calls see it too,
        # but no *new* injection occurs.
        if llm.call_count >= 5:
            checks_in_fifth = _find_completion_checks(llm.all_messages[4])
            assert len(checks_in_fifth) == 1
        if llm.call_count >= 6:
            checks_in_sixth = _find_completion_checks(llm.all_messages[5])
            assert len(checks_in_sixth) == 1

    async def test_checklist_disabled(self) -> None:
        """When completion_check=False, no checklist even above threshold."""
        responses: list[FakeResponse] = [
            react_tool_call("echo", {"text": "a"}),
            react_tool_call("echo", {"text": "b"}),
            react_tool_call("echo", {"text": "c"}),
            react_final_answer("immediate answer"),
        ]

        llm = FakeLLM(responses)
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, max_iterations=20,
            completion_check=False,
        )

        result = await agent.run("test disabled checklist")

        assert result.answer == "immediate answer"
        assert llm.call_count == 4

        for call_messages in llm.all_messages:
            checks = _find_completion_checks(call_messages)
            assert len(checks) == 0


# ======================================================================
# Tests -- Completion Checklist (Native mode)
# ======================================================================


class TestCompletionChecklistNativeMode:
    """Completion checklist in native function-calling mode."""

    async def test_checklist_triggers_when_tools_used_native(self) -> None:
        """Completion checklist triggers in native mode at threshold."""
        responses: list[FakeResponse] = [
            tool_call("echo", {"text": "a"}, call_id="c1"),
            tool_call("echo", {"text": "b"}, call_id="c2"),
            tool_call("echo", {"text": "c"}, call_id="c3"),
            answer("first attempt"),
            answer("Let me verify: 1. yes 2. yes 3. none"),
            answer("verified answer"),
        ]

        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=responses)
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, use_native_tools=True,
            max_iterations=20, completion_check=True,
        )

        result = await agent.run("test native completion check")

        # The verification turn is consumed internally.
        assert result.answer == "verified answer"
        assert llm.call_count == 6

        assert len(_find_completion_checks(llm.all_messages[4])) == 1
        assert len(_find_answer_requests(llm.all_messages[5])) == 1

    async def test_verification_with_tool_calls_still_consumed_native(self) -> None:
        """Native mode: verify turn re-running tools stays internal (prod 2026-08-04)."""
        responses: list[FakeResponse] = [
            tool_call("echo", {"text": "a"}, call_id="c1"),
            tool_call("echo", {"text": "b"}, call_id="c2"),
            tool_call("echo", {"text": "c"}, call_id="c3"),
            answer("first attempt"),
            # Verification turn double-checks with a tool call...
            tool_call("echo", {"text": "double-check"}, call_id="c4"),
            # ...then narrates the verification outcome.
            answer("核查完成，可以交付最终答案。"),
            answer("The top repo has 535,848 stars."),
        ]

        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=responses)
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, use_native_tools=True,
            max_iterations=20, completion_check=True,
        )

        result = await agent.run("top starred repo native")

        assert result.answer == "The top repo has 535,848 stars."
        assert "核查完成" not in result.answer
        assert llm.call_count == 7
        assert len(_find_answer_requests(llm.all_messages[6])) == 1

    async def test_checklist_not_triggered_no_tools_native(self) -> None:
        """No checklist in native mode when no tools were used."""
        responses: list[FakeResponse] = [
            answer("simple answer"),
        ]

        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=responses)
        registry = ToolRegistry()
        agent = ReActAgent(
            llm=llm, tools=registry, use_native_tools=True,
            max_iterations=20, completion_check=True,
        )

        result = await agent.run("greet me native")

        assert result.answer == "simple answer"
        assert llm.call_count == 1

        for call_messages in llm.all_messages:
            checks = _find_completion_checks(call_messages)
            assert len(checks) == 0

    async def test_checklist_only_triggers_once_native(self) -> None:
        """Completion checklist fires only once in native mode."""
        responses: list[FakeResponse] = [
            tool_call("echo", {"text": "a"}, call_id="c1"),
            tool_call("echo", {"text": "b"}, call_id="c2"),
            tool_call("echo", {"text": "c"}, call_id="c3"),
            answer("first attempt"),
            # After checklist, agent continues investigating; the next text
            # turn is still consumed as the verification.
            tool_call("echo", {"text": "more"}, call_id="c4"),
            answer("verification summary"),
            answer("final"),
        ]

        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=responses)
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm, tools=registry, use_native_tools=True,
            max_iterations=20, completion_check=True,
        )

        result = await agent.run("test native checklist once")

        assert result.answer == "final"

        if llm.call_count >= 5:
            checks_in_fifth = _find_completion_checks(llm.all_messages[4])
            assert len(checks_in_fifth) == 1
        if llm.call_count >= 6:
            checks_in_sixth = _find_completion_checks(llm.all_messages[5])
            assert len(checks_in_sixth) == 1


# ======================================================================
# Tests -- Constants and helper methods
# ======================================================================


class TestHarnessConstants:
    """Verify the harness constants and helper methods."""

    def test_cycle_detection_threshold_default(self) -> None:
        """The default cycle detection threshold should be 2."""
        assert _CYCLE_DETECTION_THRESHOLD == 2

    def test_cycle_warning_template_has_placeholders(self) -> None:
        """The cycle warning template must have tool_name and count placeholders."""
        assert "{tool_name}" in _CYCLE_WARNING_TEMPLATE
        assert "{count}" in _CYCLE_WARNING_TEMPLATE

    def test_cycle_warning_renders_correctly(self) -> None:
        """The cycle warning should render without errors."""
        rendered = _CYCLE_WARNING_TEMPLATE.format(tool_name="echo", count=3)
        assert "echo" in rendered
        assert "3" in rendered
        assert "identical arguments" in rendered

    def test_completion_check_prompt_content(self) -> None:
        """The completion check prompt should contain verification instructions."""
        assert "verify" in _COMPLETION_CHECK_PROMPT.lower()
        assert "original question" in _COMPLETION_CHECK_PROMPT.lower()
        assert "contradictions" in _COMPLETION_CHECK_PROMPT.lower()

    def test_compute_args_hash_deterministic(self) -> None:
        """Same args should always produce the same hash."""
        h1 = ReActAgent._compute_args_hash({"a": 1, "b": "hello"})
        h2 = ReActAgent._compute_args_hash({"b": "hello", "a": 1})
        assert h1 == h2

    def test_compute_args_hash_different_for_different_args(self) -> None:
        """Different args should produce different hashes."""
        h1 = ReActAgent._compute_args_hash({"text": "hello"})
        h2 = ReActAgent._compute_args_hash({"text": "world"})
        assert h1 != h2

    def test_compute_args_hash_handles_none(self) -> None:
        """None args should produce the same hash as empty dict."""
        h1 = ReActAgent._compute_args_hash(None)
        h2 = ReActAgent._compute_args_hash({})
        assert h1 == h2


class TestHistoryReplay:
    """History replayed from memory keeps carried context, drops stale prompts.

    A compaction summary is emitted with ``role="system"``.  Replaying
    history with a blanket ``role != "system"`` filter threw it away every
    turn, so long conversations paid for a summarization call whose output
    never reached the model.
    """

    def test_stale_system_prompt_is_dropped(self) -> None:
        history = [ChatMessage(role="system", content="You are a pirate.")]
        assert ReActAgent._history_to_replay(history) == []

    def test_compaction_summary_survives(self) -> None:
        summary = ChatMessage(
            role="system",
            content=f"{SUMMARY_PREFIX}user wants the Q2 report translated",
        )
        assert ReActAgent._history_to_replay([summary]) == [summary]

    def test_conversation_turns_pass_through(self) -> None:
        history = [
            ChatMessage(role="user", content="hi"),
            ChatMessage(role="assistant", content="hello"),
            ChatMessage(role="tool", content="result", tool_call_id="c1"),
        ]
        assert ReActAgent._history_to_replay(history) == history

    def test_order_is_preserved_in_a_mixed_history(self) -> None:
        summary = ChatMessage(role="system", content=f"{SUMMARY_PREFIX}earlier work")
        history = [
            ChatMessage(role="system", content="stale prompt"),
            summary,
            ChatMessage(role="user", content="continue"),
        ]
        replayed = ReActAgent._history_to_replay(history)
        assert [m.content for m in replayed] == [summary.content, "continue"]

    def test_non_string_system_content_is_not_mistaken_for_a_summary(self) -> None:
        """Vision arrays must not crash the ``startswith`` check."""
        history = [
            ChatMessage(
                role="system",
                content=[{"type": "text", "text": SUMMARY_PREFIX}],
            ),
        ]
        assert ReActAgent._history_to_replay(history) == []

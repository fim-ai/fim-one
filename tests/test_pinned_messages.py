"""Tests for the Pinned Messages mechanism.

Validates that pinned messages survive context compaction via both
ContextGuard (LLM compact) and CompactUtils (smart_truncate / llm_compact).
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from fim_agent.core.memory.compact import CompactUtils
from fim_agent.core.memory.context_guard import ContextGuard
from fim_agent.core.model.types import ChatMessage, LLMResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msg(role: str, text: str, *, pinned: bool = False) -> ChatMessage:
    """Shortcut to build a ChatMessage."""
    return ChatMessage(role=role, content=text, pinned=pinned)


def _make_fake_llm(summary: str = "Summary of old messages") -> AsyncMock:
    """Create a mock LLM that returns a fixed summary."""
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=LLMResult(
        message=ChatMessage(role="assistant", content=summary),
        usage={},
    ))
    return llm


# ---------------------------------------------------------------------------
# 1. ChatMessage.pinned defaults & serialisation
# ---------------------------------------------------------------------------


class TestChatMessagePinned:
    def test_pinned_defaults_to_false(self):
        msg = ChatMessage(role="user", content="hello")
        assert msg.pinned is False

    def test_pinned_can_be_set_true(self):
        msg = ChatMessage(role="user", content="hello", pinned=True)
        assert msg.pinned is True

    def test_to_openai_dict_excludes_pinned(self):
        msg = ChatMessage(role="user", content="hello", pinned=True)
        d = msg.to_openai_dict()
        assert "pinned" not in d
        assert d == {"role": "user", "content": "hello"}


# ---------------------------------------------------------------------------
# 2. ContextGuard._truncate_oversized preserves pinned flag
# ---------------------------------------------------------------------------


class TestTruncateOversizedPreservesPinned:
    def test_truncated_message_keeps_pinned_flag(self):
        guard = ContextGuard(max_message_chars=10)
        pinned_msg = _msg("user", "A" * 100, pinned=True)
        unpinned_msg = _msg("user", "B" * 100, pinned=False)

        result = guard._truncate_oversized([pinned_msg, unpinned_msg])

        assert len(result) == 2
        assert result[0].pinned is True
        assert result[1].pinned is False
        # Both should be truncated
        assert "[Truncated]" in result[0].content
        assert "[Truncated]" in result[1].content


# ---------------------------------------------------------------------------
# 3. ContextGuard LLM compact preserves pinned messages
# ---------------------------------------------------------------------------


class TestContextGuardLLMCompactPinned:
    @pytest.mark.asyncio
    async def test_pinned_messages_survive_llm_compact(self):
        """Pinned non-system messages must appear in compacted output."""
        fake_llm = _make_fake_llm("Old messages summarised.")
        guard = ContextGuard(compact_llm=fake_llm, default_budget=50)

        messages = [
            _msg("system", "You are helpful."),
            _msg("user", "My task is X.", pinned=True),
            # Enough compactable messages to trigger split (>4)
            _msg("assistant", "Step 1 reasoning " * 20),
            _msg("user", "Tool result 1 " * 20),
            _msg("assistant", "Step 2 reasoning " * 20),
            _msg("user", "Tool result 2 " * 20),
            _msg("assistant", "Step 3 reasoning " * 20),
            _msg("user", "Recent question"),
            _msg("assistant", "Recent answer 1"),
            _msg("user", "Another question"),
            _msg("assistant", "Another answer"),
        ]

        result = await guard.check_and_compact(messages, budget=200)

        # The pinned message must be present
        pinned_in_result = [m for m in result if m.pinned]
        assert len(pinned_in_result) == 1
        assert pinned_in_result[0].content == "My task is X."

        # System message must be present
        system_in_result = [m for m in result if m.role == "system"]
        assert len(system_in_result) >= 1

        # Summary message should exist
        summary_msgs = [
            m for m in result
            if m.role == "system" and "[Conversation summary]" in (m.content or "")
        ]
        assert len(summary_msgs) == 1

    @pytest.mark.asyncio
    async def test_pinned_not_summarised(self):
        """The pinned message content must NOT appear in the text sent to the LLM for summarisation."""
        fake_llm = _make_fake_llm("Summary.")
        guard = ContextGuard(compact_llm=fake_llm, default_budget=50)

        pinned_content = "UNIQUE_PINNED_TASK_DESCRIPTION"
        messages = [
            _msg("system", "System prompt."),
            _msg("user", pinned_content, pinned=True),
            _msg("assistant", "Reply 1 " * 30),
            _msg("user", "Follow-up 1 " * 30),
            _msg("assistant", "Reply 2 " * 30),
            _msg("user", "Follow-up 2 " * 30),
            _msg("assistant", "Reply 3 " * 30),
            _msg("user", "Recent 1"),
            _msg("assistant", "Recent 2"),
            _msg("user", "Recent 3"),
            _msg("assistant", "Recent 4"),
        ]

        await guard.check_and_compact(messages, budget=200)

        # Check what was sent to the LLM for summarisation
        call_args = fake_llm.chat.call_args[0][0]
        history_text = call_args[1].content  # The user message with history
        assert pinned_content not in history_text


# ---------------------------------------------------------------------------
# 4. CompactUtils.smart_truncate preserves pinned messages
# ---------------------------------------------------------------------------


class TestSmartTruncatePreservesPinned:
    def test_pinned_messages_always_kept(self):
        """Even with a tight budget, pinned messages must survive."""
        messages = [
            _msg("user", "This is my pinned task.", pinned=True),
            _msg("assistant", "Old reply " * 50),
            _msg("user", "Old question " * 50),
            _msg("assistant", "Another old reply " * 50),
            _msg("user", "Recent question"),
            _msg("assistant", "Recent answer"),
        ]

        # Use a budget that can't fit everything
        result = CompactUtils.smart_truncate(messages, max_tokens=100)

        # Pinned message must be in result
        pinned_in_result = [m for m in result if m.pinned]
        assert len(pinned_in_result) == 1
        assert pinned_in_result[0].content == "This is my pinned task."

    def test_empty_messages(self):
        assert CompactUtils.smart_truncate([], max_tokens=100) == []

    def test_fits_in_budget_returns_all(self):
        messages = [
            _msg("user", "Short", pinned=True),
            _msg("assistant", "Reply"),
        ]
        result = CompactUtils.smart_truncate(messages, max_tokens=100_000)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# 5. CompactUtils.llm_compact preserves pinned messages
# ---------------------------------------------------------------------------


class TestLLMCompactPreservesPinned:
    @pytest.mark.asyncio
    async def test_pinned_preserved_in_llm_compact(self):
        fake_llm = _make_fake_llm("Summarised old stuff.")
        messages = [
            _msg("system", "System."),
            _msg("user", "Pinned task.", pinned=True),
            _msg("assistant", "Reply 1 " * 30),
            _msg("user", "Q2 " * 30),
            _msg("assistant", "Reply 2 " * 30),
            _msg("user", "Q3 " * 30),
            _msg("assistant", "Reply 3 " * 30),
            _msg("user", "Recent 1"),
            _msg("assistant", "Recent 2"),
            _msg("user", "Recent 3"),
            _msg("assistant", "Recent 4"),
        ]

        result = await CompactUtils.llm_compact(
            messages, llm=fake_llm, max_tokens=200, keep_recent=4,
        )

        # Pinned must survive
        pinned_in_result = [m for m in result if m.pinned]
        assert len(pinned_in_result) == 1
        assert pinned_in_result[0].content == "Pinned task."

        # System must survive
        system_in_result = [m for m in result if m.role == "system"]
        assert any("System." in (m.content or "") for m in system_in_result)

        # Summary must exist
        assert any(
            "[Conversation summary]" in (m.content or "")
            for m in result
        )


# ---------------------------------------------------------------------------
# 6. Budget warning for oversized pinned messages
# ---------------------------------------------------------------------------


class TestPinnedBudgetWarning:
    @pytest.mark.asyncio
    async def test_warning_when_pinned_exceeds_half_budget(self, caplog):
        """A warning should be logged when pinned tokens > 50% of budget."""
        fake_llm = _make_fake_llm("Summary.")
        guard = ContextGuard(compact_llm=fake_llm, default_budget=50)

        # Create a very large pinned message
        huge_pinned = _msg("user", "X" * 2000, pinned=True)
        messages = [
            _msg("system", "S"),
            huge_pinned,
            _msg("assistant", "A1"),
            _msg("user", "Q1"),
            _msg("assistant", "A2"),
            _msg("user", "Q2"),
            _msg("assistant", "A3"),
            _msg("user", "Q3"),
            _msg("assistant", "A4"),
            _msg("user", "Q4"),
            _msg("assistant", "A5"),
        ]

        with caplog.at_level(logging.WARNING, logger="fim_agent.core.memory.context_guard"):
            await guard.check_and_compact(messages, budget=100)

        assert any("pinned messages consume" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 7. model_hint agent gets context_guard (bug fix verification)
# ---------------------------------------------------------------------------


class TestModelHintAgentContextGuard:
    def test_resolve_agent_passes_context_guard(self):
        """When model_hint routes to a different LLM, the new agent must
        receive context_guard and extra_instructions from the parent."""
        from fim_agent.core.planner.executor import DAGExecutor
        from fim_agent.core.planner.types import PlanStep

        # Build a mock parent agent with context_guard set
        mock_agent = MagicMock()
        mock_agent._tools = MagicMock()
        mock_agent._system_prompt_override = None
        mock_agent._extra_instructions = "Be concise."
        mock_agent._max_iterations = 10
        mock_guard = MagicMock(spec=ContextGuard)
        mock_agent._context_guard = mock_guard

        # Build a mock model registry that returns a different LLM
        mock_registry = MagicMock()
        mock_llm = MagicMock()
        mock_registry.get_by_role.return_value = mock_llm

        executor = DAGExecutor(
            agent=mock_agent,
            model_registry=mock_registry,
            context_guard=mock_guard,
        )

        step = PlanStep(id="s1", task="Do something", model_hint="fast")
        resolved = executor._resolve_agent(step)

        # The resolved agent should have context_guard and extra_instructions
        assert resolved._context_guard is mock_guard
        assert resolved._extra_instructions == "Be concise."


# ---------------------------------------------------------------------------
# 8. _build_step_query includes original_goal
# ---------------------------------------------------------------------------


class TestBuildStepQueryOriginalGoal:
    def test_includes_original_goal(self):
        from fim_agent.core.planner.executor import DAGExecutor
        from fim_agent.core.planner.types import PlanStep

        mock_agent = MagicMock()
        executor = DAGExecutor(
            agent=mock_agent,
            original_goal="Analyse sales data for Q4",
        )

        step = PlanStep(id="s1", task="Fetch CSV from URL")
        query = executor._build_step_query(step, context="")

        assert "Original goal: Analyse sales data for Q4" in query
        assert "Task: Fetch CSV from URL" in query

    def test_no_original_goal(self):
        from fim_agent.core.planner.executor import DAGExecutor
        from fim_agent.core.planner.types import PlanStep

        mock_agent = MagicMock()
        executor = DAGExecutor(agent=mock_agent)

        step = PlanStep(id="s1", task="Fetch CSV from URL")
        query = executor._build_step_query(step, context="")

        assert "Original goal" not in query
        assert "Task: Fetch CSV from URL" in query

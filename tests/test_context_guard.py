"""Tests for ContextGuard — unified context window budget manager."""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from fim_one.core.memory.context_guard import ContextGuard, _COMPACT_PROMPTS
from fim_one.core.memory.work_card import WorkCard
from fim_one.core.model import BaseLLM, ChatMessage, LLMResult, StreamChunk


# ------------------------------------------------------------------
# Mock LLM
# ------------------------------------------------------------------


class _MockLLM(BaseLLM):
    """Minimal LLM mock for ContextGuard tests."""

    def __init__(
        self,
        response_content: str = "",
        raise_exc: Exception | None = None,
    ) -> None:
        self._response_content = response_content
        self._raise_exc = raise_exc
        self.call_count = 0
        self.last_system_prompt: str | None = None

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        reasoning_effort: Any = None,
    ) -> LLMResult:
        self.call_count += 1
        # Capture system prompt for hint verification.
        for m in messages:
            if m.role == "system":
                self.last_system_prompt = m.content
                break
        if self._raise_exc is not None:
            raise self._raise_exc
        return LLMResult(
            message=ChatMessage(role="assistant", content=self._response_content),
        )

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(delta_content="mock", finish_reason="stop")

    @property
    def abilities(self) -> dict[str, bool]:
        return {"tool_call": False, "json_mode": False}


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_messages(n: int = 10, content_len: int = 300) -> list[ChatMessage]:
    """Create alternating user/assistant messages with long content."""
    msgs: list[ChatMessage] = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append(ChatMessage(role=role, content=f"msg-{i} " + "x" * content_len))
    return msgs


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


class TestUnderBudget:
    async def test_returns_unchanged_when_under_budget(self):
        guard = ContextGuard(default_budget=50_000)
        msgs = [
            ChatMessage(role="user", content="hello"),
            ChatMessage(role="assistant", content="hi"),
        ]
        result = await guard.check_and_compact(msgs)
        assert len(result) == 2
        assert result[0].content == "hello"
        assert result[1].content == "hi"


class TestOverBudgetTriggersCompact:
    async def test_over_budget_with_llm(self):
        llm = _MockLLM(response_content="Summary of old conversation.")
        # Budget must be < total (~800 tokens) but > compacted result
        # (summary + 4 recent msgs ≈ 330 tokens).
        guard = ContextGuard(compact_llm=llm, default_budget=400)
        msgs = _make_messages(10)  # ~800 tokens, well over 400

        result = await guard.check_and_compact(msgs)

        assert llm.call_count == 1
        # Should have a summary system message.
        summaries = [
            m
            for m in result
            if m.role == "system" and "[Conversation summary]" in (m.content or "")
        ]
        assert len(summaries) == 1

    async def test_over_budget_without_llm_falls_back_to_truncate(self):
        guard = ContextGuard(compact_llm=None, default_budget=100)
        msgs = _make_messages(10)

        result = await guard.check_and_compact(msgs)

        # Should have fewer messages (truncated).
        assert len(result) < len(msgs)


class TestCompactFailureFallback:
    async def test_llm_failure_falls_back_to_truncate(self):
        llm = _MockLLM(raise_exc=RuntimeError("API error"))
        guard = ContextGuard(compact_llm=llm, default_budget=100)
        msgs = _make_messages(10)

        result = await guard.check_and_compact(msgs)

        assert llm.call_count == 1
        # Fell back to smart_truncate — should have fewer messages.
        assert len(result) < len(msgs)

    async def test_empty_summary_falls_back_to_truncate(self):
        llm = _MockLLM(response_content="")
        guard = ContextGuard(compact_llm=llm, default_budget=100)
        msgs = _make_messages(10)

        result = await guard.check_and_compact(msgs)

        assert llm.call_count == 1
        assert len(result) < len(msgs)


class TestOversizedMessageTruncated:
    async def test_single_oversized_message_truncated(self):
        guard = ContextGuard(default_budget=100_000, max_message_chars=100)
        msgs = [
            ChatMessage(role="user", content="A" * 200),
        ]

        result = await guard.check_and_compact(msgs)

        assert len(result) == 1
        assert len(result[0].content) < 200
        assert result[0].content.endswith("[Truncated]")

    async def test_small_messages_not_truncated(self):
        guard = ContextGuard(default_budget=100_000, max_message_chars=1000)
        msgs = [
            ChatMessage(role="user", content="short message"),
        ]

        result = await guard.check_and_compact(msgs)

        assert result[0].content == "short message"


class TestHintSpecificPrompts:
    async def test_react_iteration_hint_uses_correct_prompt(self):
        llm = _MockLLM(response_content="Summarized.")
        guard = ContextGuard(compact_llm=llm, default_budget=100)
        msgs = _make_messages(10)

        await guard.check_and_compact(msgs, hint="react_iteration")

        assert llm.call_count == 1
        assert llm.last_system_prompt == _COMPACT_PROMPTS["react_iteration"]

    async def test_planner_input_hint(self):
        llm = _MockLLM(response_content="Summarized.")
        guard = ContextGuard(compact_llm=llm, default_budget=100)
        msgs = _make_messages(10)

        await guard.check_and_compact(msgs, hint="planner_input")

        assert llm.last_system_prompt == _COMPACT_PROMPTS["planner_input"]

    async def test_step_dependency_hint(self):
        llm = _MockLLM(response_content="Summarized.")
        guard = ContextGuard(compact_llm=llm, default_budget=100)
        msgs = _make_messages(10)

        await guard.check_and_compact(msgs, hint="step_dependency")

        assert llm.last_system_prompt == _COMPACT_PROMPTS["step_dependency"]

    async def test_unknown_hint_falls_back_to_general(self):
        llm = _MockLLM(response_content="Summarized.")
        guard = ContextGuard(compact_llm=llm, default_budget=100)
        msgs = _make_messages(10)

        await guard.check_and_compact(msgs, hint="nonexistent_hint")

        assert llm.last_system_prompt == _COMPACT_PROMPTS["general"]

    async def test_all_prompts_exist(self):
        """Verify all expected hint keys have corresponding prompts."""
        expected = {"general", "react_iteration", "planner_input", "step_dependency"}
        assert set(_COMPACT_PROMPTS.keys()) == expected


class TestBudgetOverride:
    async def test_explicit_budget_overrides_default(self):
        guard = ContextGuard(default_budget=100_000)
        msgs = _make_messages(10)  # ~800 tokens

        # With default budget (100K), should not compact.
        result1 = await guard.check_and_compact(msgs)
        assert len(result1) == 10

        # With explicit low budget, should compact.
        result2 = await guard.check_and_compact(msgs, budget=50)
        assert len(result2) < 10


class _ScriptedLLM(BaseLLM):
    """LLM mock that replays a scripted list of canned responses."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.call_count = 0

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        reasoning_effort: Any = None,
    ) -> LLMResult:
        idx = min(self.call_count, len(self._responses) - 1)
        self.call_count += 1
        return LLMResult(
            message=ChatMessage(
                role="assistant", content=self._responses[idx],
            ),
        )

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(delta_content="", finish_reason="stop")

    @property
    def abilities(self) -> dict[str, bool]:
        return {"tool_call": False, "json_mode": False}


class TestWorkCardPersistence:
    """I.15 — ContextGuard persists and merges WorkCards across compactions."""

    async def test_last_work_card_populated_after_react_iteration_compact(
        self,
    ) -> None:
        canned = """\
## 1. Primary Request
Build feature X.

## 7. Pending Tasks
- task-alpha
- task-beta

## 4. Errors
- initial-error
"""
        llm = _ScriptedLLM(responses=[canned])
        guard = ContextGuard(compact_llm=llm, default_budget=400)
        msgs = _make_messages(10)

        assert guard._last_work_card is None
        await guard.check_and_compact(msgs, hint="react_iteration")

        card = guard._last_work_card
        assert card is not None
        assert card.primary_request == "Build feature X."
        assert card.pending_tasks == ["task-alpha", "task-beta"]
        assert card.errors == ["initial-error"]

    async def test_successive_compacts_merge_work_cards(self) -> None:
        first = """\
## 1. Primary Request
Build feature X.

## 7. Pending Tasks
- task-alpha
- task-beta

## 4. Errors
- err-one
"""
        second = """\
## 1. Primary Request
Build feature X (revised).

## 7. Pending Tasks
- task-beta
- task-gamma

## 4. Errors
- err-two
"""
        llm = _ScriptedLLM(responses=[first, second])
        guard = ContextGuard(compact_llm=llm, default_budget=400)

        await guard.check_and_compact(
            _make_messages(10), hint="react_iteration",
        )
        result = await guard.check_and_compact(
            _make_messages(10), hint="react_iteration",
        )

        assert llm.call_count == 2
        merged = guard._last_work_card
        assert merged is not None
        # Newer primary request wins.
        assert merged.primary_request == "Build feature X (revised)."
        # Pending tasks union (dedup preserving order).
        assert merged.pending_tasks == [
            "task-alpha", "task-beta", "task-gamma",
        ]
        # Errors accumulate across rounds.
        assert "err-one" in merged.errors
        assert "err-two" in merged.errors

        # The emitted summary system message reflects the merged card.
        summaries = [
            m
            for m in result
            if m.role == "system"
            and "[Conversation summary]" in (m.content or "")
        ]
        assert len(summaries) == 1
        summary_content = summaries[0].content or ""
        assert "task-alpha" in summary_content
        assert "task-gamma" in summary_content

    async def test_non_react_hint_does_not_populate_work_card(self) -> None:
        llm = _ScriptedLLM(responses=["plain summary"])
        guard = ContextGuard(compact_llm=llm, default_budget=400)

        await guard.check_and_compact(_make_messages(10), hint="general")

        assert guard._last_work_card is None


class TestSystemMessagesPreserved:
    async def test_system_messages_preserved_during_compact(self):
        llm = _MockLLM(response_content="Summary of chat.")
        # Budget < total (~880) but > compacted (system + summary + 4 recent ≈ 340).
        guard = ContextGuard(compact_llm=llm, default_budget=500)

        msgs = [
            ChatMessage(role="system", content="You are a helpful assistant."),
            *_make_messages(10),
        ]

        result = await guard.check_and_compact(msgs)

        # Original system message should be preserved.
        system_msgs = [m for m in result if m.role == "system"]
        original_system = [
            m for m in system_msgs if m.content == "You are a helpful assistant."
        ]
        assert len(original_system) == 1


# ------------------------------------------------------------------
# Detail-preservation: thinking must survive rebuilds + compaction
# ------------------------------------------------------------------


class _CapturingLLM(_MockLLM):
    """_MockLLM that records the user-role content of each chat() call."""

    def __init__(self, response_content: str = "summary") -> None:
        super().__init__(response_content=response_content)
        self.captured_user: str | None = None

    async def chat(self, messages, **kw):  # type: ignore[no-untyped-def]
        for m in messages:
            if m.role == "user" and isinstance(m.content, str):
                self.captured_user = m.content
        return await super().chat(messages, **kw)


class TestTruncateOversizedPreservesThinking:
    def test_reasoning_and_signature_survive_truncation(self) -> None:
        guard = ContextGuard(max_message_chars=10)
        msg = ChatMessage(
            role="assistant",
            content="x" * 100,
            reasoning_content="DEEP_THOUGHT",
            signature="SIG-1",
            cache_control={"type": "ephemeral"},
        )
        out = guard._truncate_oversized([msg])
        assert out[0].content is not None
        assert "[Truncated]" in out[0].content
        assert out[0].reasoning_content == "DEEP_THOUGHT"
        assert out[0].signature == "SIG-1"
        assert out[0].cache_control == {"type": "ephemeral"}


class TestCompactionSummariserSeesThinking:
    async def test_reasoning_content_reaches_summariser(self) -> None:
        llm = _CapturingLLM(response_content="summary")
        guard = ContextGuard(compact_llm=llm, default_budget=400)
        msgs = _make_messages(10)
        # Put a clue ONLY in an old assistant turn's thinking block.
        msgs[1].reasoning_content = "REASON_CLUE_XYZ"
        await guard.check_and_compact(msgs)
        assert llm.captured_user is not None
        assert "REASON_CLUE_XYZ" in llm.captured_user


class TestCompactPreservesToolPairing:
    """The recent-window boundary must never orphan role="tool" messages.

    A naive ``[-keep_recent:]`` slice can summarise away the assistant
    ``tool_calls`` turn while keeping its tool results — OpenAI and
    Anthropic both reject a conversation whose tool results have no
    preceding tool_calls message (HTTP 400).
    """

    def test_split_walks_boundary_back_to_assistant(self) -> None:
        from fim_one.core.model.types import ToolCallRequest

        assistant = ChatMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCallRequest(id=f"t{i}", name="tool", arguments={})
                for i in range(4)
            ],
        )
        tools = [
            ChatMessage(role="tool", content=f"r{i}", tool_call_id=f"t{i}")
            for i in range(4)
        ]
        filler = [
            ChatMessage(role="user", content="u"),
            ChatMessage(role="assistant", content="a"),
        ]
        compactable = filler + [assistant, *tools]

        old, recent = ContextGuard._split_on_tool_boundary(compactable, 4)

        # Naive slice would be [tool r0..r3] — the fix pulls the assistant in.
        assert recent[0] is assistant
        assert [m.content for m in recent[1:]] == ["r0", "r1", "r2", "r3"]
        assert old == filler

    def test_split_without_tools_is_plain_slice(self) -> None:
        msgs = _make_messages(8)
        old, recent = ContextGuard._split_on_tool_boundary(msgs, 4)
        assert old == msgs[:4]
        assert recent == msgs[4:]

    async def test_compact_keeps_tool_calls_turn_with_its_results(self) -> None:
        from fim_one.core.model.types import ToolCallRequest

        llm = _MockLLM(response_content="Summary of old conversation.")
        guard = ContextGuard(compact_llm=llm, default_budget=300)
        msgs = _make_messages(8, content_len=200)
        msgs.append(
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCallRequest(id=f"t{i}", name="search", arguments={})
                    for i in range(4)
                ],
            )
        )
        for i in range(4):
            msgs.append(
                ChatMessage(role="tool", content=f"result-{i}", tool_call_id=f"t{i}")
            )

        result = await guard.check_and_compact(msgs)

        tool_positions = [i for i, m in enumerate(result) if m.role == "tool"]
        assert tool_positions, "tool results should survive in the recent window"
        first_tool = tool_positions[0]
        assert any(
            m.role == "assistant" and m.tool_calls for m in result[:first_tool]
        ), "kept tool results must be preceded by their assistant tool_calls turn"

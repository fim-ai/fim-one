"""Tests for ContextGuard — unified context window budget manager."""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from fim_one.core.memory.context_guard import ContextGuard, _COMPACT_PROMPTS
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

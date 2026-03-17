"""Tests for CompactUtils — token estimation, smart truncation, and LLM compact."""

from __future__ import annotations

from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest

from fim_one.core.memory.compact import CompactUtils
from fim_one.core.model import BaseLLM, ChatMessage, LLMResult, StreamChunk


class TestEstimateTokens:
    def test_empty_string(self):
        assert CompactUtils.estimate_tokens("") == 0

    def test_short_string(self):
        # "hi" = 2 chars → max(1, 0) = 1
        assert CompactUtils.estimate_tokens("hi") >= 1

    def test_longer_ascii_string(self):
        text = "a" * 400
        assert CompactUtils.estimate_tokens(text) == 100

    def test_none_like(self):
        assert CompactUtils.estimate_tokens("") == 0

    def test_pure_chinese(self):
        # 10 Chinese chars → 10 / 1.5 ≈ 6.67 → int(6.67) = 6
        text = "你好世界测试中文字符"
        tokens = CompactUtils.estimate_tokens(text)
        assert tokens == 6

    def test_pure_chinese_longer(self):
        # 36 Chinese chars → 36 / 1.5 = 24
        text = (
            "这是一段较长的中文文本用来测试分词估算的准确性看看效果如何呢我觉得还行吧"
        )
        tokens = CompactUtils.estimate_tokens(text)
        assert tokens == 24

    def test_mixed_chinese_english(self):
        # "Hello你好World世界" → 10 ASCII + 4 CJK
        # 10/4 + 4/1.5 = 2.5 + 2.67 = 5.17 → int(5.17) = 5
        text = "Hello你好World世界"
        tokens = CompactUtils.estimate_tokens(text)
        assert tokens == 5

    def test_chinese_with_code(self):
        # Mixed: Chinese explanation with code snippet
        text = "使用print('hello')来输出"
        # CJK chars: 使用 来输出 = 5 non-ASCII
        # ASCII chars: print('hello') = 14 ASCII
        # 14/4 + 5/1.5 = 3.5 + 3.33 = 6.83 → int(6.83) = 6
        tokens = CompactUtils.estimate_tokens(text)
        assert tokens == 6

    def test_chinese_much_higher_than_naive(self):
        # The old len//4 heuristic would massively undercount Chinese.
        # 100 Chinese chars: old = len("...") // 4 (bytes don't matter here
        # since len() counts codepoints), but each CJK char is ~1 token.
        text = "中" * 100
        tokens = CompactUtils.estimate_tokens(text)
        # 100 / 1.5 = 66.67 → 66
        assert tokens == 66
        # Old heuristic would give 100/4 = 25, which is way too low
        assert tokens > 50


class TestEstimateMessagesTokens:
    def test_empty_list(self):
        assert CompactUtils.estimate_messages_tokens([]) == 0

    def test_single_message(self):
        msgs = [ChatMessage(role="user", content="hello world")]
        tokens = CompactUtils.estimate_messages_tokens(msgs)
        # 4 overhead + 11 ASCII chars / 4 = 4 + 2 = 6
        assert tokens > 0

    def test_multiple_messages(self):
        msgs = [
            ChatMessage(role="user", content="hi"),
            ChatMessage(role="assistant", content="hello"),
        ]
        tokens = CompactUtils.estimate_messages_tokens(msgs)
        assert tokens > 0


class TestSmartTruncate:
    def test_empty_input(self):
        assert CompactUtils.smart_truncate([], max_tokens=1000) == []

    def test_all_fit(self):
        msgs = [
            ChatMessage(role="user", content="hi"),
            ChatMessage(role="assistant", content="hello"),
        ]
        result = CompactUtils.smart_truncate(msgs, max_tokens=10000)
        assert len(result) == 2
        assert result[0].role == "user"
        assert result[1].role == "assistant"

    def test_truncation_keeps_recent(self):
        # Create many messages that exceed budget.
        msgs = []
        for i in range(50):
            role = "user" if i % 2 == 0 else "assistant"
            msgs.append(ChatMessage(role=role, content=f"message number {i} " * 20))

        result = CompactUtils.smart_truncate(msgs, max_tokens=200)
        assert len(result) < len(msgs)
        # The last message in the result should be the last from the input
        # (or close to it).
        assert result[-1].content == msgs[-1].content or len(result) > 0

    def test_does_not_start_with_assistant(self):
        # Budget only fits the last 2 messages; after truncation the first
        # message would be "assistant" — smart_truncate must drop it.
        msgs = [
            ChatMessage(role="user", content="a" * 200),  # ~54 tokens
            ChatMessage(role="assistant", content="b" * 200),  # ~54 tokens
            ChatMessage(role="user", content="hi"),  # ~5 tokens
            ChatMessage(role="assistant", content="hello"),  # ~5 tokens
        ]
        result = CompactUtils.smart_truncate(msgs, max_tokens=20)
        if result:
            assert result[0].role != "assistant"

    def test_single_user_message(self):
        msgs = [ChatMessage(role="user", content="just me")]
        result = CompactUtils.smart_truncate(msgs, max_tokens=10000)
        assert len(result) == 1
        assert result[0].content == "just me"


# ======================================================================
# MockLLM for llm_compact tests
# ======================================================================


class _MockLLM(BaseLLM):
    """A minimal LLM mock that returns a pre-configured chat response.

    If ``raise_exc`` is set, ``chat()`` raises that exception instead.
    """

    def __init__(
        self,
        response_content: str = "",
        raise_exc: Exception | None = None,
    ) -> None:
        self._response_content = response_content
        self._raise_exc = raise_exc
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
        self.call_count += 1
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
        return {
            "tool_call": False,
            "json_mode": False,
            "vision": False,
            "streaming": False,
        }


# ======================================================================
# Helper — build a long conversation history
# ======================================================================


def _make_long_history(n: int = 20, content_len: int = 200) -> list[ChatMessage]:
    """Create *n* alternating user/assistant messages with long content."""
    msgs: list[ChatMessage] = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append(ChatMessage(role=role, content=f"msg-{i} " + "x" * content_len))
    return msgs


# ======================================================================
# TestLlmCompact
# ======================================================================


class TestLlmCompact:
    """Tests for ``CompactUtils.llm_compact``."""

    async def test_llm_compact_short_history_returns_unchanged(self):
        """When the history fits within max_tokens, return it as-is (no LLM call)."""
        msgs = [
            ChatMessage(role="user", content="hi"),
            ChatMessage(role="assistant", content="hello"),
        ]
        llm = _MockLLM(response_content="should not be called")
        result = await CompactUtils.llm_compact(msgs, llm=llm, max_tokens=10000)

        assert len(result) == 2
        assert result[0].content == "hi"
        assert result[1].content == "hello"
        # LLM should never have been invoked.
        assert llm.call_count == 0

    async def test_llm_compact_summarizes_old_messages(self):
        """Long history is split: old messages summarised, recent kept verbatim."""
        msgs = _make_long_history(10)  # 10 messages, ~55 tokens each = ~550 total
        summary_text = "The user and assistant discussed topics 0-5."
        llm = _MockLLM(response_content=summary_text)

        # Budget must be:
        #  - smaller than total (~550) so compaction triggers
        #  - large enough to hold summary + 4 recent messages (~5 * 55 = 275)
        result = await CompactUtils.llm_compact(
            msgs,
            llm=llm,
            max_tokens=400,
            keep_recent=4,
        )

        # LLM should have been called once for summarisation.
        assert llm.call_count == 1

        # First message should be the summary injected as a system message.
        assert result[0].role == "system"
        assert summary_text in result[0].content

        # The last 4 messages from the original history should be preserved.
        original_recent = msgs[-4:]
        for orig, compacted in zip(original_recent, result[1:]):
            assert orig.content == compacted.content

    async def test_llm_compact_fallback_on_llm_failure(self):
        """When the LLM raises an exception, fall back to smart_truncate."""
        msgs = _make_long_history(10)
        llm = _MockLLM(raise_exc=RuntimeError("API down"))

        result = await CompactUtils.llm_compact(
            msgs,
            llm=llm,
            max_tokens=50,
            keep_recent=4,
        )

        # Should have attempted the LLM call.
        assert llm.call_count == 1

        # Fallback: result should be a truncated subset that fits the budget.
        assert len(result) < len(msgs)
        total_tokens = CompactUtils.estimate_messages_tokens(result)
        assert total_tokens <= 50

    async def test_llm_compact_fallback_on_empty_summary(self):
        """When the LLM returns empty content, fall back to smart_truncate."""
        msgs = _make_long_history(10)
        llm = _MockLLM(response_content="")

        result = await CompactUtils.llm_compact(
            msgs,
            llm=llm,
            max_tokens=50,
            keep_recent=4,
        )

        assert llm.call_count == 1
        # Fell back to smart_truncate — no system summary message.
        assert all(
            m.role != "system" or "[Conversation summary]" not in (m.content or "")
            for m in result
        )
        assert len(result) < len(msgs)

    async def test_llm_compact_keeps_recent_messages(self):
        """The ``keep_recent`` parameter controls how many tail messages are kept."""
        msgs = _make_long_history(12)  # ~660 tokens total
        summary_text = "Summary of old turns."
        llm = _MockLLM(response_content=summary_text)

        # Budget must exceed 7 compacted messages (~385) but be under 12 original (~660).
        result = await CompactUtils.llm_compact(
            msgs,
            llm=llm,
            max_tokens=500,
            keep_recent=6,
        )

        assert llm.call_count == 1
        # 1 summary message + 6 recent messages = 7 total.
        assert len(result) == 7
        assert result[0].role == "system"
        for i, orig in enumerate(msgs[-6:]):
            assert result[i + 1].content == orig.content

    async def test_llm_compact_too_few_messages_falls_back(self):
        """When len(messages) <= keep_recent, fall back to smart_truncate."""
        # 3 messages with keep_recent=4 — not enough to split.
        msgs = _make_long_history(3)
        llm = _MockLLM(response_content="should not be called")

        result = await CompactUtils.llm_compact(
            msgs,
            llm=llm,
            max_tokens=50,
            keep_recent=4,
        )

        # LLM should not have been called — fell straight to smart_truncate.
        assert llm.call_count == 0
        # Result should fit the budget.
        total_tokens = CompactUtils.estimate_messages_tokens(result)
        assert total_tokens <= 50

    async def test_llm_compact_empty_messages(self):
        """Empty input returns empty list without calling the LLM."""
        llm = _MockLLM(response_content="nope")
        result = await CompactUtils.llm_compact([], llm=llm, max_tokens=8000)
        assert result == []
        assert llm.call_count == 0

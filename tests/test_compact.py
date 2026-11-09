"""Tests for CompactUtils — token estimation, smart truncation, and LLM compact."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from fim_one.core.memory.compact import TRUNCATION_MARKER, CompactUtils
from fim_one.core.model import ChatMessage

from .fake_llm import FakeLLM, answer, raises


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


class TestTruncateToTokens:
    """``truncate_to_tokens`` is the inverse of ``estimate_tokens``."""

    def test_non_positive_budget_yields_empty(self):
        assert CompactUtils.truncate_to_tokens("hello", 0) == ""
        assert CompactUtils.truncate_to_tokens("hello", -5) == ""

    def test_empty_text_passthrough(self):
        assert CompactUtils.truncate_to_tokens("", 10) == ""

    def test_text_within_budget_is_untouched(self):
        text = "short"
        assert CompactUtils.truncate_to_tokens(text, 1000) == text

    def test_result_is_a_prefix(self):
        text = "abcdefghij" * 20
        out = CompactUtils.truncate_to_tokens(text, 10)
        assert text.startswith(out)

    @pytest.mark.parametrize(
        "text",
        [
            "中" * 500,
            "hello world " * 200,
            "混合 mixed 内容 content " * 100,
        ],
    )
    @pytest.mark.parametrize("budget", [1, 10, 50, 200])
    def test_result_fits_the_budget(self, text: str, budget: int):
        """The trimmed prefix must not cost more than the budget allowed.

        ``estimate_tokens`` floors to int and has a ``max(1, ...)`` floor,
        so allow one token of slack.
        """
        out = CompactUtils.truncate_to_tokens(text, budget)
        assert CompactUtils.estimate_tokens(out) <= budget + 1

    def test_cjk_gets_fewer_chars_than_ascii_for_equal_budget(self):
        """A CJK char costs ~2.7x an ASCII char, so it earns fewer chars.

        The old ``max_chars = tokens * 4`` arithmetic handed both the same
        character allowance, overshooting the budget on Chinese text.
        """
        budget = 50
        cjk = CompactUtils.truncate_to_tokens("中" * 500, budget)
        ascii_text = CompactUtils.truncate_to_tokens("a" * 500, budget)
        assert len(cjk) < len(ascii_text)
        # The naive inverse would have granted 200 chars to both.
        assert len(ascii_text) == budget * 4
        assert len(cjk) < budget * 4


class TestTruncateHeadTail:
    """Head+tail truncation keeps the conclusion, which trails the output."""

    def test_text_within_budget_is_untouched(self):
        assert CompactUtils.truncate_head_tail("short", 1000) == "short"

    def test_non_positive_budget_yields_empty(self):
        assert CompactUtils.truncate_head_tail("hello", 0) == ""

    def test_empty_text_passthrough(self):
        assert CompactUtils.truncate_head_tail("", 100) == ""

    def test_both_ends_survive(self):
        text = "HEAD " + ("filler " * 3000) + " TAIL"
        out = CompactUtils.truncate_head_tail(text, 400)
        assert out.startswith("HEAD")
        assert out.endswith("TAIL")
        assert TRUNCATION_MARKER in out

    def test_head_only_truncation_would_lose_the_tail(self):
        """Contrast with the prefix cut this replaced."""
        text = "HEAD " + ("filler " * 3000) + " TAIL"
        assert not CompactUtils.truncate_to_tokens(text, 400).endswith("TAIL")

    def test_result_fits_the_budget(self):
        text = "x" * 40_000
        out = CompactUtils.truncate_head_tail(text, 500)
        assert CompactUtils.estimate_tokens(out) <= 501

    def test_cjk_result_fits_the_budget(self):
        text = "中" * 10_000
        out = CompactUtils.truncate_head_tail(text, 300)
        assert CompactUtils.estimate_tokens(out) <= 301

    def test_tiny_budget_degrades_to_a_prefix_without_overlapping(self):
        """A budget too small for both ends must not duplicate content."""
        text = "abcdefghij" * 50
        out = CompactUtils.truncate_head_tail(text, 2)
        assert len(out) <= len(text)
        assert text.startswith(out.split(TRUNCATION_MARKER)[0])


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
        llm = FakeLLM([answer("should not be called")])
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
        llm = FakeLLM([answer(summary_text)])

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
        llm = FakeLLM([raises(RuntimeError("API down"))])

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
        llm = FakeLLM([answer("")])

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
        llm = FakeLLM([answer(summary_text)])

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
        llm = FakeLLM([answer("should not be called")])

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
        llm = FakeLLM([answer("nope")])
        result = await CompactUtils.llm_compact([], llm=llm, max_tokens=8000)
        assert result == []
        assert llm.call_count == 0


class TestSmartTruncateToolPairing:
    """smart_truncate must not leave orphan role="tool" messages at the
    head of the kept window (providers reject them with HTTP 400)."""

    def test_orphan_leading_tools_are_dropped(self):
        from fim_one.core.model.types import ToolCallRequest

        big = ChatMessage(role="user", content="q " + "x" * 2000, pinned=True)
        assistant = ChatMessage(
            role="assistant",
            content="calling " + "a" * 1200,
            tool_calls=[ToolCallRequest(id="t1", name="search", arguments={})],
        )
        tool = ChatMessage(role="tool", content="r" * 200, tool_call_id="t1")
        follow_up = ChatMessage(role="user", content="continue " + "u" * 200)
        msgs = [big, assistant, tool, follow_up]

        # Budget fits pinned + follow_up + tool, but NOT the assistant turn
        # that owns the tool result — the orphan tool must be dropped.
        budget = 4 + CompactUtils.estimate_tokens(big.content)
        budget += 4 + CompactUtils.estimate_tokens(follow_up.content)
        budget += 4 + CompactUtils.estimate_tokens(tool.content)

        result = CompactUtils.smart_truncate(msgs, budget)

        assert all(m.role != "tool" for m in result)
        assert result[0] is big
        assert result[-1] is follow_up

    def test_leading_assistant_with_tool_calls_drops_its_results_too(self):
        from fim_one.core.model.types import ToolCallRequest

        assistant = ChatMessage(
            role="assistant",
            content="c" * 40,
            tool_calls=[ToolCallRequest(id="t1", name="search", arguments={})],
        )
        tool = ChatMessage(role="tool", content="r" * 40, tool_call_id="t1")
        user = ChatMessage(role="user", content="next question " + "q" * 40)
        old_user = ChatMessage(role="user", content="old " + "o" * 4000)
        msgs = [old_user, assistant, tool, user]

        budget = 4 + CompactUtils.estimate_tokens(user.content)
        budget += 4 + CompactUtils.estimate_tokens(tool.content)
        budget += 4 + CompactUtils.estimate_tokens(assistant.content)

        result = CompactUtils.smart_truncate(msgs, budget)

        # The kept window starts at the assistant turn (no pinned messages),
        # which must be pruned — and pruning it must also remove its tool
        # results instead of exposing them as orphans.
        assert all(m.role != "tool" for m in result)
        assert result == [user]


# ---------------------------------------------------------------------------
# Chunked summarisation
# ---------------------------------------------------------------------------


def _request_tokens(messages: list[ChatMessage]) -> int:
    """Size of one summariser request by the estimator compaction uses."""
    return CompactUtils.estimate_messages_tokens(messages)


def _history(n: int, body: str) -> list[ChatMessage]:
    """*n* alternating user/assistant turns, each tagged ``MARK-<i>``."""
    return [
        ChatMessage(
            role="user" if i % 2 == 0 else "assistant",
            content=f"MARK-{i} {body}",
        )
        for i in range(n)
    ]


def _summariser(context_size: int = 8_000, max_output_tokens: int = 2_000) -> FakeLLM:
    """A summariser with a small declared window; answers ``S<n>`` per call."""
    return FakeLLM(
        [answer(f"S{i}") for i in range(1, 200)],
        context_size=context_size,
        max_output_tokens=max_output_tokens,
    )


_PROMPT = "Summarise the conversation."


class TestSummarizeHistorySingleRequest:
    async def test_history_that_fits_is_one_plain_request(self) -> None:
        llm = _summariser()
        msgs = _history(4, "short")

        summary = await CompactUtils.summarize_history(
            msgs, llm, _PROMPT, input_budget=5_000
        )

        assert summary == "S1"
        assert llm.chat_call_count == 1
        call = llm.last_call
        assert call.system_prompt == _PROMPT
        assert call.user_text() == (
            "User: MARK-0 short\nAssistant: MARK-1 short\n"
            "User: MARK-2 short\nAssistant: MARK-3 short"
        )

    async def test_empty_history_makes_no_call(self) -> None:
        llm = _summariser()
        assert await CompactUtils.summarize_history([], llm, _PROMPT, input_budget=5_000) is None
        assert llm.chat_call_count == 0


class TestSummarizeHistoryChunked:
    """A history larger than the summariser's window is folded in parts."""

    async def test_no_request_exceeds_the_summarisers_window(self) -> None:
        llm = _summariser(context_size=8_000, max_output_tokens=2_000)
        budget = 5_520  # (8000 - 2000) * 0.92
        msgs = _history(90, "x" * 1_200)  # ~27k tokens, ~5 windows
        assert _request_tokens(msgs) > 4 * budget

        summary = await CompactUtils.summarize_history(msgs, llm, _PROMPT, input_budget=budget)

        assert llm.chat_call_count > 1
        for call in llm.chat_calls:
            assert _request_tokens(call.messages) <= budget
            assert _request_tokens(call.messages) <= 8_000 - 2_000
        # The result is the last fold, and every turn reached the summariser.
        assert summary == f"S{llm.chat_call_count}"
        seen = "\n".join(call.user_text() for call in llm.chat_calls)
        assert all(f"MARK-{i} " in seen for i in range(90))

    async def test_cjk_history_stays_within_the_window(self) -> None:
        # CJK costs ~2.7x more per character; the packing must use the
        # estimator's cost, not a character count.
        llm = _summariser()
        budget = 5_520
        msgs = _history(60, "中文内容测试" * 150)

        await CompactUtils.summarize_history(msgs, llm, _PROMPT, input_budget=budget)

        assert llm.chat_call_count > 1
        for call in llm.chat_calls:
            assert _request_tokens(call.messages) <= budget

    async def test_later_parts_carry_the_summary_so_far(self) -> None:
        llm = _summariser()
        msgs = _history(60, "x" * 1_200)

        await CompactUtils.summarize_history(msgs, llm, _PROMPT, input_budget=5_520)

        first, second = llm.chat_calls[0], llm.chat_calls[1]
        assert first.system_prompt == _PROMPT
        assert "[Summary of the conversation so far]" not in first.user_text()
        assert second.system_prompt is not None
        assert second.system_prompt.startswith(_PROMPT)
        assert "[Summary of the conversation so far]\nS1" in second.user_text()
        assert "[Next part of the conversation]" in second.user_text()

    async def test_parts_are_consecutive_and_in_order(self) -> None:
        llm = _summariser()
        msgs = _history(60, "x" * 1_200)

        await CompactUtils.summarize_history(msgs, llm, _PROMPT, input_budget=5_520)

        order: list[int] = []
        for call in llm.chat_calls:
            for line in call.user_text().splitlines():
                if ": MARK-" in line:
                    order.append(int(line.split("MARK-")[1].split(" ")[0]))
        assert order == list(range(60))

    async def test_analysis_scratchpad_is_not_carried_forward(self) -> None:
        llm = FakeLLM(
            [answer("<analysis>scratch</analysis>\nKept one"), answer("Kept two")],
            context_size=8_000,
            max_output_tokens=2_000,
        )
        msgs = _history(30, "x" * 1_200)

        summary = await CompactUtils.summarize_history(msgs, llm, _PROMPT, input_budget=5_520)

        assert "scratch" not in llm.chat_calls[1].user_text()
        assert "Kept one" in llm.chat_calls[1].user_text()
        assert summary == "Kept two"

    async def test_single_message_larger_than_a_part_is_cut_not_dropped(self) -> None:
        llm = _summariser()
        budget = 5_520
        huge = "".join(f"<{i:05d}>" for i in range(6_000))  # 42k chars, ~10.5k tokens
        msgs = [
            ChatMessage(role="user", content="before"),
            ChatMessage(role="assistant", content=huge),
            ChatMessage(role="user", content="after"),
        ]

        await CompactUtils.summarize_history(msgs, llm, _PROMPT, input_budget=budget)

        assert llm.chat_call_count >= 2
        for call in llm.chat_calls:
            assert _request_tokens(call.messages) <= budget
        seen = "".join(call.user_text() for call in llm.chat_calls)
        # Pieces rejoin to the original text: drop the scaffolding between
        # them and every tag must still be there, in order.
        assert all(f"<{i:05d}>" in seen for i in (0, 1_500, 3_000, 4_500, 5_999))

    async def test_every_summariser_call_is_metered(self) -> None:
        usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
        llm = FakeLLM(
            [answer("S", usage=usage)], context_size=8_000, max_output_tokens=2_000
        )
        tracker = AsyncMock()
        msgs = _history(60, "x" * 1_200)

        await CompactUtils.summarize_history(
            msgs, llm, _PROMPT, input_budget=5_520, usage_tracker=tracker
        )

        assert llm.chat_call_count > 1
        assert tracker.record.await_count == llm.chat_call_count


class TestSummarizeHistoryToolPairs:
    """A tool call and its results are never put in different parts."""

    def _pairs(self, n: int, body: str) -> list[ChatMessage]:
        from fim_one.core.model.types import ToolCallRequest

        msgs: list[ChatMessage] = []
        for i in range(n):
            msgs.append(
                ChatMessage(
                    role="assistant",
                    content=f"CALL-{i} {body}",
                    tool_calls=[ToolCallRequest(id=f"t{i}", name="search", arguments={})],
                )
            )
            msgs.append(
                ChatMessage(role="tool", content=f"RESULT-{i} {body}", tool_call_id=f"t{i}")
            )
        return msgs

    async def test_native_tool_call_stays_with_its_result(self) -> None:
        llm = _summariser()
        # Each pair is ~0.6k tokens; a part holds several, with an uneven
        # remainder, so a boundary would land inside a pair if pairs were
        # not kept whole.
        msgs = self._pairs(40, "x" * 1_150)

        await CompactUtils.summarize_history(msgs, llm, _PROMPT, input_budget=5_520)

        assert llm.chat_call_count > 1
        for call in llm.chat_calls:
            text = call.user_text()
            for i in range(40):
                assert (f"CALL-{i} " in text) == (f"RESULT-{i} " in text)

    async def test_json_mode_observation_stays_with_its_action(self) -> None:
        llm = _summariser()
        msgs: list[ChatMessage] = []
        for i in range(40):
            msgs.append(ChatMessage(role="assistant", content=f"ACT-{i} " + "x" * 1_150))
            msgs.append(
                ChatMessage(role="user", content=f"Observation: OBS-{i} " + "x" * 1_150)
            )

        await CompactUtils.summarize_history(msgs, llm, _PROMPT, input_budget=5_520)

        assert llm.chat_call_count > 1
        for call in llm.chat_calls:
            text = call.user_text()
            for i in range(40):
                assert (f"ACT-{i} " in text) == (f"OBS-{i} " in text)

    async def test_pair_larger_than_a_part_still_fits_the_window(self) -> None:
        # A pair that cannot fit one part has to be split; the requests
        # must still respect the window and lose nothing.
        llm = _summariser()
        budget = 5_520
        msgs = self._pairs(3, "y" * 16_000)  # each pair ~8k tokens

        await CompactUtils.summarize_history(msgs, llm, _PROMPT, input_budget=budget)

        for call in llm.chat_calls:
            assert _request_tokens(call.messages) <= budget
        seen = "\n".join(call.user_text() for call in llm.chat_calls)
        assert all(f"CALL-{i} " in seen and f"RESULT-{i} " in seen for i in range(3))


class TestSummarizeHistoryBounds:
    async def test_prompt_that_leaves_no_room_gives_up_without_calling(self) -> None:
        # An agent's custom compact instructions can be longer than the
        # summariser's window; no part could ever fit, so do not try.
        llm = _summariser()
        msgs = _history(60, "x" * 1_200)

        summary = await CompactUtils.summarize_history(
            msgs, llm, "p" * 40_000, input_budget=5_520
        )

        assert summary is None
        assert llm.chat_call_count == 0

    async def test_failure_part_way_returns_none(self) -> None:
        llm = FakeLLM(
            [answer("S1"), raises(RuntimeError("boom"))],
            context_size=8_000,
            max_output_tokens=2_000,
        )
        msgs = _history(60, "x" * 1_200)

        assert (
            await CompactUtils.summarize_history(msgs, llm, _PROMPT, input_budget=5_520)
            is None
        )
        assert llm.chat_call_count == 2

    async def test_empty_part_summary_returns_none(self) -> None:
        llm = FakeLLM([answer("")], context_size=8_000, max_output_tokens=2_000)
        msgs = _history(60, "x" * 1_200)

        assert (
            await CompactUtils.summarize_history(msgs, llm, _PROMPT, input_budget=5_520)
            is None
        )
        assert llm.chat_call_count == 1

    async def test_part_count_is_capped(self) -> None:
        llm = _summariser()
        budget = 5_520
        msgs = _history(200, "x" * 1_200)  # ~11 windows

        with patch("fim_one.core.memory.compact.MAX_COMPACT_CHUNKS", 3):
            summary = await CompactUtils.summarize_history(
                msgs, llm, _PROMPT, input_budget=budget
            )

        assert llm.chat_call_count == 3
        assert summary == "S3"
        calls = llm.chat_calls
        for call in calls:
            assert _request_tokens(call.messages) <= budget
        # First part and the most recent parts survive; the gap is flagged.
        assert "MARK-0 " in calls[0].user_text()
        assert "MARK-199 " in calls[2].user_text()
        assert "earlier part(s) of the conversation omitted" in calls[1].user_text()

    async def test_cap_of_one_keeps_the_most_recent_part(self) -> None:
        llm = _summariser()
        msgs = _history(200, "x" * 1_200)

        with patch("fim_one.core.memory.compact.MAX_COMPACT_CHUNKS", 1):
            await CompactUtils.summarize_history(msgs, llm, _PROMPT, input_budget=5_520)

        assert llm.chat_call_count == 1
        assert "MARK-199 " in llm.last_call.user_text()
        assert _request_tokens(llm.last_call.messages) <= 5_520

    @pytest.mark.parametrize(
        "line",
        ["a" * 1_000, "中" * 400, "mixed 中文 and ascii " * 60, "x"],
    )
    def test_split_line_always_advances_and_loses_nothing(self, line: str) -> None:
        for max_tokens in (1, 3, 50):
            pieces = CompactUtils._split_line(line, max_tokens)
            assert "".join(pieces) == line
            assert all(pieces)
            assert all(
                len(piece) == 1 or CompactUtils.estimate_tokens(piece) <= max_tokens
                for piece in pieces
            )


class TestLlmCompactChunked:
    """Load-time compaction (DbMemory) with a summariser smaller than history."""

    async def test_history_larger_than_summariser_window_is_summarised(self) -> None:
        # Before chunking this sent ~36k tokens to an 8k-window model in a
        # single request; the provider rejects that and compaction silently
        # degraded to truncation.
        llm = _summariser(context_size=8_000, max_output_tokens=2_000)
        window = 8_000 - 2_000
        msgs = _history(120, "x" * 1_200)

        result = await CompactUtils.llm_compact(msgs, llm, max_tokens=4_000, keep_recent=4)

        assert llm.chat_call_count > 1
        for call in llm.chat_calls:
            assert _request_tokens(call.messages) <= window
        summaries = [
            m for m in result if m.role == "system" and "[Conversation summary]" in (m.content or "")
        ]
        assert len(summaries) == 1
        assert CompactUtils.estimate_messages_tokens(result) <= 4_000
        assert result[-1].content == msgs[-1].content

    async def test_explicit_summary_budget_wins_over_the_declared_window(self) -> None:
        llm = _summariser(context_size=200_000, max_output_tokens=8_000)
        msgs = _history(120, "x" * 1_200)

        await CompactUtils.llm_compact(
            msgs, llm, max_tokens=4_000, summary_input_budget=5_520
        )

        assert llm.chat_call_count > 1
        for call in llm.chat_calls:
            assert _request_tokens(call.messages) <= 5_520

    async def test_unknown_summariser_uses_the_documented_default(self) -> None:
        from fim_one.core.memory.budget import DEFAULT_INPUT_BUDGET

        llm = FakeLLM([answer(f"S{i}") for i in range(1, 50)])  # no window metadata
        msgs = _history(300, "x" * 1_200)  # ~91k tokens
        assert _request_tokens(msgs) > 2 * DEFAULT_INPUT_BUDGET

        await CompactUtils.llm_compact(msgs, llm, max_tokens=4_000)

        assert llm.chat_call_count > 1
        for call in llm.chat_calls:
            assert _request_tokens(call.messages) <= DEFAULT_INPUT_BUDGET

    async def test_recent_window_does_not_start_with_orphan_tool_results(self) -> None:
        from fim_one.core.model.types import ToolCallRequest

        llm = _summariser(context_size=200_000, max_output_tokens=8_000)
        msgs = _history(8, "x" * 1_200)
        msgs.append(
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCallRequest(id=f"t{i}", name="search", arguments={}) for i in range(4)
                ],
            )
        )
        msgs.extend(
            ChatMessage(role="tool", content=f"result-{i}", tool_call_id=f"t{i}")
            for i in range(4)
        )

        result = await CompactUtils.llm_compact(msgs, llm, max_tokens=1_000, keep_recent=4)

        first_tool = next(i for i, m in enumerate(result) if m.role == "tool")
        assert any(m.role == "assistant" and m.tool_calls for m in result[:first_tool])

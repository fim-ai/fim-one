"""Cache-prefix stability guards for the ReAct system prompt.

Prompt caching is prefix matching: the provider reuses a cached prefix only
while the bytes ahead of the breakpoint are identical to the previous call.
The loop sets one breakpoint, on the static half of the system prompt, and
keeps wall-clock-sensitive text in a dynamic suffix after it.  Anything that
leaks per-call content into the static half silently drops the hit rate to
zero — nothing fails, the bill just goes up.

These lock the static half.  Note the invariant does NOT currently extend
to the message history: ``micro_compact`` and ``ContextGuard`` rewrite
older turns in place, so a breakpoint further down would miss every turn.
``TestHistoryIsNotYetPrefixStable`` pins that as a known limitation, so
whoever makes history append-only learns the breakpoint can move.
"""

from __future__ import annotations

from typing import Any

import pytest

from fim_one.core.agent import ReActAgent
from fim_one.core.memory.microcompact import micro_compact
from fim_one.core.model import ChatMessage
from fim_one.core.tool import ToolRegistry

from .conftest import EchoTool, FakeLLM


class CachingFakeLLM(FakeLLM):
    """A FakeLLM whose id makes ``is_cache_capable`` true.

    Without one the loop emits the single concatenated system message and
    sets no breakpoint, so the placement tests would pass vacuously.
    """

    @property
    def model_id(self) -> str:
        return "claude-sonnet-5"


def _agent(*, caching: bool = False, **kwargs: Any) -> ReActAgent:
    registry = ToolRegistry()
    registry.register(EchoTool())
    llm = CachingFakeLLM(responses=[]) if caching else FakeLLM(responses=[])
    return ReActAgent(
        llm=llm,
        tools=registry,
        enable_plan_tool=False,
        **kwargs,
    )


class TestStaticPrefixIsStable:
    """The cached half must be byte-identical from one call to the next."""

    def test_repeated_builds_match(self) -> None:
        agent = _agent()
        first, _ = agent._build_system_prompt_split()
        second, _ = agent._build_system_prompt_split()
        assert first == second

    def test_repeated_builds_match_in_native_mode(self) -> None:
        agent = _agent()
        first, _ = agent._build_system_prompt_split_native()
        second, _ = agent._build_system_prompt_split_native()
        assert first == second

    def test_two_agents_with_the_same_config_agree(self) -> None:
        """Otherwise the cache is per-process, not per-configuration."""
        a, _ = _agent()._build_system_prompt_split()
        b, _ = _agent()._build_system_prompt_split()
        assert a == b

    def test_tool_order_does_not_drift(self) -> None:
        """Tool descriptions are serialized into the prefix."""
        agent = _agent()
        renders = {agent._format_tool_descriptions() for _ in range(5)}
        assert len(renders) == 1


class TestClockStaysOutOfTheCachedHalf:
    """The classic prefix-buster: a timestamp inside the cached region.

    Every turn would then rewrite the prefix and force a full prefill.
    """

    def test_prefix_carries_no_datetime(self) -> None:
        agent = _agent(user_timezone="Asia/Shanghai")
        prefix, _ = agent._build_system_prompt_split()
        assert "Current date and time" not in prefix

    def test_suffix_is_where_the_clock_lives(self) -> None:
        agent = _agent(user_timezone="Asia/Shanghai")
        _, suffix = agent._build_system_prompt_split()
        assert "Current date and time" in suffix

    def test_timezone_changes_do_not_touch_the_prefix(self) -> None:
        shanghai, _ = _agent(user_timezone="Asia/Shanghai")._build_system_prompt_split()
        utc, _ = _agent(user_timezone="UTC")._build_system_prompt_split()
        assert shanghai == utc

    def test_attachments_do_not_touch_the_prefix(self) -> None:
        """The vision hint rides the dynamic side for the same reason."""
        agent = _agent(caching=True)
        prefix, suffix = agent._build_system_prompt_split()

        without = agent._emit_system_messages(prefix, suffix, vision_hint=False)
        with_images = agent._emit_system_messages(prefix, suffix, vision_hint=True)

        assert without[0].content == with_images[0].content


class TestBreakpointPlacement:
    """The breakpoint marks the boundary the provider caches up to."""

    def test_cacheable_models_get_the_split_form(self) -> None:
        messages = _agent(caching=True)._emit_system_messages("static", "dynamic")

        assert len(messages) == 2
        assert messages[0].content == "static"
        assert messages[0].cache_control == {"type": "ephemeral"}
        assert messages[1].cache_control is None

    def test_other_providers_get_no_breakpoint_but_keep_both_halves(self) -> None:
        """``cache_control`` would be dropped or rejected elsewhere."""
        messages = _agent()._emit_system_messages("static", "dynamic")

        assert len(messages) == 1
        assert messages[0].cache_control is None
        assert "static" in str(messages[0].content)
        assert "dynamic" in str(messages[0].content)

    def test_an_empty_suffix_never_yields_a_dangling_breakpoint(self) -> None:
        messages = _agent(caching=True)._emit_system_messages("static", "")
        assert len(messages) == 1
        assert messages[0].content == "static"


class TestHistoryIsNotYetPrefixStable:
    """Why the loop stops at one breakpoint.

    Grok asserts ``serialize(turn N)`` is a byte prefix of
    ``serialize(turn N+1)`` and places breakpoints down the transcript.
    That requires history to be append-only.  Ours is not: the
    micro-compaction pass rewrites older tool results in place, so a
    breakpoint below the system prompt would be invalidated every turn.
    """

    def test_micro_compact_rewrites_earlier_messages(self) -> None:
        history = [ChatMessage(role="user", content="q")]
        for i in range(10):
            history.append(
                ChatMessage(role="tool", content=f"result {i}", tool_call_id=f"c{i}"),
            )

        compacted = micro_compact(history, keep_recent=2)

        rewritten = [
            i
            for i, (before, after) in enumerate(zip(history, compacted, strict=True))
            if before.content != after.content
        ]
        assert rewritten, (
            "micro_compact no longer rewrites history in place — history may "
            "now be prefix-stable, so an additional cache breakpoint below "
            "the system prompt is worth revisiting."
        )
        # Specifically, it edits the early part of the transcript, which is
        # exactly the region a downstream breakpoint would try to cache.
        assert min(rewritten) < len(history) - 2

    @pytest.mark.parametrize("keep_recent", [0, 1, 4])
    def test_recent_tail_is_preserved(self, keep_recent: int) -> None:
        """What it does keep verbatim, so the rewrite stays bounded."""
        history = [
            ChatMessage(role="tool", content=f"r{i}", tool_call_id=f"c{i}")
            for i in range(8)
        ]

        compacted = micro_compact(history, keep_recent=keep_recent)

        if keep_recent:
            assert [m.content for m in compacted[-keep_recent:]] == [
                m.content for m in history[-keep_recent:]
            ]

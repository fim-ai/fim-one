"""Tests for the system prompt section registry and cache breakpoints."""

from __future__ import annotations

import json
from typing import Any

import pytest

from fim_one.core.agent import ReActAgent
from fim_one.core.model import ChatMessage, LLMResult
from fim_one.core.prompt import (
    DYNAMIC_BOUNDARY,
    PromptRegistry,
    PromptSection,
    is_cache_capable,
)
from fim_one.core.tool import ToolRegistry

from .conftest import EchoTool, FakeLLM

# ======================================================================
# PromptRegistry — unit tests
# ======================================================================


class TestPromptRegistry:
    """Cover register, render, memoization, and dynamic re-rendering."""

    def test_render_static_only_no_boundary(self) -> None:
        reg = PromptRegistry()
        reg.register(PromptSection(name="a", content="alpha"))
        reg.register(PromptSection(name="b", content="beta"))

        full, idx = reg.render()
        assert full == "alpha\n\nbeta"
        # With no dynamic section, the boundary index is end-of-string.
        assert idx == len(full)
        assert DYNAMIC_BOUNDARY not in full

    def test_render_with_dynamic_inserts_boundary(self) -> None:
        reg = PromptRegistry()
        reg.register(PromptSection(name="identity", content="I am FIM One."))
        reg.register(
            PromptSection(
                name="now",
                content=lambda **kw: f"Now: {kw['when']}",
                is_dynamic=True,
            ),
        )

        full, idx = reg.render(dynamic_kwargs={"when": "2026-04-17"})
        assert "I am FIM One." in full
        assert "Now: 2026-04-17" in full
        assert DYNAMIC_BOUNDARY in full
        # The boundary marker starts exactly at idx.
        assert full[idx : idx + len(DYNAMIC_BOUNDARY)] == DYNAMIC_BOUNDARY

    def test_static_section_memoization(self) -> None:
        """Static callables must run exactly once across renders."""
        counter = {"n": 0}

        def _make_content() -> str:
            counter["n"] += 1
            return f"rendered-{counter['n']}"

        reg = PromptRegistry()
        reg.register(PromptSection(name="once", content=_make_content))

        first, _ = reg.render()
        second, _ = reg.render()
        third, _ = reg.render()

        assert first == "rendered-1"
        assert second == "rendered-1"
        assert third == "rendered-1"
        assert counter["n"] == 1

    def test_dynamic_section_reruns_every_render(self) -> None:
        counter = {"n": 0}

        def _make_dynamic(**_: Any) -> str:
            counter["n"] += 1
            return f"call-{counter['n']}"

        reg = PromptRegistry()
        reg.register(PromptSection(name="static", content="HELLO"))
        reg.register(
            PromptSection(name="dynamic", content=_make_dynamic, is_dynamic=True),
        )

        first, _ = reg.render()
        second, _ = reg.render()
        third, _ = reg.render()

        assert counter["n"] == 3
        assert "call-1" in first
        assert "call-2" in second
        assert "call-3" in third

    def test_re_register_replaces_in_place_and_clears_cache(self) -> None:
        reg = PromptRegistry()
        reg.register(PromptSection(name="a", content="first"))
        reg.render()  # prime the cache
        reg.register(PromptSection(name="a", content="second"))

        full, _ = reg.render()
        assert full == "second"

    def test_clear_removes_all_sections(self) -> None:
        reg = PromptRegistry()
        reg.register(PromptSection(name="a", content="x"))
        reg.clear()
        full, idx = reg.render()
        assert full == ""
        assert idx == 0

    def test_render_split_strips_boundary(self) -> None:
        reg = PromptRegistry()
        reg.register(PromptSection(name="core", content="CORE"))
        reg.register(
            PromptSection(
                name="date",
                content=lambda **_: "NOW",
                is_dynamic=True,
            ),
        )

        prefix, suffix = reg.render_split()
        assert prefix == "CORE"
        assert suffix == "NOW"
        assert DYNAMIC_BOUNDARY not in prefix
        assert DYNAMIC_BOUNDARY not in suffix

    def test_render_split_only_prefix(self) -> None:
        reg = PromptRegistry()
        reg.register(PromptSection(name="only", content="STATIC"))
        prefix, suffix = reg.render_split()
        assert prefix == "STATIC"
        assert suffix == ""


# ======================================================================
# is_cache_capable
# ======================================================================


class TestIsCacheCapable:
    """Model-id substring detection for Anthropic-style prompt caching."""

    @pytest.mark.parametrize(
        "model_id",
        [
            "claude-3-5-sonnet-20240620",
            "claude-opus-4-20250514",
            "anthropic/claude-3-haiku",
            "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
            "vertex_ai/claude-3-5-sonnet@20240620",
            "Claude-Opus-4",  # case-insensitive
        ],
    )
    def test_true_for_anthropic_family(self, model_id: str) -> None:
        assert is_cache_capable(model_id) is True

    @pytest.mark.parametrize(
        "model_id",
        [
            "gpt-4o",
            "gpt-4-turbo-2024-04-09",
            "gemini-1.5-pro",
            "deepseek-chat",
            "qwen-max",
            "llama-3.1-70b",
        ],
    )
    def test_false_for_non_anthropic(self, model_id: str) -> None:
        assert is_cache_capable(model_id) is False

    def test_false_for_none_and_empty(self) -> None:
        assert is_cache_capable(None) is False
        assert is_cache_capable("") is False


# ======================================================================
# ChatMessage.cache_control serialization
# ======================================================================


class TestChatMessageCacheControl:
    """Ensure cache_control flows through to the OpenAI-format dict."""

    def test_cache_control_included_when_set(self) -> None:
        msg = ChatMessage(
            role="system",
            content="hello",
            cache_control={"type": "ephemeral"},
        )
        d = msg.to_openai_dict()
        assert d["cache_control"] == {"type": "ephemeral"}

    def test_cache_control_omitted_when_none(self) -> None:
        msg = ChatMessage(role="system", content="hello")
        d = msg.to_openai_dict()
        assert "cache_control" not in d

    def test_cache_control_coexists_with_other_fields(self) -> None:
        msg = ChatMessage(
            role="system",
            content="hi",
            reasoning_content="thinking...",
            signature="sig123",
            cache_control={"type": "ephemeral"},
        )
        d = msg.to_openai_dict()
        assert d["cache_control"] == {"type": "ephemeral"}
        assert d["reasoning_content"] == "thinking..."
        assert d["signature"] == "sig123"


# ======================================================================
# ReAct integration — split system messages for cache-capable models
# ======================================================================


def _final_answer_response(answer: str) -> LLMResult:
    return LLMResult(
        message=ChatMessage(
            role="assistant",
            content=json.dumps({"type": "final_answer", "reasoning": "done", "answer": answer}),
        ),
    )


class _ClaudeLikeLLM(FakeLLM):
    """FakeLLM that advertises a Claude model id so caching kicks in."""

    @property
    def model_id(self) -> str:
        return "claude-3-5-sonnet-20240620"


class _GPTLikeLLM(FakeLLM):
    """FakeLLM that advertises a GPT model id (no caching)."""

    @property
    def model_id(self) -> str:
        return "gpt-4o-2024-08-06"


class TestReActCacheBreakpoints:
    """Verify the 3 refactored call sites emit correct system messages."""

    @pytest.fixture
    def tools(self) -> ToolRegistry:
        reg = ToolRegistry()
        reg.register(EchoTool())
        return reg

    async def test_json_mode_claude_emits_two_system_messages(self, tools: ToolRegistry) -> None:
        llm = _ClaudeLikeLLM(
            responses=[_final_answer_response("42")],
            abilities={
                "tool_call": False,
                "json_mode": True,
                "vision": False,
                "streaming": True,
                "thinking": False,
            },
        )
        # Disable native mode so JSON-mode path is exercised.
        agent = ReActAgent(
            llm=llm,
            tools=tools,
            max_iterations=2,
            use_native_tools=False,
        )
        result = await agent.run("hi")

        system_msgs = [m for m in result.messages if m.role == "system"]
        assert len(system_msgs) == 2, "cache-capable models should get 2 system messages"
        assert system_msgs[0].cache_control == {"type": "ephemeral"}
        assert system_msgs[1].cache_control is None
        # Dynamic suffix carries the datetime line.
        assert "Current date and time" in (system_msgs[1].content or "")
        # Static prefix does NOT carry datetime.
        assert "Current date and time" not in (system_msgs[0].content or "")
        # Identity still in prefix.
        assert "FIM One" in (system_msgs[0].content or "")

    async def test_json_mode_gpt_emits_single_system_message(self, tools: ToolRegistry) -> None:
        llm = _GPTLikeLLM(
            responses=[_final_answer_response("42")],
            abilities={
                "tool_call": False,
                "json_mode": True,
                "vision": False,
                "streaming": True,
                "thinking": False,
            },
        )
        agent = ReActAgent(
            llm=llm,
            tools=tools,
            max_iterations=2,
            use_native_tools=False,
        )
        result = await agent.run("hi")

        system_msgs = [m for m in result.messages if m.role == "system"]
        assert len(system_msgs) == 1, "non-cache-capable models should get 1 system message"
        assert system_msgs[0].cache_control is None
        # Same text content is preserved in the combined message.
        combined = system_msgs[0].content or ""
        assert "FIM One" in combined
        assert "Current date and time" in combined

    async def test_native_mode_claude_emits_two_system_messages(self, tools: ToolRegistry) -> None:
        llm = _ClaudeLikeLLM(
            responses=[_final_answer_response("ok")],
            abilities={
                "tool_call": True,
                "json_mode": True,
                "vision": False,
                "streaming": True,
                "thinking": False,
            },
        )
        agent = ReActAgent(llm=llm, tools=tools, max_iterations=2)
        # When use_native_tools=True AND tool_call ability is advertised,
        # native path is active.
        assert agent._native_mode_active is True
        result = await agent.run("hi")

        system_msgs = [m for m in result.messages if m.role == "system"]
        assert len(system_msgs) == 2
        assert system_msgs[0].cache_control == {"type": "ephemeral"}
        assert system_msgs[1].cache_control is None

    async def test_native_mode_gpt_emits_single_system_message(self, tools: ToolRegistry) -> None:
        llm = _GPTLikeLLM(
            responses=[_final_answer_response("ok")],
            abilities={
                "tool_call": True,
                "json_mode": True,
                "vision": False,
                "streaming": True,
                "thinking": False,
            },
        )
        agent = ReActAgent(llm=llm, tools=tools, max_iterations=2)
        assert agent._native_mode_active is True
        result = await agent.run("hi")

        system_msgs = [m for m in result.messages if m.role == "system"]
        assert len(system_msgs) == 1
        assert system_msgs[0].cache_control is None

    async def test_system_prompt_override_disables_split(self, tools: ToolRegistry) -> None:
        """Custom system_prompt override collapses to a single message."""
        llm = _ClaudeLikeLLM(
            responses=[_final_answer_response("ok")],
            abilities={
                "tool_call": False,
                "json_mode": True,
                "vision": False,
                "streaming": True,
                "thinking": False,
            },
        )
        agent = ReActAgent(
            llm=llm,
            tools=tools,
            system_prompt="You are a test agent. Respond in JSON.",
            max_iterations=2,
            use_native_tools=False,
        )
        result = await agent.run("hi")

        system_msgs = [m for m in result.messages if m.role == "system"]
        # With an override there is no dynamic suffix, so a single
        # system message is emitted regardless of model.
        assert len(system_msgs) == 1
        assert system_msgs[0].content == "You are a test agent. Respond in JSON."

    async def test_same_prompt_text_across_cache_and_non_cache(self, tools: ToolRegistry) -> None:
        """Cache-capable and non-cache-capable models receive the SAME text."""
        claude = _ClaudeLikeLLM(
            responses=[_final_answer_response("ok")],
            abilities={
                "tool_call": False,
                "json_mode": True,
                "vision": False,
                "streaming": True,
                "thinking": False,
            },
        )
        gpt = _GPTLikeLLM(
            responses=[_final_answer_response("ok")],
            abilities={
                "tool_call": False,
                "json_mode": True,
                "vision": False,
                "streaming": True,
                "thinking": False,
            },
        )

        claude_agent = ReActAgent(llm=claude, tools=tools, max_iterations=2, use_native_tools=False)
        gpt_agent = ReActAgent(llm=gpt, tools=tools, max_iterations=2, use_native_tools=False)

        claude_result = await claude_agent.run("hi")
        gpt_result = await gpt_agent.run("hi")

        claude_system = [m for m in claude_result.messages if m.role == "system"]
        gpt_system = [m for m in gpt_result.messages if m.role == "system"]

        # Concatenate Claude's two messages into the same shape GPT sees.
        claude_combined = "\n\n".join((m.content or "") for m in claude_system)
        gpt_combined = gpt_system[0].content or ""

        # Identity, guidelines, datetime all present on both sides.
        assert "FIM One" in claude_combined
        assert "FIM One" in gpt_combined
        assert "Current date and time" in claude_combined
        assert "Current date and time" in gpt_combined

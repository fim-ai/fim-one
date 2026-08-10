"""Tests for the provider-aware reasoning-replay policy.

Covers the policy resolver (:func:`reasoning_replay_policy`) and its
enforcement at the single centralised site
(:meth:`ChatMessage.to_openai_dict` + the wiring in
:meth:`OpenAICompatibleLLM._build_request_kwargs`).

Policy truth table (copied from the task spec):

| Model ID                                   | Policy                |
| ------------------------------------------ | --------------------- |
| ``claude-sonnet-4-6``                      | ``anthropic_thinking`` |
| ``anthropic/claude-opus-4-7``              | ``anthropic_thinking`` |
| ``bedrock/anthropic.claude-3-5-sonnet``    | ``anthropic_thinking`` |
| ``vertex_ai/claude-sonnet-4@20241022``     | ``anthropic_thinking`` |
| ``deepseek-reasoner``                      | ``informational_only`` |
| ``qwen-qwq-32b-preview``                   | ``informational_only`` |
| ``gemini-2.0-flash-thinking-exp``          | ``informational_only`` |
| ``o1-preview`` / ``o3-mini`` / ``o4-mini`` | ``informational_only`` |
| ``gpt-4o`` / ``gpt-4-turbo``               | ``unsupported``        |
| ``gemini-1.5-pro``                         | ``unsupported``        |
| ``None``                                   | ``unsupported``        |
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from fim_one.core.model import ChatMessage
from fim_one.core.model.types import StreamChunk
from fim_one.core.prompt import reasoning_replay_policy

# ======================================================================
# reasoning_replay_policy — pure function
# ======================================================================


class TestReasoningReplayPolicy:
    """Model-id fragment detection for reasoning replay."""

    @pytest.mark.parametrize(
        "model_id",
        [
            "claude-sonnet-4-6",
            "claude-opus-4-7",
            "claude-haiku-4-5",
            "anthropic/claude-opus-4-7",
            "anthropic/claude-3-5-sonnet-20240620",
            "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
            "vertex_ai/claude-sonnet-4@20241022",
            "Claude-Sonnet-4",  # case-insensitive
        ],
    )
    def test_anthropic_thinking(self, model_id: str) -> None:
        assert reasoning_replay_policy(model_id) == "anthropic_thinking"

    @pytest.mark.parametrize(
        "model_id",
        [
            "deepseek-reasoner",
            "deepseek-r1-distill-qwen-32b",
            "qwen-qwq-32b-preview",
            "qwq-32b",
            "gemini-2.0-flash-thinking-exp",
            "gemini-2.5-flash-thinking-exp",
            "o1-preview",
            "o1-mini",
            "o3-mini",
            "o4-mini",
        ],
    )
    def test_informational_only(self, model_id: str) -> None:
        assert reasoning_replay_policy(model_id) == "informational_only"

    @pytest.mark.parametrize(
        "model_id",
        [
            "gpt-5",
            "gpt-5.6-luna",
            "gpt-5-mini",
            "openai/gpt-5.6-luna",
        ],
    )
    def test_openai_responses(self, model_id: str) -> None:
        """GPT-5.x carries reasoning as opaque Responses items."""
        assert reasoning_replay_policy(model_id) == "openai_responses"

    def test_openai_responses_still_drops_readable_reasoning(self) -> None:
        """The chat shape has no slot for items, so nothing is replayed there."""
        msg = ChatMessage(
            role="assistant",
            content="a",
            reasoning_content="thought",
            reasoning_items=[{"type": "reasoning", "encrypted_content": "g"}],
        )
        d = msg.to_openai_dict(replay_policy="openai_responses")
        assert "reasoning_content" not in d
        assert "reasoning_items" not in d

    @pytest.mark.parametrize(
        "model_id",
        [
            "gpt-4o",
            "gpt-4o-2024-08-06",
            "gpt-4-turbo",
            "gpt-4-turbo-2024-04-09",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
            "deepseek-chat",
            "qwen-max",
            "llama-3.1-70b",
            "mistral-large-latest",
        ],
    )
    def test_unsupported(self, model_id: str) -> None:
        assert reasoning_replay_policy(model_id) == "unsupported"

    def test_none_and_empty_defensive(self) -> None:
        # Guard against unconfigured / legacy paths that pass ``None``.
        assert reasoning_replay_policy(None) == "unsupported"
        assert reasoning_replay_policy("") == "unsupported"


# ======================================================================
# ChatMessage.to_openai_dict(replay_policy=…) — serialisation gate
# ======================================================================


class TestChatMessageReplayFilter:
    """Ensure the policy is enforced at serialisation time."""

    def _msg_with_reasoning(self) -> ChatMessage:
        return ChatMessage(
            role="assistant",
            content="final text",
            reasoning_content="<thinking>internal</thinking>",
            signature="sig-xyz",
        )

    def test_backward_compat_no_policy_includes_reasoning(self) -> None:
        """Uncoordinated callers (legacy) keep the permissive behaviour."""
        msg = self._msg_with_reasoning()
        d = msg.to_openai_dict()
        assert d["reasoning_content"] == "<thinking>internal</thinking>"
        assert d["signature"] == "sig-xyz"

    def test_anthropic_thinking_includes_reasoning(self) -> None:
        msg = self._msg_with_reasoning()
        d = msg.to_openai_dict(replay_policy="anthropic_thinking")
        assert d["reasoning_content"] == "<thinking>internal</thinking>"
        assert d["signature"] == "sig-xyz"

    def test_informational_only_drops_reasoning(self) -> None:
        msg = self._msg_with_reasoning()
        d = msg.to_openai_dict(replay_policy="informational_only")
        assert "reasoning_content" not in d
        assert "signature" not in d
        # Regular content is still preserved for the next turn.
        assert d["content"] == "final text"

    def test_unsupported_drops_reasoning(self) -> None:
        msg = self._msg_with_reasoning()
        d = msg.to_openai_dict(replay_policy="unsupported")
        assert "reasoning_content" not in d
        assert "signature" not in d
        assert d["content"] == "final text"

    def test_policy_does_not_affect_cache_control(self) -> None:
        """cache_control is gated elsewhere (is_cache_capable) — must remain."""
        msg = ChatMessage(
            role="system",
            content="hi",
            cache_control={"type": "ephemeral"},
            reasoning_content="should be dropped",
        )
        d = msg.to_openai_dict(replay_policy="informational_only")
        assert d["cache_control"] == {"type": "ephemeral"}
        assert "reasoning_content" not in d

    def test_policy_does_not_affect_tool_calls(self) -> None:
        """Tool calls must remain on replay regardless of policy."""
        from fim_one.core.model.types import ToolCallRequest

        msg = ChatMessage(
            role="assistant",
            content=None,
            reasoning_content="dropped",
            tool_calls=[
                ToolCallRequest(id="call_1", name="echo", arguments={"x": 1}),
            ],
        )
        d = msg.to_openai_dict(replay_policy="informational_only")
        assert "reasoning_content" not in d
        assert d["tool_calls"][0]["function"]["name"] == "echo"


# ======================================================================
# _build_request_kwargs — the centralised enforcement point
# ======================================================================


class _StubLiteLLMResponse:
    """Bare-bones LiteLLM response stand-in for ``_build_request_kwargs``
    smoke tests.  We don't actually dispatch the call; we only verify
    that the kwargs dict contains the expected messages shape.
    """

    pass


def _build_kwargs_for(model: str, messages: list[ChatMessage]) -> dict[str, Any]:
    """Instantiate an OpenAI-compatible LLM and return its request kwargs.

    Drives the single centralised enforcement point
    (``_build_request_kwargs``) without making any network calls.
    """
    from fim_one.core.model.openai_compatible import OpenAICompatibleLLM

    llm = OpenAICompatibleLLM(
        api_key="test",
        base_url="https://api.example.com/v1",
        model=model,
    )
    return llm._build_request_kwargs(
        messages,
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
    )


class TestBuildRequestKwargsEnforcement:
    """Provider-specific assertions on the outgoing request messages."""

    def _history(self) -> list[ChatMessage]:
        return [
            ChatMessage(role="system", content="You are helpful."),
            ChatMessage(role="user", content="hi"),
            ChatMessage(
                role="assistant",
                content="done",
                reasoning_content="private CoT",
                signature="sig-abc",
            ),
            ChatMessage(role="user", content="again"),
        ]

    @pytest.mark.parametrize(
        "model",
        [
            "claude-sonnet-4-5",
            "anthropic/claude-opus-4-7",
            "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
            "vertex_ai/claude-sonnet-4@20241022",
        ],
    )
    def test_claude_family_replays_reasoning(self, model: str) -> None:
        kwargs = _build_kwargs_for(model, self._history())
        msgs = kwargs["messages"]
        assistant = next(m for m in msgs if m["role"] == "assistant")
        assert assistant["reasoning_content"] == "private CoT"
        assert assistant["signature"] == "sig-abc"

    @pytest.mark.parametrize(
        "model",
        [
            "deepseek-reasoner",
            "qwen-qwq-32b-preview",
            "gemini-2.0-flash-thinking-exp",
            "o1-preview",
            "o3-mini",
            "o4-mini",
        ],
    )
    def test_informational_providers_drop_reasoning(self, model: str) -> None:
        kwargs = _build_kwargs_for(model, self._history())
        msgs = kwargs["messages"]
        assistant = next(m for m in msgs if m["role"] == "assistant")
        assert "reasoning_content" not in assistant
        assert "signature" not in assistant
        # But the textual content MUST still be there.
        assert assistant["content"] == "done"

    @pytest.mark.parametrize(
        "model",
        [
            "gpt-4o",
            "gpt-4-turbo",
            "gemini-1.5-pro",
            "deepseek-chat",
        ],
    )
    def test_unsupported_providers_drop_reasoning(self, model: str) -> None:
        kwargs = _build_kwargs_for(model, self._history())
        msgs = kwargs["messages"]
        assistant = next(m for m in msgs if m["role"] == "assistant")
        assert "reasoning_content" not in assistant
        assert "signature" not in assistant


# ======================================================================
# Integration — full fake-LLM turn with reasoning_content on assistant
# ======================================================================


class _FakeReasoningLLM:
    """Minimal LLM stub for the ReAct integration test.

    Records the ``messages`` list it is called with on every turn so we
    can assert the reasoning filter survived into the second iteration.
    """

    def __init__(self, model_id: str) -> None:
        self._model_id = model_id
        self.call_log: list[list[ChatMessage]] = []

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def abilities(self) -> dict[str, bool]:
        return {
            "tool_call": False,
            "tool_choice": False,
            "json_mode": True,
            "vision": False,
            "streaming": False,
            "thinking": True,
        }

    async def chat(
        self,
        messages: list[ChatMessage],
        **_kwargs: Any,
    ) -> Any:
        # Snapshot the caller-supplied list before we return.
        self.call_log.append([*messages])
        from fim_one.core.model import LLMResult

        turn = len(self.call_log)
        if turn == 1:
            # First turn: emit reasoning_content AND a "not-yet-done"
            # answer so ReAct continues to a second iteration.
            import json

            return LLMResult(
                message=ChatMessage(
                    role="assistant",
                    content=json.dumps(
                        {
                            "type": "final_answer",
                            "reasoning": "(could not parse LLM output as JSON)",
                            "answer": "placeholder",
                        }
                    ),
                    reasoning_content="secret CoT from DeepSeek",
                    signature=None,
                ),
                usage={
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            )
        # Second turn: real final answer.
        import json

        return LLMResult(
            message=ChatMessage(
                role="assistant",
                content=json.dumps(
                    {
                        "type": "final_answer",
                        "reasoning": "done",
                        "answer": "42",
                    }
                ),
            ),
            usage={
                "prompt_tokens": 12,
                "completion_tokens": 3,
                "total_tokens": 15,
            },
        )

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        **_kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        async def _g() -> AsyncIterator[StreamChunk]:
            yield StreamChunk(delta_content="done")

        return _g()


def test_to_openai_dict_filters_reasoning_for_deepseek() -> None:
    """End-to-end: build outgoing messages with history containing
    reasoning_content for a non-Anthropic provider and verify the
    policy filter removes it.
    """
    history: list[ChatMessage] = [
        ChatMessage(role="system", content="You are helpful."),
        ChatMessage(role="user", content="hi"),
        ChatMessage(
            role="assistant",
            content="42",
            reasoning_content="secret DeepSeek CoT",
        ),
        ChatMessage(role="user", content="again"),
    ]
    kwargs = _build_kwargs_for("deepseek-reasoner", history)
    assistant = next(m for m in kwargs["messages"] if m["role"] == "assistant")
    assert assistant["content"] == "42"
    assert "reasoning_content" not in assistant
    assert "signature" not in assistant


def test_to_openai_dict_preserves_reasoning_for_claude() -> None:
    """Regression guard for A3 — Claude family must still receive the
    thinking block + signature on replay.
    """
    history: list[ChatMessage] = [
        ChatMessage(role="system", content="You are helpful."),
        ChatMessage(role="user", content="hi"),
        ChatMessage(
            role="assistant",
            content="42",
            reasoning_content="thinking about the universe",
            signature="opaque-signature-bytes",
        ),
        ChatMessage(role="user", content="again"),
    ]
    kwargs = _build_kwargs_for("claude-sonnet-4-5", history)
    assistant = next(m for m in kwargs["messages"] if m["role"] == "assistant")
    assert assistant["reasoning_content"] == "thinking about the universe"
    assert assistant["signature"] == "opaque-signature-bytes"

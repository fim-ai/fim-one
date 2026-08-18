"""A ``finish_reason="length"`` response must not dispatch any tool call.

**What broke.** A model emitted two tool calls and got cut off by the output
token limit before the second one finished. The first call's arguments were
complete JSON, so the guard let it through, ran it, and reported success. The
model had planned the two calls as one batch; the half that ran left the
conversation describing work that was only partly done.

**Why it broke.** The guard asked "do the arguments parse?" and treated a
parseable payload as proof the call survived intact. That is not what
``length`` means: it says the *message* was cut, not where. A batch cut
between calls leaves every emitted call parseable and the intended remainder
missing. A call cut before its first argument delta arrives is
indistinguishable from a legitimate argument-less call. And the streaming
aggregator salvages unparsable arguments into ``{"_raw": ...}``, so parsing
is not even a reliable truncation signal.

**The rule now.** ``finish_reason == "length"`` plus any tool call means the
whole batch is dropped and ``truncated_tool_call`` is reported, on all three
paths (chat completions, streaming completions, ``/v1/responses``).
``ReActAgent`` turns that flag into a retry prompt asking for smaller calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from fim_one.core.model.openai_compatible import OpenAICompatibleLLM
from fim_one.core.model.types import ChatMessage


@dataclass
class _FakeFunction:
    name: str = ""
    arguments: str = "{}"


@dataclass
class _FakeToolCall:
    id: str = "call_1"
    type: str = "function"
    function: _FakeFunction = field(default_factory=_FakeFunction)


@dataclass
class _FakeMessage:
    role: str = "assistant"
    content: str | None = None
    tool_calls: list[_FakeToolCall] | None = None


@dataclass
class _FakeChoice:
    message: _FakeMessage = field(default_factory=_FakeMessage)
    finish_reason: str | None = None


@dataclass
class _FakeResponse:
    choices: list[_FakeChoice] = field(default_factory=list)
    usage: Any = None


def _llm() -> OpenAICompatibleLLM:
    return OpenAICompatibleLLM(
        api_key="k",
        base_url="https://example.invalid/v1",
        model="test-model",
        rate_limit_config=None,
    )


def _response(finish_reason: str, *calls: tuple[str, str]) -> _FakeResponse:
    tool_calls = [
        _FakeToolCall(
            id=f"call_{i}",
            function=_FakeFunction(name=name, arguments=arguments),
        )
        for i, (name, arguments) in enumerate(calls, start=1)
    ]
    return _FakeResponse(
        choices=[
            _FakeChoice(
                message=_FakeMessage(tool_calls=tool_calls or None),
                finish_reason=finish_reason,
            )
        ]
    )


async def _chat(response: _FakeResponse) -> Any:
    llm = _llm()
    with patch.object(
        OpenAICompatibleLLM,
        "_dispatch_acompletion",
        new=AsyncMock(return_value=response),
    ):
        return await llm.chat([ChatMessage(role="user", content="hi")])


class TestTruncatedToolCallBatch:
    """The non-streaming ``chat()`` path."""

    @pytest.mark.asyncio
    async def test_parseable_sibling_of_a_cut_call_is_dropped(self) -> None:
        """The complete first call dies with the truncated second one."""
        result = await _chat(
            _response(
                "length",
                ("echo", '{"text": "hi"}'),
                ("file_ops", '{"path": "/tm'),
            )
        )

        assert result.message.tool_calls is None
        assert result.truncated_tool_call is True

    @pytest.mark.asyncio
    async def test_single_parseable_call_is_dropped_too(self) -> None:
        """Parseable arguments do not prove the batch was complete.

        The cut may have landed between calls, so what arrived is a prefix
        of what the model intended to send.
        """
        result = await _chat(_response("length", ("echo", '{"text": "hi"}')))

        assert result.message.tool_calls is None
        assert result.truncated_tool_call is True

    @pytest.mark.asyncio
    async def test_argumentless_call_is_dropped_too(self) -> None:
        """An empty payload is ambiguous: no arguments, or none yet."""
        result = await _chat(_response("length", ("ping", "")))

        assert result.message.tool_calls is None
        assert result.truncated_tool_call is True

    @pytest.mark.asyncio
    async def test_normal_stop_still_dispatches(self) -> None:
        """The guard is scoped to ``length``; a clean stop is untouched."""
        result = await _chat(_response("tool_calls", ("echo", '{"text": "hi"}')))

        assert result.message.tool_calls is not None
        assert len(result.message.tool_calls) == 1
        assert result.message.tool_calls[0].arguments == {"text": "hi"}
        assert result.truncated_tool_call is False

    @pytest.mark.asyncio
    async def test_length_without_tool_calls_is_not_flagged(self) -> None:
        """A truncated plain answer is a continuation case, not a drop."""
        result = await _chat(_response("length"))

        assert result.truncated_tool_call is False
        assert result.finish_reason == "length"

"""The one scripted ``BaseLLM`` the test suite uses.

Before this module every test that needed to drive the agent loop wrote its
own ``BaseLLM`` subclass: fifteen of them across nine files, each
re-implementing the same ``chat`` / ``stream_chat`` / ``abilities`` trio and
each capturing a slightly different subset of the request. Behaviour drifted
between the copies, and a change to ``BaseLLM`` meant fifteen edits.

``FakeLLM`` replaces all of them. A test scripts the turns the model should
produce, runs the real code path, and asserts on the trajectory:

    llm = FakeLLM([answer("done")])
    agent = ReActAgent(llm=llm, tools=registry)
    assert await agent.run("hi") == "done"

Scripting is per **turn**, in order; when the script runs out the last turn
repeats, so a test only has to spell out the turns it cares about.
``stream_chat()`` derives its chunks from the same turn as ``chat()``, so a
script works whether the code under test streams or not. Use
:func:`chunks` when a test needs to control the stream itself.

Every call is recorded on :attr:`FakeLLM.calls`, which is how a test asserts
on what the agent *sent*: the message list, tools, tool choice, response
format.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from fim_one.core.model import BaseLLM, ChatMessage, LLMResult, StreamChunk
from fim_one.core.model.types import ToolCallRequest

__all__ = [
    "FakeLLM",
    "FakeCall",
    "FakeResponse",
    "NATIVE_TOOLS",
    "NO_NATIVE_TOOLS",
    "answer",
    "chunks",
    "json_answer",
    "raises",
    "react_final_answer",
    "react_tool_call",
    "tool_call",
    "tool_calls",
    "truncated_tool_call",
]


# ---------------------------------------------------------------------------
# Ability presets
# ---------------------------------------------------------------------------

#: Text-only model: the ReAct loop must fall back to JSON-protocol prompting.
NO_NATIVE_TOOLS: dict[str, bool] = {
    "tool_call": False,
    "json_mode": False,
    "vision": False,
    "streaming": False,
}

#: Model with native function calling, which selects the ``_run_native`` path.
NATIVE_TOOLS: dict[str, bool] = {
    "tool_call": True,
    "json_mode": True,
    "vision": False,
    "streaming": True,
}


# ---------------------------------------------------------------------------
# One scripted turn
# ---------------------------------------------------------------------------


@dataclass
class FakeResponse:
    """One scripted model turn.

    Exactly one of the three fields is set. Build these with the helpers
    below rather than constructing them directly.
    """

    #: Returned from ``chat()``; converted to chunks for ``stream_chat()``.
    result: LLMResult | None = None
    #: Raised by both ``chat()`` and ``stream_chat()``.
    exc: Exception | None = None
    #: Yielded verbatim by ``stream_chat()``. ``chat()`` rejects the turn.
    stream: list[StreamChunk] | None = None


def answer(
    content: str,
    *,
    finish_reason: str = "stop",
    reasoning: str | None = None,
    usage: dict[str, int] | None = None,
) -> FakeResponse:
    """A plain assistant answer with no tool calls."""
    return FakeResponse(
        result=LLMResult(
            message=ChatMessage(
                role="assistant",
                content=content,
                reasoning_content=reasoning,
            ),
            usage=usage or {},
            finish_reason=finish_reason,
        )
    )


def json_answer(payload: dict[str, Any], **kwargs: Any) -> FakeResponse:
    """An answer whose content is *payload* serialised.

    The JSON-protocol ReAct path (models without native tool calling) reads
    the assistant's decision out of the message text.
    """
    return answer(json.dumps(payload, ensure_ascii=False), **kwargs)


def react_tool_call(
    tool_name: str = "echo",
    tool_args: dict[str, Any] | None = None,
    *,
    reasoning: str = "calling tool",
    **kwargs: Any,
) -> FakeResponse:
    """A JSON-protocol tool call, for models without native tool calling.

    The wire shape the ``_run_json`` path parses out of the message text.
    """
    return json_answer(
        {
            "type": "tool_call",
            "reasoning": reasoning,
            "tool_name": tool_name,
            "tool_args": tool_args if tool_args is not None else {"text": "ok"},
        },
        **kwargs,
    )


def react_final_answer(
    text: str = "done",
    *,
    reasoning: str = "finished",
    **kwargs: Any,
) -> FakeResponse:
    """A JSON-protocol final answer, the counterpart to :func:`react_tool_call`."""
    return json_answer(
        {"type": "final_answer", "reasoning": reasoning, "answer": text},
        **kwargs,
    )


def tool_call(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    call_id: str | None = None,
    content: str | None = None,
    usage: dict[str, int] | None = None,
) -> FakeResponse:
    """A native turn calling one tool.

    Args:
        name: Tool to call.
        arguments: Arguments for the call.
        call_id: Explicit call id; defaults to ``call_1``. Set it when the
            test asserts on which call a tool result belongs to.
        content: Optional preamble text streamed before the call.
        usage: Optional token usage attached to the turn.
    """
    return tool_calls(
        [(call_id or "call_1", name, arguments or {})],
        content=content,
        usage=usage,
    )


def tool_calls(
    calls: list[tuple[str, str, dict[str, Any]]],
    *,
    content: str | None = None,
    usage: dict[str, int] | None = None,
) -> FakeResponse:
    """A native turn calling several tools at once.

    Args:
        calls: ``(call_id, name, arguments)`` per call, in order. Use this
            to exercise how a batch is dispatched: in parallel, in order,
            or aborted part-way when one call fails.
        content: Optional preamble text streamed before the calls.
        usage: Optional token usage attached to the turn.
    """
    return FakeResponse(
        result=LLMResult(
            message=ChatMessage(
                role="assistant",
                content=content,
                tool_calls=[
                    ToolCallRequest(id=call_id, name=name, arguments=arguments)
                    for call_id, name, arguments in calls
                ],
            ),
            usage=usage or {},
            finish_reason="tool_calls",
        )
    )


def raises(exc: Exception) -> FakeResponse:
    """A turn that raises, for retry / fallback / degradation tests."""
    return FakeResponse(exc=exc)


def chunks(stream: list[StreamChunk]) -> FakeResponse:
    """A turn whose stream is spelled out chunk by chunk.

    For tests about the streaming protocol itself — split deltas, signatures,
    provider-specific finish reasons. Turns built this way have no
    non-streaming form, so ``chat()`` raises on them.
    """
    return FakeResponse(stream=list(stream))


def truncated_tool_call(preamble: str = "") -> FakeResponse:
    """A turn the output limit cut off while writing a tool call.

    Mirrors what ``OpenAICompatibleLLM`` emits once it drops the batch: the
    preamble text the model managed to write, then a ``length`` finish
    carrying the truncation flag and no calls.
    """
    stream = [StreamChunk(delta_content=preamble)] if preamble else []
    stream.append(StreamChunk(finish_reason="length", truncated_tool_call=True))
    return FakeResponse(stream=stream)


# ---------------------------------------------------------------------------
# Recorded requests
# ---------------------------------------------------------------------------


@dataclass
class FakeCall:
    """One recorded request, as the code under test issued it."""

    kind: str  # "chat" | "stream"
    messages: list[ChatMessage]
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    response_format: dict[str, Any] | None = None
    reasoning_effort: Any = None

    @property
    def system_prompt(self) -> str | None:
        """Content of the first system message, if any."""
        for message in self.messages:
            if message.role == "system":
                content = message.content
                return content if isinstance(content, str) else None
        return None

    def user_text(self) -> str:
        """Content of the last user message, for substring assertions."""
        for message in reversed(self.messages):
            if message.role == "user" and isinstance(message.content, str):
                return message.content
        return ""

    def text(self) -> str:
        """All message content joined, for substring assertions."""
        return "\n".join(
            m.content for m in self.messages if isinstance(m.content, str)
        )


# ---------------------------------------------------------------------------
# The fake
# ---------------------------------------------------------------------------


class FakeLLM(BaseLLM):
    """Replays scripted turns and records every request it receives.

    Args:
        responses: Scripted turns, in order. The last one repeats once the
            script is exhausted. Accepts bare ``LLMResult`` objects too, so
            tests written against the older fake keep working.
        abilities: Capability map. Defaults to :data:`NO_NATIVE_TOOLS`.
        model_id: Value reported by the ``model_id`` property.
        context_size: Value reported by the ``context_size`` property.
    """

    def __init__(
        self,
        responses: list[FakeResponse] | list[LLMResult] | None = None,
        *,
        abilities: dict[str, bool] | None = None,
        model_id: str | None = None,
        context_size: int | None = None,
    ) -> None:
        self._responses: list[FakeResponse] = [
            _coerce_turn(r) for r in (responses or [])
        ]
        self._abilities = abilities
        self._model_id = model_id
        self._context_size = context_size
        #: Every request received, in order.
        self.calls: list[FakeCall] = []

    # -- scripting ----------------------------------------------------------

    def set_responses(self, responses: list[FakeResponse] | list[LLMResult]) -> None:
        """Replace the script. Does not reset recorded calls."""
        self._responses = [_coerce_turn(r) for r in responses]

    def _next_turn(self) -> FakeResponse:
        if not self._responses:
            raise AssertionError(
                "FakeLLM was called but has no scripted turns. "
                "Pass responses to the constructor or call set_responses()."
            )
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        return self._responses[index]

    # -- recorded requests --------------------------------------------------

    @property
    def call_count(self) -> int:
        """Number of ``chat()`` + ``stream_chat()`` calls received."""
        return len(self.calls)

    @property
    def chat_call_count(self) -> int:
        return len(self.chat_calls)

    @property
    def stream_call_count(self) -> int:
        return len(self.stream_calls)

    @property
    def chat_calls(self) -> list[FakeCall]:
        """Only the non-streaming requests."""
        return [c for c in self.calls if c.kind == "chat"]

    @property
    def stream_calls(self) -> list[FakeCall]:
        """Only the streaming requests."""
        return [c for c in self.calls if c.kind == "stream"]

    @property
    def all_messages(self) -> list[list[ChatMessage]]:
        """Message list of every request, in order."""
        return [c.messages for c in self.calls]

    @property
    def last_call(self) -> FakeCall:
        """Most recent request. Fails loudly when nothing was sent."""
        if not self.calls:
            raise AssertionError("FakeLLM received no calls")
        return self.calls[-1]

    @property
    def received_tools(self) -> list[dict[str, Any]] | None:
        """Tools sent on the most recent request."""
        return self.last_call.tools

    @property
    def received_tool_choice(self) -> str | dict[str, Any] | None:
        """Tool choice sent on the most recent request."""
        return self.last_call.tool_choice

    # -- BaseLLM ------------------------------------------------------------

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
        self.calls.append(
            FakeCall(
                kind="chat",
                messages=list(messages),
                tools=tools,
                tool_choice=tool_choice,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                reasoning_effort=reasoning_effort,
            )
        )
        turn = self._next_turn()
        if turn.exc is not None:
            raise turn.exc
        if turn.result is None:
            raise AssertionError(
                "This turn was scripted with chunks() and has no non-streaming "
                "form; the code under test called chat() instead of stream_chat()."
            )
        return turn.result

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        self.calls.append(
            FakeCall(
                kind="stream",
                messages=list(messages),
                tools=tools,
                tool_choice=tool_choice,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        )
        turn = self._next_turn()
        if turn.exc is not None:
            raise turn.exc
        if turn.stream is not None:
            for chunk in turn.stream:
                yield chunk
            return
        assert turn.result is not None
        for chunk in _result_to_chunks(turn.result):
            yield chunk

    # -- capabilities -------------------------------------------------------

    @property
    def abilities(self) -> dict[str, bool]:
        if self._abilities is not None:
            return self._abilities
        return dict(NO_NATIVE_TOOLS)

    @property
    def model_id(self) -> str | None:
        return self._model_id

    @property
    def context_size(self) -> int | None:
        return self._context_size


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _coerce_turn(response: FakeResponse | LLMResult) -> FakeResponse:
    if isinstance(response, FakeResponse):
        return response
    return FakeResponse(result=response)


def _result_to_chunks(result: LLMResult) -> list[StreamChunk]:
    """Render an ``LLMResult`` as the chunks a provider would stream.

    Reasoning first, then content, then one terminal chunk carrying the
    finish reason, any tool calls, and usage — the shape ``_run_native``
    and ``_stream_tool_decision`` consume.
    """
    message = result.message
    stream: list[StreamChunk] = []
    if message.reasoning_content:
        stream.append(StreamChunk(delta_reasoning=message.reasoning_content))
    if isinstance(message.content, str) and message.content:
        stream.append(StreamChunk(delta_content=message.content))

    finish = result.finish_reason
    if finish is None:
        finish = "tool_calls" if message.tool_calls else "stop"
    stream.append(
        StreamChunk(
            finish_reason=finish,
            tool_calls=message.tool_calls,
            usage=result.usage or None,
            truncated_tool_call=result.truncated_tool_call,
        )
    )
    return stream

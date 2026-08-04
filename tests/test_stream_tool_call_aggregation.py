"""Tests for the streaming tool-call aggregation logic.

Verifies that the ``_iterate()`` accumulator inside
``OpenAICompatibleLLM.stream_chat()`` correctly handles providers that
reuse ``index=0`` for every streamed tool-call delta (e.g. Claude via
certain proxies).  Without the ``index_remap`` logic, multiple concurrent
tool calls collide on the same slot and produce corrupted arguments.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.core.model.openai_compatible import (
    OpenAICompatibleLLM,
    _PartialToolCall,
)
from fim_one.core.model.types import StreamChunk, ToolCallRequest


# ---------------------------------------------------------------------------
# Lightweight stub objects that mimic the LiteLLM streaming response shape
# ---------------------------------------------------------------------------


@dataclass
class _FakeFunction:
    name: str | None = None
    arguments: str | None = None


@dataclass
class _FakeToolCallDelta:
    index: int = 0
    id: str | None = None
    function: _FakeFunction | None = None


@dataclass
class _FakeDelta:
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[_FakeToolCallDelta] | None = None


@dataclass
class _FakeChoice:
    delta: _FakeDelta = field(default_factory=_FakeDelta)
    finish_reason: str | None = None


@dataclass
class _FakeChunk:
    choices: list[_FakeChoice] = field(default_factory=list)
    usage: Any = None


def _tc_chunk(
    *,
    index: int = 0,
    tc_id: str | None = None,
    name: str | None = None,
    arguments: str | None = None,
    finish_reason: str | None = None,
) -> _FakeChunk:
    """Build a single streaming chunk carrying one tool-call delta."""
    fn = _FakeFunction(name=name, arguments=arguments)
    tc = _FakeToolCallDelta(index=index, id=tc_id, function=fn)
    delta = _FakeDelta(tool_calls=[tc])
    choice = _FakeChoice(delta=delta, finish_reason=finish_reason)
    return _FakeChunk(choices=[choice])


def _finish_chunk(finish_reason: str = "tool_calls") -> _FakeChunk:
    """Build a chunk that signals stream end with no tool-call delta."""
    delta = _FakeDelta()
    choice = _FakeChoice(delta=delta, finish_reason=finish_reason)
    return _FakeChunk(choices=[choice])


# ---------------------------------------------------------------------------
# Helper to drive the stream and collect emitted StreamChunks
# ---------------------------------------------------------------------------


async def _run_stream(chunks: list[_FakeChunk]) -> list[StreamChunk]:
    """Feed *chunks* through ``OpenAICompatibleLLM.stream_chat()``.

    Patches ``litellm.acompletion`` to return an async iterator over the
    supplied fake chunks, then collects all yielded ``StreamChunk`` objects.
    """

    async def _fake_stream():
        for c in chunks:
            yield c

    llm = OpenAICompatibleLLM(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        model="gpt-4o",
        retry_config=None,
        rate_limit_config=None,
    )

    with patch("fim_one.core.model.openai_compatible.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=_fake_stream())
        result: list[StreamChunk] = []
        stream_iter = await llm._stream_chat_impl(messages=[])
        async for sc in stream_iter:
            result.append(sc)
        return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStreamToolCallAggregation:
    """Boundary detection when providers reuse index=0."""

    @pytest.mark.asyncio
    async def test_two_different_tools_same_index_split_by_id(self) -> None:
        """Two calls with different ids at index=0 produce two ToolCallRequests."""
        chunks = [
            # First tool call — header
            _tc_chunk(index=0, tc_id="call_1", name="get_weather", arguments='{"ci'),
            _tc_chunk(index=0, arguments='ty": "NYC"}'),
            # Second tool call — new id signals boundary
            _tc_chunk(index=0, tc_id="call_2", name="get_time", arguments='{"tz"'),
            _tc_chunk(index=0, arguments=': "EST"}'),
            # Finish
            _finish_chunk(),
        ]
        result = await _run_stream(chunks)

        # Find the chunk that carries tool_calls
        tc_chunks = [c for c in result if c.tool_calls]
        assert len(tc_chunks) == 1
        tool_calls = tc_chunks[0].tool_calls
        assert tool_calls is not None
        assert len(tool_calls) == 2

        assert tool_calls[0].name == "get_weather"
        assert tool_calls[0].arguments == {"city": "NYC"}
        assert tool_calls[0].id == "call_1"

        assert tool_calls[1].name == "get_time"
        assert tool_calls[1].arguments == {"tz": "EST"}
        assert tool_calls[1].id == "call_2"

    @pytest.mark.asyncio
    async def test_same_tool_invoked_twice_same_index_different_id(self) -> None:
        """Same tool name called twice with different ids — must not merge."""
        chunks = [
            _tc_chunk(index=0, tc_id="call_A", name="search", arguments='{"q":'),
            _tc_chunk(index=0, arguments=' "cats"}'),
            _tc_chunk(index=0, tc_id="call_B", name="search", arguments='{"q":'),
            _tc_chunk(index=0, arguments=' "dogs"}'),
            _finish_chunk(),
        ]
        result = await _run_stream(chunks)

        tc_chunks = [c for c in result if c.tool_calls]
        assert len(tc_chunks) == 1
        tool_calls = tc_chunks[0].tool_calls
        assert tool_calls is not None
        assert len(tool_calls) == 2

        assert tool_calls[0].id == "call_A"
        assert tool_calls[0].arguments == {"q": "cats"}

        assert tool_calls[1].id == "call_B"
        assert tool_calls[1].arguments == {"q": "dogs"}

    @pytest.mark.asyncio
    async def test_different_name_triggers_boundary(self) -> None:
        """Different tool name at same index without explicit id change."""
        chunks = [
            _tc_chunk(index=0, tc_id="call_1", name="tool_a", arguments='{"x": 1}'),
            # New name, no id — boundary by name difference
            _tc_chunk(index=0, name="tool_b", arguments='{"y": 2}'),
            _finish_chunk(),
        ]
        result = await _run_stream(chunks)

        tc_chunks = [c for c in result if c.tool_calls]
        assert len(tc_chunks) == 1
        tool_calls = tc_chunks[0].tool_calls
        assert tool_calls is not None
        assert len(tool_calls) == 2

        assert tool_calls[0].name == "tool_a"
        assert tool_calls[0].arguments == {"x": 1}

        assert tool_calls[1].name == "tool_b"
        assert tool_calls[1].arguments == {"y": 2}

    @pytest.mark.asyncio
    async def test_arg_only_deltas_follow_remap(self) -> None:
        """Arg-only deltas (no name/id) after a boundary use the remapped slot."""
        chunks = [
            _tc_chunk(index=0, tc_id="c1", name="fn1", arguments='{"a":'),
            _tc_chunk(index=0, arguments=' 1}'),
            # Boundary — new tool call
            _tc_chunk(index=0, tc_id="c2", name="fn2", arguments='{"b":'),
            # Arg-only continuation — must land in the remapped (fn2) slot
            _tc_chunk(index=0, arguments=' 2,'),
            _tc_chunk(index=0, arguments=' "c": 3}'),
            _finish_chunk(),
        ]
        result = await _run_stream(chunks)

        tc_chunks = [c for c in result if c.tool_calls]
        assert len(tc_chunks) == 1
        tool_calls = tc_chunks[0].tool_calls
        assert tool_calls is not None
        assert len(tool_calls) == 2

        assert tool_calls[0].name == "fn1"
        assert tool_calls[0].arguments == {"a": 1}

        assert tool_calls[1].name == "fn2"
        assert tool_calls[1].arguments == {"b": 2, "c": 3}

    @pytest.mark.asyncio
    async def test_three_tools_same_index(self) -> None:
        """Three tool calls all at index=0 — each gets its own slot."""
        chunks = [
            _tc_chunk(index=0, tc_id="c1", name="alpha", arguments='{"v": 1}'),
            _tc_chunk(index=0, tc_id="c2", name="beta", arguments='{"v": 2}'),
            _tc_chunk(index=0, tc_id="c3", name="gamma", arguments='{"v": 3}'),
            _finish_chunk(),
        ]
        result = await _run_stream(chunks)

        tc_chunks = [c for c in result if c.tool_calls]
        assert len(tc_chunks) == 1
        tool_calls = tc_chunks[0].tool_calls
        assert tool_calls is not None
        assert len(tool_calls) == 3

        assert tool_calls[0].name == "alpha"
        assert tool_calls[1].name == "beta"
        assert tool_calls[2].name == "gamma"
        for i, tc in enumerate(tool_calls, 1):
            assert tc.arguments == {"v": i}

    @pytest.mark.asyncio
    async def test_normal_distinct_indices_unaffected(self) -> None:
        """Standard behavior: distinct indices work without remap."""
        chunks = [
            _tc_chunk(index=0, tc_id="c1", name="fn_a", arguments='{"k": "a"}'),
            _tc_chunk(index=1, tc_id="c2", name="fn_b", arguments='{"k": "b"}'),
            _finish_chunk(),
        ]
        result = await _run_stream(chunks)

        tc_chunks = [c for c in result if c.tool_calls]
        assert len(tc_chunks) == 1
        tool_calls = tc_chunks[0].tool_calls
        assert tool_calls is not None
        assert len(tool_calls) == 2

        assert tool_calls[0].name == "fn_a"
        assert tool_calls[0].arguments == {"k": "a"}
        assert tool_calls[1].name == "fn_b"
        assert tool_calls[1].arguments == {"k": "b"}

    @pytest.mark.asyncio
    async def test_single_tool_call_no_collision(self) -> None:
        """A single tool call with fragmented args works normally."""
        chunks = [
            _tc_chunk(index=0, tc_id="c1", name="search", arguments='{"query'),
            _tc_chunk(index=0, arguments='": "hello'),
            _tc_chunk(index=0, arguments=' world"}'),
            _finish_chunk(),
        ]
        result = await _run_stream(chunks)

        tc_chunks = [c for c in result if c.tool_calls]
        assert len(tc_chunks) == 1
        tool_calls = tc_chunks[0].tool_calls
        assert tool_calls is not None
        assert len(tool_calls) == 1

        assert tool_calls[0].name == "search"
        assert tool_calls[0].arguments == {"query": "hello world"}


class TestPartialToolCallDirect:
    """Unit tests for the _PartialToolCall accumulator."""

    def test_default_values(self) -> None:
        p = _PartialToolCall()
        assert p.id == ""
        assert p.name == ""
        assert p.arguments == ""

    def test_accumulation(self) -> None:
        p = _PartialToolCall()
        p.id = "call_1"
        p.name = "my_tool"
        p.arguments += '{"a":'
        p.arguments += " 1}"
        assert p.arguments == '{"a": 1}'


class TestFlushToolCalls:
    """Unit tests for _flush_tool_calls static method."""

    def test_sorted_output(self) -> None:
        """Tool calls are emitted in index order."""
        pending: dict[int, _PartialToolCall] = {}

        p2 = _PartialToolCall()
        p2.id = "c2"
        p2.name = "beta"
        p2.arguments = '{"v": 2}'
        pending[5] = p2

        p1 = _PartialToolCall()
        p1.id = "c1"
        p1.name = "alpha"
        p1.arguments = '{"v": 1}'
        pending[0] = p1

        result = OpenAICompatibleLLM._flush_tool_calls(pending)
        assert len(result) == 2
        assert result[0].name == "alpha"
        assert result[1].name == "beta"

    def test_malformed_json_uses_raw(self) -> None:
        """Bad JSON arguments fall back to _raw wrapper."""
        pending: dict[int, _PartialToolCall] = {}
        p = _PartialToolCall()
        p.id = "c1"
        p.name = "broken"
        p.arguments = "{invalid json"
        pending[0] = p

        result = OpenAICompatibleLLM._flush_tool_calls(pending)
        assert len(result) == 1
        assert result[0].arguments == {"_raw": "{invalid json"}


class TestOutputLimitDuringToolCall:
    """A tool call cut off mid-arguments by ``finish_reason="length"``."""

    @pytest.mark.asyncio
    async def test_truncated_tool_call_is_dropped_and_reported(self) -> None:
        """The half-written call is dropped; the truncation is surfaced."""
        chunks = [
            _tc_chunk(index=0, tc_id="call_1", name="file_ops", arguments='{"content": "<html'),
            _tc_chunk(index=0, arguments="><body>lots of markup"),
            _finish_chunk("length"),
        ]
        result = await _run_stream(chunks)

        # No tool call escapes with half a JSON payload.
        assert not any(c.tool_calls for c in result)
        # ...and the finish reason is not swallowed on the way out.
        final = [c for c in result if c.finish_reason]
        assert final, "finish_reason must reach the caller"
        assert final[-1].finish_reason == "length"
        assert final[-1].truncated_tool_call is True

    @pytest.mark.asyncio
    async def test_complete_call_survives_length_finish(self) -> None:
        """Arguments that did finish are still dispatched under "length"."""
        chunks = [
            _tc_chunk(index=0, tc_id="call_1", name="echo", arguments='{"text": "hi"}'),
            _finish_chunk("length"),
        ]
        result = await _run_stream(chunks)

        tc_chunks = [c for c in result if c.tool_calls]
        assert len(tc_chunks) == 1
        tool_calls = tc_chunks[0].tool_calls
        assert tool_calls is not None
        assert tool_calls[0].arguments == {"text": "hi"}
        assert tc_chunks[0].truncated_tool_call is False

    @pytest.mark.asyncio
    async def test_unknown_finish_reason_still_flushes(self) -> None:
        """A provider-specific terminal reason must not strand the call."""
        chunks = [
            _tc_chunk(index=0, tc_id="call_1", name="echo", arguments='{"text": "hi"}'),
            _finish_chunk("end_turn"),
        ]
        result = await _run_stream(chunks)

        tc_chunks = [c for c in result if c.tool_calls]
        assert len(tc_chunks) == 1
        assert tc_chunks[0].finish_reason == "end_turn"

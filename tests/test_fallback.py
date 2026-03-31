"""Tests for the FallbackLLM wrapper."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from fim_one.core.model.base import REASONING_INHERIT, BaseLLM
from fim_one.core.model.fallback import FallbackLLM, is_availability_error
from fim_one.core.model.types import ChatMessage, LLMResult, StreamChunk


# ======================================================================
# Helpers
# ======================================================================


def _make_status_error(status_code: int, msg: str = "") -> Exception:
    """Create a mock exception with a ``status_code`` attribute."""
    err = Exception(msg or f"HTTP {status_code}")
    err.status_code = status_code  # type: ignore[attr-defined]
    return err


def _make_named_error(class_name: str) -> Exception:
    """Create an exception whose class name matches a known error type."""
    cls: type[Exception] = type(class_name, (Exception,), {})
    return cls(f"Mock {class_name}")


def _msg(content: str) -> ChatMessage:
    return ChatMessage(role="user", content=content)


def _result(content: str) -> LLMResult:
    return LLMResult(
        message=ChatMessage(role="assistant", content=content),
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )


class StubLLM(BaseLLM):
    """A stub LLM that returns a fixed response or raises an exception."""

    def __init__(
        self,
        *,
        chat_result: LLMResult | None = None,
        chat_error: Exception | None = None,
        stream_chunks: list[StreamChunk] | None = None,
        stream_error: Exception | None = None,
        model_id_val: str = "stub-model",
    ) -> None:
        self._chat_result = chat_result
        self._chat_error = chat_error
        self._stream_chunks = stream_chunks or []
        self._stream_error = stream_error
        self._model_id_val = model_id_val
        self.chat_call_count = 0
        self.stream_call_count = 0

    @property
    def model_id(self) -> str:
        return self._model_id_val

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        reasoning_effort: str | object | None = REASONING_INHERIT,
    ) -> LLMResult:
        self.chat_call_count += 1
        if self._chat_error is not None:
            raise self._chat_error
        assert self._chat_result is not None
        return self._chat_result

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        self.stream_call_count += 1
        if self._stream_error is not None:
            raise self._stream_error
        for chunk in self._stream_chunks:
            yield chunk


# ======================================================================
# is_availability_error
# ======================================================================


class TestIsAvailabilityError:
    """Verify error classification for fallback decisions."""

    def test_429_is_availability_error(self) -> None:
        assert is_availability_error(_make_status_error(429)) is True

    def test_503_is_availability_error(self) -> None:
        assert is_availability_error(_make_status_error(503)) is True

    def test_529_is_availability_error(self) -> None:
        assert is_availability_error(_make_status_error(529)) is True

    def test_400_not_availability_error(self) -> None:
        assert is_availability_error(_make_status_error(400)) is False

    def test_401_not_availability_error(self) -> None:
        assert is_availability_error(_make_status_error(401)) is False

    def test_403_not_availability_error(self) -> None:
        assert is_availability_error(_make_status_error(403)) is False

    def test_404_not_availability_error(self) -> None:
        assert is_availability_error(_make_status_error(404)) is False

    def test_500_not_availability_error(self) -> None:
        """500 is a server error but not an availability error for fallback."""
        assert is_availability_error(_make_status_error(500)) is False

    def test_context_overflow_not_availability(self) -> None:
        """Context overflow should NOT trigger fallback."""
        err = _make_status_error(400, "maximum context length exceeded")
        assert is_availability_error(err) is False

    def test_connection_error_is_availability(self) -> None:
        assert is_availability_error(ConnectionError("refused")) is True

    def test_timeout_error_is_availability(self) -> None:
        assert is_availability_error(TimeoutError("timed out")) is True

    def test_api_connection_error_by_name(self) -> None:
        assert is_availability_error(_make_named_error("APIConnectionError")) is True

    def test_api_timeout_error_by_name(self) -> None:
        assert is_availability_error(_make_named_error("APITimeoutError")) is True

    def test_service_unavailable_error_by_name(self) -> None:
        assert is_availability_error(_make_named_error("ServiceUnavailableError")) is True

    def test_generic_value_error_not_availability(self) -> None:
        assert is_availability_error(ValueError("bad")) is False

    def test_generic_runtime_error_not_availability(self) -> None:
        assert is_availability_error(RuntimeError("fail")) is False


# ======================================================================
# FallbackLLM — chat()
# ======================================================================


class TestFallbackChat:
    """Verify fallback behaviour for non-streaming chat calls."""

    async def test_primary_success_no_fallback(self) -> None:
        """When primary succeeds, fallback is never called."""
        primary = StubLLM(chat_result=_result("primary answer"))
        fallback = StubLLM(chat_result=_result("fallback answer"))
        llm = FallbackLLM(primary=primary, fallback=fallback)

        result = await llm.chat([_msg("hello")])
        assert result.message.content == "primary answer"
        assert primary.chat_call_count == 1
        assert fallback.chat_call_count == 0

    async def test_fallback_on_429(self) -> None:
        """Rate limited primary triggers fallback."""
        primary = StubLLM(chat_error=_make_status_error(429))
        fallback = StubLLM(chat_result=_result("fallback"))
        llm = FallbackLLM(primary=primary, fallback=fallback)

        result = await llm.chat([_msg("hello")])
        assert result.message.content == "fallback"
        assert primary.chat_call_count == 1
        assert fallback.chat_call_count == 1

    async def test_fallback_on_503(self) -> None:
        """Service unavailable primary triggers fallback."""
        primary = StubLLM(chat_error=_make_status_error(503))
        fallback = StubLLM(chat_result=_result("fallback"))
        llm = FallbackLLM(primary=primary, fallback=fallback)

        result = await llm.chat([_msg("hello")])
        assert result.message.content == "fallback"

    async def test_fallback_on_529(self) -> None:
        """Overloaded primary triggers fallback."""
        primary = StubLLM(chat_error=_make_status_error(529))
        fallback = StubLLM(chat_result=_result("fallback"))
        llm = FallbackLLM(primary=primary, fallback=fallback)

        result = await llm.chat([_msg("hello")])
        assert result.message.content == "fallback"

    async def test_fallback_on_connection_error(self) -> None:
        """Connection error triggers fallback."""
        primary = StubLLM(chat_error=ConnectionError("refused"))
        fallback = StubLLM(chat_result=_result("fallback"))
        llm = FallbackLLM(primary=primary, fallback=fallback)

        result = await llm.chat([_msg("hello")])
        assert result.message.content == "fallback"

    async def test_fallback_on_timeout_error(self) -> None:
        """Timeout error triggers fallback."""
        primary = StubLLM(chat_error=TimeoutError("timed out"))
        fallback = StubLLM(chat_result=_result("fallback"))
        llm = FallbackLLM(primary=primary, fallback=fallback)

        result = await llm.chat([_msg("hello")])
        assert result.message.content == "fallback"

    async def test_no_fallback_on_400(self) -> None:
        """Bad request error propagates without fallback."""
        error = _make_status_error(400)
        primary = StubLLM(chat_error=error)
        fallback = StubLLM(chat_result=_result("fallback"))
        llm = FallbackLLM(primary=primary, fallback=fallback)

        with pytest.raises(Exception) as exc_info:
            await llm.chat([_msg("hello")])
        assert exc_info.value is error
        assert fallback.chat_call_count == 0

    async def test_no_fallback_on_401(self) -> None:
        """Auth error propagates without fallback."""
        error = _make_status_error(401)
        primary = StubLLM(chat_error=error)
        fallback = StubLLM(chat_result=_result("fallback"))
        llm = FallbackLLM(primary=primary, fallback=fallback)

        with pytest.raises(Exception) as exc_info:
            await llm.chat([_msg("hello")])
        assert exc_info.value is error
        assert fallback.chat_call_count == 0

    async def test_no_fallback_on_context_overflow(self) -> None:
        """Context overflow is NOT an availability error -- different recovery path."""
        error = _make_status_error(400, "maximum context length exceeded")
        primary = StubLLM(chat_error=error)
        fallback = StubLLM(chat_result=_result("fallback"))
        llm = FallbackLLM(primary=primary, fallback=fallback)

        with pytest.raises(Exception) as exc_info:
            await llm.chat([_msg("hello")])
        assert exc_info.value is error
        assert fallback.chat_call_count == 0

    async def test_fallback_error_propagates(self) -> None:
        """If both primary and fallback fail, the fallback error propagates."""
        fallback_error = _make_status_error(500, "fallback also failed")
        primary = StubLLM(chat_error=_make_status_error(503))
        fallback = StubLLM(chat_error=fallback_error)
        llm = FallbackLLM(primary=primary, fallback=fallback)

        with pytest.raises(Exception) as exc_info:
            await llm.chat([_msg("hello")])
        assert exc_info.value is fallback_error


# ======================================================================
# FallbackLLM — stream_chat()
# ======================================================================


class TestFallbackStreamChat:
    """Verify fallback behaviour for streaming chat calls."""

    async def test_primary_stream_success(self) -> None:
        """Successful primary stream yields all chunks."""
        chunks = [
            StreamChunk(delta_content="hello"),
            StreamChunk(delta_content=" world", finish_reason="stop"),
        ]
        primary = StubLLM(stream_chunks=chunks)
        fallback = StubLLM(stream_chunks=[StreamChunk(delta_content="fallback")])
        llm = FallbackLLM(primary=primary, fallback=fallback)

        collected: list[StreamChunk] = []
        async for c in llm.stream_chat([_msg("hi")]):
            collected.append(c)

        assert len(collected) == 2
        assert collected[0].delta_content == "hello"
        assert collected[1].delta_content == " world"
        assert primary.stream_call_count == 1
        assert fallback.stream_call_count == 0

    async def test_fallback_stream_on_503(self) -> None:
        """503 from primary stream triggers fallback stream."""
        primary = StubLLM(stream_error=_make_status_error(503))
        fallback = StubLLM(stream_chunks=[StreamChunk(delta_content="fallback")])
        llm = FallbackLLM(primary=primary, fallback=fallback)

        collected: list[StreamChunk] = []
        async for c in llm.stream_chat([_msg("hi")]):
            collected.append(c)

        assert len(collected) == 1
        assert collected[0].delta_content == "fallback"

    async def test_fallback_stream_on_connection_error(self) -> None:
        """Connection error from primary triggers fallback stream."""
        primary = StubLLM(stream_error=ConnectionError("refused"))
        fallback = StubLLM(stream_chunks=[StreamChunk(delta_content="fb")])
        llm = FallbackLLM(primary=primary, fallback=fallback)

        collected: list[StreamChunk] = []
        async for c in llm.stream_chat([_msg("hi")]):
            collected.append(c)

        assert len(collected) == 1
        assert collected[0].delta_content == "fb"

    async def test_no_fallback_stream_on_400(self) -> None:
        """400 from primary stream propagates without fallback."""
        error = _make_status_error(400)
        primary = StubLLM(stream_error=error)
        fallback = StubLLM(stream_chunks=[StreamChunk(delta_content="fb")])
        llm = FallbackLLM(primary=primary, fallback=fallback)

        with pytest.raises(Exception) as exc_info:
            async for _ in llm.stream_chat([_msg("hi")]):
                pass
        assert exc_info.value is error
        assert fallback.stream_call_count == 0

    async def test_empty_primary_stream(self) -> None:
        """Empty stream from primary is fine -- no fallback triggered."""
        primary = StubLLM(stream_chunks=[])
        fallback = StubLLM(stream_chunks=[StreamChunk(delta_content="fb")])
        llm = FallbackLLM(primary=primary, fallback=fallback)

        collected: list[StreamChunk] = []
        async for c in llm.stream_chat([_msg("hi")]):
            collected.append(c)

        assert len(collected) == 0
        assert fallback.stream_call_count == 0


# ======================================================================
# FallbackLLM — properties
# ======================================================================


class TestFallbackProperties:
    """Verify property delegation to the primary LLM."""

    def test_model_id_delegates_to_primary(self) -> None:
        primary = StubLLM(chat_result=_result("x"), model_id_val="gpt-4o")
        fallback = StubLLM(chat_result=_result("y"), model_id_val="gpt-4o-mini")
        llm = FallbackLLM(primary=primary, fallback=fallback)

        assert llm.model_id == "gpt-4o"

    def test_abilities_delegates_to_primary(self) -> None:
        primary = StubLLM(chat_result=_result("x"))
        fallback = StubLLM(chat_result=_result("y"))
        llm = FallbackLLM(primary=primary, fallback=fallback)

        assert llm.abilities == primary.abilities

    def test_primary_and_fallback_accessors(self) -> None:
        primary = StubLLM(chat_result=_result("x"))
        fallback = StubLLM(chat_result=_result("y"))
        llm = FallbackLLM(primary=primary, fallback=fallback)

        assert llm.primary is primary
        assert llm.fallback is fallback

"""Fallback LLM wrapper for automatic provider failover.

When the primary LLM encounters availability failures (HTTP 429/503/529),
the wrapper transparently retries the request on a backup LLM rather than
surfacing the error to the caller.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from .base import REASONING_INHERIT, BaseLLM
from .retry import is_context_overflow
from .types import ChatMessage, LLMResult, StreamChunk

logger = logging.getLogger(__name__)

# HTTP status codes that trigger fallback (availability failures only).
_FALLBACK_STATUS_CODES: frozenset[int] = frozenset({429, 503, 529})


def is_availability_error(exc: Exception) -> bool:
    """Check whether an exception indicates a provider availability failure.

    Triggers on:
    - HTTP 529 (overloaded)
    - HTTP 503 (service unavailable)
    - HTTP 429 (rate limited) after the retry layer has already exhausted retries

    Does NOT trigger on:
    - HTTP 400 (bad request / validation)
    - HTTP 401/403 (authentication)
    - Context overflow errors (handled separately by ContextGuard)
    - Any other non-availability error

    Args:
        exc: The exception to inspect.

    Returns:
        ``True`` if the error is an availability failure suitable for fallback.
    """
    # Never fallback on context overflow -- that has its own recovery path.
    if is_context_overflow(exc):
        return False

    status_code: int | None = getattr(exc, "status_code", None)
    if status_code is not None and status_code in _FALLBACK_STATUS_CODES:
        return True

    # Connection and timeout errors also indicate availability problems.
    error_type_name = type(exc).__name__
    availability_names = {
        "APIConnectionError",
        "APITimeoutError",
        "ConnectError",
        "ReadTimeout",
        "ConnectTimeout",
        "TimeoutException",
        "Timeout",
        "ServiceUnavailableError",
    }
    if error_type_name in availability_names:
        return True

    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True

    return False


class FallbackLLM(BaseLLM):
    """Wraps a primary LLM with a fallback for availability failures.

    Delegates all calls to the primary LLM first.  If the primary fails with
    an availability error (HTTP 529 overloaded, 503 service unavailable, or
    429 rate limited after retries are exhausted), the request is automatically
    retried on the fallback LLM.

    This is transparent to the caller -- the returned ``LLMResult`` /
    ``StreamChunk`` objects are identical regardless of which backend served
    the request.

    Note: content errors (400 bad request, 401 auth, etc.) and context
    overflow errors are NOT caught -- those propagate to the caller for
    proper handling (e.g. ContextGuard for overflow).

    Args:
        primary: The preferred LLM instance.
        fallback: The backup LLM instance used when the primary is unavailable.
    """

    def __init__(self, primary: BaseLLM, fallback: BaseLLM) -> None:
        self._primary = primary
        self._fallback = fallback

    @property
    def model_id(self) -> str | None:
        return self._primary.model_id

    @property
    def context_size(self) -> int | None:
        return self._primary.context_size

    @property
    def abilities(self) -> dict[str, bool]:
        return self._primary.abilities

    @property
    def primary(self) -> BaseLLM:
        """Access the underlying primary LLM."""
        return self._primary

    @property
    def fallback(self) -> BaseLLM:
        """Access the underlying fallback LLM."""
        return self._fallback

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
        """Send a chat request, falling back on availability errors."""
        try:
            return await self._primary.chat(
                messages,
                tools=tools,
                tool_choice=tool_choice,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                reasoning_effort=reasoning_effort,
            )
        except Exception as exc:
            if not is_availability_error(exc):
                raise
            logger.warning(
                "Primary model unavailable, falling back to %s: %s",
                self._fallback.model_id or "fallback",
                exc,
            )
            return await self._fallback.chat(
                messages,
                tools=tools,
                tool_choice=tool_choice,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                reasoning_effort=reasoning_effort,
            )

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Send a streaming request, falling back on availability errors."""
        try:
            iterator = self._primary.stream_chat(
                messages,
                tools=tools,
                tool_choice=tool_choice,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            # We need to actually start iteration to detect connection errors.
            # The async generator from stream_chat may raise on the first
            # __anext__ call (when the HTTP connection is established), not
            # at creation time.  We eagerly fetch the first chunk to detect
            # such failures before committing to the primary stream.
            first_chunk: StreamChunk | None = None
            try:
                first_chunk = await iterator.__anext__()
            except StopAsyncIteration:
                # Empty stream from primary -- that's fine, just return.
                return

            # Primary stream started successfully -- yield all chunks.
            yield first_chunk
            async for chunk in iterator:
                yield chunk

        except Exception as exc:
            if not is_availability_error(exc):
                raise
            logger.warning(
                "Primary model unavailable (stream), falling back to %s: %s",
                self._fallback.model_id or "fallback",
                exc,
            )
            async for chunk in self._fallback.stream_chat(
                messages,
                tools=tools,
                tool_choice=tool_choice,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                yield chunk

"""Abstract base class for LLM providers."""

from __future__ import annotations

__fim_license__ = "FIM-SAL-1.1"
__fim_origin__ = "https://github.com/fim-ai/fim-one"

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from .types import ChatMessage, LLMResult, StreamChunk


class BaseLLM(ABC):
    """Abstract base for all LLM implementations.

    Subclasses must implement ``chat`` and ``stream_chat``.  They may also
    override ``abilities`` to declare which optional features the underlying
    model supports.
    """

    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResult:
        """Send a chat completion request.

        Args:
            messages: The conversation history.
            tools: Optional list of tool definitions in OpenAI format.
            tool_choice: Optional tool choice constraint (e.g. ``"auto"``,
                ``"none"``, or a dict specifying a particular function).
            temperature: Sampling temperature override.
            max_tokens: Maximum tokens to generate.
            response_format: Optional response format constraint (e.g. JSON mode).

        Returns:
            An ``LLMResult`` containing the assistant message and token usage.
        """
        ...

    @abstractmethod
    async def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Send a streaming chat completion request.

        Args:
            messages: The conversation history.
            tools: Optional list of tool definitions in OpenAI format.
            tool_choice: Optional tool choice constraint (e.g. ``"auto"``,
                ``"none"``, or a dict specifying a particular function).
            temperature: Sampling temperature override.
            max_tokens: Maximum tokens to generate.

        Yields:
            ``StreamChunk`` instances as they arrive from the provider.
        """
        ...
        # Ensure the method signature is recognised as an async generator.
        # The explicit `yield` is never reached but satisfies the type checker.
        yield  # pragma: no cover

    @property
    def model_id(self) -> str | None:
        """Return the underlying model identifier, if known."""
        return None

    @property
    def abilities(self) -> dict[str, bool]:
        """Declare model capabilities.

        Override in subclasses to advertise which features are supported.

        Returns:
            A mapping of capability name to availability flag.
        """
        return {
            "tool_call": False,
            "json_mode": False,
            "vision": False,
            "streaming": False,
        }

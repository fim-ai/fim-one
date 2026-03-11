"""OpenAI-compatible LLM implementation.

Uses LiteLLM to route requests to any provider (OpenAI, Anthropic, Gemini,
DeepSeek, Mistral, etc.) without provider-specific conditionals.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import litellm

from .base import BaseLLM
from .rate_limit import RateLimitConfig, TokenBucketRateLimiter
from .retry import RetryConfig, retry_async_call, retry_async_iterator
from .types import ChatMessage, LLMResult, StreamChunk, ToolCallRequest

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LiteLLM global configuration
# ---------------------------------------------------------------------------
litellm.num_retries = 0  # We use our own retry.py
litellm.drop_params = True  # Silently drop unsupported params per model
litellm.suppress_debug_info = True

# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------

# Domain → provider for official API endpoints.
KNOWN_DOMAINS: dict[str, str] = {
    "api.openai.com": "openai",
    "anthropic.com": "anthropic",
    "generativelanguage.googleapis.com": "gemini",
    "api.deepseek.com": "deepseek",
    "api.mistral.ai": "mistral",
}

# URL path segments that hint at provider protocol on relay platforms
# (e.g. UniAPI: /claude → Anthropic native, /gemini → Google native).
PATH_PROVIDER_HINTS: dict[str, str] = {
    "/claude": "anthropic",
    "/anthropic": "anthropic",
    "/gemini": "gemini",
}

# Providers whose native protocol LiteLLM handles — these need api_base
# when the domain isn't the official one (i.e. relay/proxy scenarios).
_NATIVE_PROVIDERS = frozenset(KNOWN_DOMAINS.values())


def _resolve_litellm_model(
    base_url: str,
    model: str,
    provider: str | None = None,
) -> tuple[str, str | None]:
    """Map (base_url, model, provider) to (litellm_model, optional api_base).

    Resolution order:
    1. Explicit ``provider`` (from DB ModelConfig.provider) — highest priority.
    2. Domain match against ``KNOWN_DOMAINS`` (official API endpoints).
    3. URL path hint against ``PATH_PROVIDER_HINTS`` (relay platforms).
    4. Fallback to ``openai/`` prefix (generic OpenAI-compatible).

    For official endpoints (step 2), no ``api_base`` is returned because
    LiteLLM routes natively.  For everything else, ``api_base`` is included
    so LiteLLM knows where to send the request.
    """
    # 1. Explicit provider from DB config
    if provider:
        for domain, prov in KNOWN_DOMAINS.items():
            if prov == provider and domain in base_url:
                return f"{provider}/{model}", None  # Official endpoint
        return f"{provider}/{model}", base_url  # Relay/proxy

    # 2. Domain match (official APIs)
    for domain, prov in KNOWN_DOMAINS.items():
        if domain in base_url:
            return f"{prov}/{model}", None

    # 3. URL path hint (relay platforms like UniAPI)
    for path_segment, prov in PATH_PROVIDER_HINTS.items():
        if path_segment in base_url:
            return f"{prov}/{model}", base_url

    # 4. Generic OpenAI-compatible fallback
    return f"openai/{model}", base_url


class OpenAICompatibleLLM(BaseLLM):
    """LLM implementation backed by LiteLLM for universal provider support.

    Works with OpenAI, Anthropic, Gemini, DeepSeek, Mistral, vLLM, Ollama,
    and any other provider that LiteLLM supports or that exposes an
    OpenAI-compatible ``/v1/chat/completions`` interface.

    Args:
        api_key: API key for authentication.
        base_url: Base URL of the API (e.g. ``https://api.openai.com/v1``).
        model: Model identifier (e.g. ``gpt-4o``).
        default_temperature: Fallback temperature when none is specified per-call.
        default_max_tokens: Fallback max_tokens when none is specified per-call.
        retry_config: Configuration for retry with exponential backoff.
            Pass ``None`` to disable retries entirely.
        rate_limit_config: Configuration for the token-bucket rate limiter.
            Pass ``None`` to disable rate limiting entirely.
        reasoning_effort: Optional reasoning effort level (``low``/``medium``/``high``).
        reasoning_budget_tokens: Optional explicit token budget for Anthropic thinking.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        *,
        default_temperature: float = 0.7,
        default_max_tokens: int = 64000,
        retry_config: RetryConfig | None = RetryConfig(),
        rate_limit_config: RateLimitConfig | None = RateLimitConfig(),
        reasoning_effort: str | None = None,
        reasoning_budget_tokens: int | None = None,
        provider: str | None = None,
        json_mode_enabled: bool = True,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._litellm_model, self._api_base = _resolve_litellm_model(
            base_url, model, provider,
        )
        self._default_temperature = default_temperature
        self._default_max_tokens = default_max_tokens
        self._retry_config = retry_config or RetryConfig(max_retries=0)
        self._rate_limiter: TokenBucketRateLimiter | None = (
            TokenBucketRateLimiter(rate_limit_config) if rate_limit_config else None
        )
        self._reasoning_effort = reasoning_effort
        self._reasoning_budget_tokens = reasoning_budget_tokens
        self._json_mode_enabled = json_mode_enabled

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def api_key(self) -> str:
        return self._api_key

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
        """Send a non-streaming chat completion request.

        The call is automatically wrapped with rate limiting and retry logic
        according to the configuration supplied at construction time.
        """
        return await retry_async_call(
            self._chat_impl,
            self._retry_config,
            messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )

    async def _chat_impl(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResult:
        """Inner implementation of ``chat()`` -- one attempt, no retry."""
        if self._rate_limiter is not None:
            await self._rate_limiter.acquire()

        kwargs = self._build_request_kwargs(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            stream=False,
        )
        response = await litellm.acompletion(**kwargs)

        choice = response.choices[0]
        assistant_msg = self._parse_choice_message(choice)

        usage: dict[str, int] = {}
        if response.usage is not None:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        # Report actual token usage back to the rate limiter.
        if self._rate_limiter is not None and usage.get("total_tokens"):
            await self._rate_limiter.report_usage(usage["total_tokens"])

        return LLMResult(message=assistant_msg, usage=usage)

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

        The entire stream is retried from scratch on transient failures.
        Rate limiting is applied before each attempt.

        Yields ``StreamChunk`` instances as they arrive.  Tool-call deltas are
        accumulated and emitted as complete ``ToolCallRequest`` objects once the
        stream signals ``finish_reason`` of ``"tool_calls"`` or ``"stop"``.
        """
        async for chunk in retry_async_iterator(
            self._stream_chat_impl,
            self._retry_config,
            messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            yield chunk

    async def _stream_chat_impl(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Inner implementation of ``stream_chat()`` -- one attempt, no retry."""
        if self._rate_limiter is not None:
            await self._rate_limiter.acquire()

        kwargs = self._build_request_kwargs(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        stream = await litellm.acompletion(**kwargs)

        async def _iterate() -> AsyncIterator[StreamChunk]:
            # Accumulate partial tool calls keyed by their index in the array.
            pending_tool_calls: dict[int, _PartialToolCall] = {}
            stream_usage: dict[str, int] | None = None
            usage_yielded = False

            async for chunk in stream:  # type: ignore[union-attr]
                # Extract usage from any chunk that carries it (typically the
                # final chunk, which may have empty choices).
                if hasattr(chunk, "usage") and chunk.usage is not None:
                    stream_usage = {
                        "prompt_tokens": chunk.usage.prompt_tokens,
                        "completion_tokens": chunk.usage.completion_tokens,
                        "total_tokens": chunk.usage.total_tokens,
                    }

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta
                finish_reason = chunk.choices[0].finish_reason

                # --- content / reasoning fragments ---
                delta_content = getattr(delta, "content", None)
                delta_reasoning = getattr(delta, "reasoning_content", None)

                # --- tool-call fragments ---
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in pending_tool_calls:
                            pending_tool_calls[idx] = _PartialToolCall()
                        partial = pending_tool_calls[idx]
                        if tc_delta.id:
                            partial.id = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                # Index collision: provider reused the same
                                # index for a different tool call.  Allocate
                                # a fresh slot so names don't concatenate.
                                if partial.name and partial.name != tc_delta.function.name:
                                    idx = max(pending_tool_calls.keys()) + 1
                                    pending_tool_calls[idx] = _PartialToolCall()
                                    partial = pending_tool_calls[idx]
                                partial.name = tc_delta.function.name
                            if tc_delta.function.arguments:
                                partial.arguments += tc_delta.function.arguments

                # Emit a chunk for every delta that carries content or reasoning.
                if delta_content or delta_reasoning:
                    yield StreamChunk(
                        delta_content=delta_content,
                        delta_reasoning=delta_reasoning,
                        finish_reason=finish_reason,
                    )

                # When the stream finishes, flush any accumulated tool calls.
                if finish_reason in ("tool_calls", "stop") and pending_tool_calls:
                    completed = self._flush_tool_calls(pending_tool_calls)
                    yield StreamChunk(
                        finish_reason=finish_reason,
                        tool_calls=completed,
                        usage=stream_usage,
                    )
                    usage_yielded = stream_usage is not None
                    pending_tool_calls.clear()
                elif finish_reason and not pending_tool_calls:
                    # Final chunk with no tool calls (normal stop).
                    yield StreamChunk(finish_reason=finish_reason, usage=stream_usage)
                    usage_yielded = stream_usage is not None

            # Emit trailing usage if it arrived on a separate empty-choices
            # chunk after finish_reason was already processed.
            if stream_usage and not usage_yielded:
                yield StreamChunk(usage=stream_usage)

        return _iterate()

    @property
    def abilities(self) -> dict[str, bool]:
        """Capability flags for the LLM.

        ``tool_call`` is always True — ReAct uses ``tool_choice="auto"``
        which works fine even with Anthropic thinking enabled.
        ``structured_llm_call`` uses forced ``tool_choice`` which Anthropic
        rejects when thinking is active, but its own try/except fallback
        handles the 400 gracefully (native_fc → json_mode → plain_text).
        """
        return {
            "tool_call": True,
            "json_mode": self._json_mode_enabled,
            "vision": True,
            "streaming": True,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_request_kwargs(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None,
        max_tokens: int | None,
        response_format: dict[str, Any] | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        """Build the keyword arguments dict for ``litellm.acompletion()``."""
        effective_temperature = (
            temperature if temperature is not None else self._default_temperature
        )
        token_limit = max_tokens if max_tokens is not None else self._default_max_tokens

        kwargs: dict[str, Any] = {
            "model": self._litellm_model,
            "messages": [m.to_openai_dict() for m in messages],
            "temperature": effective_temperature,
            "max_tokens": token_limit,
            "stream": stream,
            "api_key": self._api_key,
        }
        if stream:
            kwargs["stream_options"] = {"include_usage": True}
        if self._api_base is not None:
            kwargs["api_base"] = self._api_base
        if tools:
            kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
        if response_format is not None:
            kwargs["response_format"] = response_format
        if self._reasoning_effort:
            # GPT-5.x /v1/chat/completions rejects reasoning_effort when
            # tools are present.  Silently skip to keep agent workflows working.
            if tools and self._model.lower().startswith("gpt-5"):
                logger.debug(
                    "Dropping reasoning_effort for %s (unsupported with tools in chat completions)",
                    self._model,
                )
            elif self._reasoning_budget_tokens and self._litellm_model.startswith("anthropic/"):
                # Explicit budget override — pass thinking directly, skip
                # reasoning_effort to avoid LiteLLM's auto-mapping conflict.
                kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": self._reasoning_budget_tokens,
                }
                # Bedrock rejects temperature != 1.0 when thinking is enabled
                kwargs["temperature"] = 1.0
            else:
                # Let LiteLLM handle the translation for each provider:
                #   - Anthropic: reasoning_effort → thinking param (auto budget)
                #   - OpenAI o-series: reasoning_effort passed through
                #   - Others: drop_params=True handles unsupported cases
                kwargs["reasoning_effort"] = self._reasoning_effort
                # LiteLLM maps reasoning_effort → thinking for Anthropic/Bedrock;
                # Bedrock rejects temperature != 1.0 when thinking is enabled
                if self._litellm_model.startswith("anthropic/"):
                    kwargs["temperature"] = 1.0
        return kwargs

    @staticmethod
    def _parse_tool_calls(
        raw_tool_calls: list[Any],
    ) -> list[ToolCallRequest]:
        """Parse tool calls from a response choice."""
        result: list[ToolCallRequest] = []
        for tc in raw_tool_calls:
            try:
                arguments = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "Failed to parse tool-call arguments for %s, using raw string",
                    tc.function.name,
                )
                arguments = {"_raw": tc.function.arguments}
            result.append(
                ToolCallRequest(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=arguments,
                )
            )
        return result

    @staticmethod
    def _parse_choice_message(choice: Any) -> ChatMessage:
        """Convert a response choice object into a ``ChatMessage``."""
        msg = choice.message
        tool_calls: list[ToolCallRequest] | None = None
        if msg.tool_calls:
            tool_calls = OpenAICompatibleLLM._parse_tool_calls(msg.tool_calls)
        # Extract extended thinking / reasoning content.
        # Different providers use different field names:
        #   - DeepSeek R1: reasoning_content
        #   - Anthropic (via LiteLLM): reasoning_content
        #   - Some proxies: reasoning
        reasoning_content = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
        # Guard against the field being a non-string (e.g. dict from some proxies).
        if reasoning_content and not isinstance(reasoning_content, str):
            reasoning_content = None
        return ChatMessage(
            role=msg.role,
            content=msg.content,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
        )

    @staticmethod
    def _flush_tool_calls(
        pending: dict[int, _PartialToolCall],
    ) -> list[ToolCallRequest]:
        """Convert accumulated partial tool calls into complete requests."""
        completed: list[ToolCallRequest] = []
        for idx in sorted(pending):
            partial = pending[idx]
            try:
                arguments = json.loads(partial.arguments)
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "Failed to parse streamed tool-call arguments for %s, using raw string",
                    partial.name,
                )
                arguments = {"_raw": partial.arguments}
            completed.append(
                ToolCallRequest(
                    id=partial.id,
                    name=partial.name,
                    arguments=arguments,
                )
            )
        return completed


class _PartialToolCall:
    """Mutable accumulator for streamed tool-call fragments."""

    __slots__ = ("arguments", "id", "name")

    def __init__(self) -> None:
        self.id: str = ""
        self.name: str = ""
        self.arguments: str = ""

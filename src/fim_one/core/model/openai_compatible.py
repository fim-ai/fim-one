"""OpenAI-compatible LLM implementation.

Uses LiteLLM to route requests to any provider (OpenAI, Anthropic, Gemini,
DeepSeek, Mistral, etc.) without provider-specific conditionals.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import litellm

from .base import REASONING_INHERIT, BaseLLM

# Local alias — shorter than importing from base everywhere.
_REASONING_INHERIT = REASONING_INHERIT
from .rate_limit import RateLimitConfig, TokenBucketRateLimiter
from .retry import RetryConfig, retry_async_call, retry_async_iterator
from .types import ChatMessage, LLMResult, StreamChunk, ToolCallRequest

logger = logging.getLogger(__name__)

# Regex to extract <think>…</think> blocks from model content.
# Some providers (MiniMax, QwQ, etc.) wrap CoT reasoning this way
# instead of using an API-level reasoning_content field.
_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)

# ---------------------------------------------------------------------------
# LiteLLM global configuration
# ---------------------------------------------------------------------------
litellm.num_retries = 0  # We use our own retry.py
litellm.drop_params = True  # Silently drop unsupported params per model
litellm.suppress_debug_info = True

# ---------------------------------------------------------------------------
# Connection pooling — shared httpx.AsyncClient for all LLM calls
# ---------------------------------------------------------------------------
# LiteLLM internally caches OpenAI SDK clients (AsyncOpenAI) keyed by
# (api_key, api_base, timeout, …) in ``litellm.in_memory_llm_clients_cache``
# (up to 200 entries, 600 s TTL).  Each cached client normally creates its
# own httpx.AsyncClient with *default* pool settings (unlimited connections,
# 5 s keepalive expiry).  The short keepalive means connections are dropped
# after just 5 seconds of idle time — wasteful for bursty LLM workloads.
#
# By setting ``litellm.aclient_session`` to a long-lived client with tuned
# pool limits, *all* OpenAI-compatible providers share the same connection
# pool with better keepalive behaviour.  This is transparent to callers —
# the litellm.acompletion() API is unchanged.
#
# Pool sizing rationale:
#   - max_connections=100: enough for concurrent agent/DAG/streaming calls
#   - max_keepalive_connections=20: keep warm connections to frequent providers
#   - keepalive_expiry=30: much longer than the default 5 s; avoids needless
#     reconnects between successive LLM calls in a single agent turn
#   - connect timeout 10 s, overall timeout 300 s (LLM responses can be slow)

_SHARED_HTTP_CLIENT: httpx.AsyncClient | None = None


def _get_shared_http_client() -> httpx.AsyncClient:
    """Return (and lazily create) the module-level shared httpx.AsyncClient.

    The client is also installed as ``litellm.aclient_session`` so that
    LiteLLM's internal OpenAI SDK client factory uses it automatically.
    """
    global _SHARED_HTTP_CLIENT
    if _SHARED_HTTP_CLIENT is None or _SHARED_HTTP_CLIENT.is_closed:
        _SHARED_HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=10.0),
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=30,
            ),
            follow_redirects=True,
        )
        # Tell LiteLLM to use this client for all OpenAI-compatible providers.
        litellm.aclient_session = _SHARED_HTTP_CLIENT
        logger.info(
            "Shared HTTP connection pool initialised "
            "(max_conn=100, keepalive=20, keepalive_expiry=30s)"
        )
    return _SHARED_HTTP_CLIENT


async def close_shared_http_client() -> None:
    """Close the shared httpx.AsyncClient and reset litellm's session reference.

    Call this during application shutdown (e.g. in the FastAPI lifespan)
    to release connections cleanly.
    """
    global _SHARED_HTTP_CLIENT
    litellm.aclient_session = None
    if _SHARED_HTTP_CLIENT is not None and not _SHARED_HTTP_CLIENT.is_closed:
        await _SHARED_HTTP_CLIENT.aclose()
        logger.info("Shared HTTP connection pool closed")
    _SHARED_HTTP_CLIENT = None


# Eagerly initialise the shared client so it is ready for the first LLM call.
_get_shared_http_client()

# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------

# Domain → LiteLLM provider prefix for endpoints that LiteLLM can route
# natively (no api_base needed).  Only providers with built-in endpoint
# routing in LiteLLM belong here.  All other OpenAI-compatible providers
# (MiniMax, DashScope, Moonshot, etc.) are handled by the generic fallback
# which passes api_base so requests reach the correct server.
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

    # 2. Domain match (official APIs — LiteLLM routes natively)
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
        context_size: Optional context window size in tokens.  When provided,
            downstream components (e.g. ContextGuard in DAG executor) can
            compute model-aware token budgets instead of using a global default.
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
        tool_choice_enabled: bool = True,
        context_size: int | None = None,
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
        self._tool_choice_enabled = tool_choice_enabled
        self._context_size = context_size

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def context_size(self) -> int | None:
        return self._context_size

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
        reasoning_effort: str | object | None = _REASONING_INHERIT,
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
            reasoning_effort=reasoning_effort,
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
        reasoning_effort: str | object | None = _REASONING_INHERIT,
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
            reasoning_effort=reasoning_effort,
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
            think_parser = _ThinkTagStreamParser()

            async for chunk in stream:
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

                # Re-route <think>...</think> from content to reasoning.
                if delta_content:
                    parsed_content, parsed_reasoning = think_parser.feed(
                        delta_content,
                    )
                    delta_content = parsed_content or None
                    if parsed_reasoning:
                        delta_reasoning = (
                            (delta_reasoning or "") + parsed_reasoning
                        ) or None

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

            # Flush any remaining buffered <think> content.
            flush_content, flush_reasoning = think_parser.flush()
            if flush_content or flush_reasoning:
                yield StreamChunk(
                    delta_content=flush_content or None,
                    delta_reasoning=flush_reasoning or None,
                )

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
            "tool_choice": self._tool_choice_enabled,
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
        reasoning_effort: str | object | None = _REASONING_INHERIT,
    ) -> dict[str, Any]:
        """Build the keyword arguments dict for ``litellm.acompletion()``.

        Args:
            reasoning_effort: Per-call override.  ``_REASONING_INHERIT``
                (default) falls back to the instance-level setting;
                ``None`` suppresses reasoning; a string overrides the level.
        """
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

        # Resolve effective reasoning effort: per-call override > instance default.
        effective_reasoning = (
            self._reasoning_effort
            if reasoning_effort is _REASONING_INHERIT
            else reasoning_effort
        )
        if effective_reasoning:
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
                kwargs["reasoning_effort"] = effective_reasoning
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
        # Strip <think>...</think> from content (providers like MiniMax embed
        # CoT this way instead of using an API-level reasoning field).
        content = msg.content
        if isinstance(content, str) and "<think>" in content:
            think_parts: list[str] = []

            def _collect(m: re.Match[str]) -> str:
                think_parts.append(m.group(1).strip())
                return ""

            content = _THINK_RE.sub(_collect, content).strip() or None
            if think_parts:
                extracted = "\n".join(think_parts)
                reasoning_content = (
                    reasoning_content + "\n" + extracted
                    if reasoning_content
                    else extracted
                )
        return ChatMessage(
            role=msg.role,
            content=content,
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


class _ThinkTagStreamParser:
    """Re-routes ``<think>...</think>`` from streamed content to reasoning.

    Some providers (MiniMax, QwQ) embed chain-of-thought inside the
    ``content`` field wrapped in ``<think>`` tags rather than using an
    API-level ``reasoning_content`` field.  This parser detects the pattern
    and transparently reroutes the thinking portion to ``delta_reasoning``
    so it renders in the Reasoning panel instead of the Answer.

    State machine:
        DETECT  -- did stream start with ``<think>``?
        THINKING -- inside the think block, emit as reasoning
        CONTENT  -- normal content passthrough
    """

    DETECT = 0
    THINKING = 1
    CONTENT = 2

    _OPEN = "<think>"
    _CLOSE = "</think>"

    def __init__(self) -> None:
        self._state = self.DETECT
        self._buf = ""

    def feed(self, text: str) -> tuple[str, str]:
        """Process a content delta.

        Returns:
            ``(content_to_emit, reasoning_to_emit)`` -- either may be empty.
        """
        self._buf += text

        # --- DETECT: decide if stream starts with <think> ---
        if self._state == self.DETECT:
            stripped = self._buf.lstrip()
            if len(stripped) < len(self._OPEN):
                # Not enough data yet -- check if it could still be a prefix
                if self._OPEN.startswith(stripped):
                    return "", ""  # keep buffering
                # Not a <think> prefix -- passthrough
                self._state = self.CONTENT
                out = self._buf
                self._buf = ""
                return out, ""

            if stripped.startswith(self._OPEN):
                self._state = self.THINKING
                # Drop everything up to and including <think>
                self._buf = stripped[len(self._OPEN):]
                # Fall through to THINKING
            else:
                self._state = self.CONTENT
                out = self._buf
                self._buf = ""
                return out, ""

        # --- THINKING: emit as reasoning until </think> ---
        if self._state == self.THINKING:
            close_idx = self._buf.find(self._CLOSE)
            if close_idx != -1:
                reasoning = self._buf[:close_idx]
                after = self._buf[close_idx + len(self._CLOSE):]
                self._buf = ""
                self._state = self.CONTENT
                content = after.lstrip("\n") if after.strip() else ""
                return content, reasoning
            # Keep a tail buffer in case </think> is split across chunks
            safe = len(self._buf) - (len(self._CLOSE) - 1)
            if safe > 0:
                reasoning = self._buf[:safe]
                self._buf = self._buf[safe:]
                return "", reasoning
            return "", ""

        # --- CONTENT: passthrough ---
        out = self._buf
        self._buf = ""
        return out, ""

    def flush(self) -> tuple[str, str]:
        """Flush remaining buffer at end of stream."""
        if not self._buf:
            return "", ""
        buf = self._buf
        self._buf = ""
        if self._state == self.THINKING:
            return "", buf
        return buf, ""


class _PartialToolCall:
    """Mutable accumulator for streamed tool-call fragments."""

    __slots__ = ("arguments", "id", "name")

    def __init__(self) -> None:
        self.id: str = ""
        self.name: str = ""
        self.arguments: str = ""

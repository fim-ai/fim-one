"""OpenAI-compatible LLM implementation.

Uses LiteLLM to route requests to any provider (OpenAI, Anthropic, Gemini,
DeepSeek, Mistral, etc.) without provider-specific conditionals.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import litellm
from litellm.exceptions import BadRequestError, NotFoundError

from fim_one.core.prompt.reasoning import reasoning_replay_policy

from .base import REASONING_INHERIT, BaseLLM

# Local alias — shorter than importing from base everywhere.
_REASONING_INHERIT = REASONING_INHERIT
from .normalize import normalize_alternating_messages
from .rate_limit import RateLimitConfig, TokenBucketRateLimiter
from .responses_adapter import (
    build_responses_input,
    convert_response_format,
    convert_tool_choice,
    convert_tools,
    parse_response,
    stream_to_chunks,
)
from .retry import RetryConfig, retry_async_call, retry_async_iterator
from .types import ChatMessage, LLMResult, StreamChunk, ToolCallRequest

logger = logging.getLogger(__name__)

# Regex to extract <think>…</think> blocks from model content.
# Some providers (MiniMax, QwQ, etc.) wrap CoT reasoning this way
# instead of using an API-level reasoning_content field.
_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)


def _merge_cache_usage(usage: dict[str, int], raw_usage: Any) -> None:
    """Pull Anthropic-style cache token counters from a LiteLLM usage object.

    LiteLLM surfaces Anthropic prompt-caching counters in two shapes:

    * Directly on the usage object:
      ``usage.cache_read_input_tokens`` /
      ``usage.cache_creation_input_tokens`` (modern LiteLLM + Anthropic
      native routes).
    * Nested under ``usage.prompt_tokens_details`` (OpenAI-compat shim
      for some proxies):
      ``usage.prompt_tokens_details.cached_tokens``.

    Both paths are probed best-effort.  Missing / malformed fields
    default to ``0`` — this helper must never raise on an unexpected
    provider response shape because it runs on the hot path.

    The helper mutates *usage* in place, adding:

    * ``cache_read_input_tokens`` — number of input tokens served from
      the Anthropic prompt cache on this call (billed at ~10% of
      normal input rate).
    * ``cache_creation_input_tokens`` — number of input tokens written
      to the cache on this call (billed at ~125% of normal).

    Downstream consumers (``UsageTracker``, ``TurnProfiler``, the
    ``/chat/*`` SSE payload) can then surface cache efficiency without
    needing provider-specific parsing logic.
    """
    cache_read = 0
    cache_creation = 0
    # Direct attributes (Anthropic native / LiteLLM >= 1.50).
    direct_read = getattr(raw_usage, "cache_read_input_tokens", None)
    direct_creation = getattr(raw_usage, "cache_creation_input_tokens", None)
    if isinstance(direct_read, int):
        cache_read = direct_read
    if isinstance(direct_creation, int):
        cache_creation = direct_creation
    # Nested fallback under prompt_tokens_details (OpenAI-compat shim).
    if cache_read == 0:
        details = getattr(raw_usage, "prompt_tokens_details", None)
        nested_read = getattr(details, "cached_tokens", None)
        if isinstance(nested_read, int):
            cache_read = nested_read
    usage["cache_read_input_tokens"] = cache_read
    usage["cache_creation_input_tokens"] = cache_creation


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
# Pool sizing rationale (all overridable via env — see below):
#   - max_connections=100: enough for concurrent agent/DAG/streaming calls
#   - max_keepalive_connections=20: keep warm connections to frequent providers
#   - keepalive_expiry=5: a connection idle longer than this is DROPPED rather
#     than silently reused.  A longer expiry (we previously used 30 s) opens a
#     window where an intermediary (relay / NAT / LB) silently reaps an idle
#     connection — no FIN/RST — and httpx, which does not probe a pooled
#     connection before reuse, hands back the half-dead socket; the next write
#     fails with ``APIConnectionError: Connection error``.  Successive LLM
#     calls within a single agent turn are milliseconds-to-seconds apart, so a
#     5 s expiry still lets them reuse a warm connection; only cross-turn /
#     low-traffic idle connections (the ones that actually go stale) get
#     discarded.  Set ``LLM_HTTP_MAX_KEEPALIVE=0`` to disable reuse entirely
#     (one TLS handshake per call; zero stale-reuse risk).
#   - connect timeout 10 s, overall timeout 300 s (LLM responses can be slow)
#
# Env overrides:
#   - LLM_HTTP_MAX_CONNECTIONS   (default 100)
#   - LLM_HTTP_MAX_KEEPALIVE     (default 20; 0 = never reuse a connection)
#   - LLM_HTTP_KEEPALIVE_EXPIRY  (default 5.0, seconds)

_SHARED_HTTP_CLIENT: httpx.AsyncClient | None = None


def _incomplete_arguments(raw: str | None) -> bool:
    """Return True when a tool call's argument JSON never finished arriving.

    Used to tell a genuinely complete call apart from one the output limit
    cut in half.  An empty payload counts as complete — some providers send
    argument-less calls.
    """
    if not raw:
        return False
    try:
        json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return True
    return False


def _pool_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    return int(raw) if raw else default


def _pool_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    return float(raw) if raw else default


def _get_shared_http_client() -> httpx.AsyncClient:
    """Return (and lazily create) the module-level shared httpx.AsyncClient.

    The client is also installed as ``litellm.aclient_session`` so that
    LiteLLM's internal OpenAI SDK client factory uses it automatically.

    Pool limits are read from the environment (see module comment above) so
    operators can tune keep-alive behaviour — or disable connection reuse
    entirely — without a code change when running behind a flaky upstream.
    """
    global _SHARED_HTTP_CLIENT
    if _SHARED_HTTP_CLIENT is None or _SHARED_HTTP_CLIENT.is_closed:
        recreating = _SHARED_HTTP_CLIENT is not None  # closed → being replaced
        max_conn = _pool_int("LLM_HTTP_MAX_CONNECTIONS", 100)
        max_keepalive = _pool_int("LLM_HTTP_MAX_KEEPALIVE", 20)
        keepalive_expiry = _pool_float("LLM_HTTP_KEEPALIVE_EXPIRY", 5.0)
        _SHARED_HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=10.0),
            limits=httpx.Limits(
                max_connections=max_conn,
                max_keepalive_connections=max_keepalive,
                keepalive_expiry=keepalive_expiry,
            ),
            follow_redirects=True,
        )
        # Tell LiteLLM to use this client for all OpenAI-compatible providers.
        litellm.aclient_session = _SHARED_HTTP_CLIENT
        # CRITICAL: LiteLLM caches AsyncOpenAI clients (200 entries / 600 s TTL)
        # that each wrap the shared session above.  When LiteLLM evicts one of
        # those cached clients (idle-TTL expiry — e.g. after a quiet period), the
        # OpenAI SDK closes the http_client it was handed, which is *our shared
        # session*.  That silently closes the pool for every other cached client
        # too, and since this factory is the only place that recreates it, every
        # subsequent call fails with "Cannot send a request, as the client has
        # been closed." until the process restarts.  Whenever we replace a closed
        # session we MUST also drop LiteLLM's now-stale cached clients so they are
        # rebuilt around the fresh session on the next call.
        if recreating:
            _flush_litellm_client_cache()
        logger.info(
            "Shared HTTP connection pool %s "
            "(max_conn=%d, keepalive=%d, keepalive_expiry=%ss)",
            "recreated after close" if recreating else "initialised",
            max_conn,
            max_keepalive,
            keepalive_expiry,
        )
    return _SHARED_HTTP_CLIENT


def _flush_litellm_client_cache() -> None:
    """Drop LiteLLM's in-memory cache of provider SDK clients.

    These cached ``AsyncOpenAI`` clients hold a reference to the previous (now
    closed) shared httpx session; flushing forces LiteLLM to rebuild them around
    the freshly created session.  Best-effort — guarded so a LiteLLM internal
    API change can never break LLM calls.
    """
    try:
        # ``litellm`` is treated as untyped, but the cache object carries a real
        # annotation so its ``flush_cache`` reads as an untyped call under strict
        # mypy; route the access through ``Any`` to keep the call type-clean.
        cache: Any = litellm.in_memory_llm_clients_cache
        cache.flush_cache()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not flush LiteLLM client cache: %s", exc)


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


# Anthropic model families that use the *adaptive* thinking protocol
# (``thinking={"type": "adaptive"}`` + ``output_config.effort``) instead of the
# legacy ``thinking={"type": "enabled", "budget_tokens": N}`` form.  The legacy
# form is deprecated on Opus 4.6 / Sonnet 4.6 and **rejected with a 400** on
# Opus 4.7, Opus 4.8, Fable 5, and Mythos 5.  LiteLLM only auto-maps
# ``reasoning_effort`` → adaptive for the 4.6 models, so for everything newer we
# must emit the adaptive protocol ourselves (see ``_build_request_kwargs``).
_ANTHROPIC_ADAPTIVE_THINKING_FRAGMENTS = (
    "opus-4-6",
    "opus-4.6",
    "opus_4_6",
    "opus-4-7",
    "opus-4.7",
    "opus_4_7",
    "opus-4-8",
    "opus-4.8",
    "opus_4_8",
    "sonnet-4-6",
    "sonnet-4.6",
    "sonnet_4_6",
    "fable-5",
    "fable_5",
    "mythos-5",
    "mythos_5",
)

# Anthropic models that reject sampling parameters (``temperature`` / ``top_p`` /
# ``top_k``) outright with a 400.  Opus 4.6 / Sonnet 4.6 still accept them, so
# they are deliberately absent here.
_ANTHROPIC_NO_SAMPLING_FRAGMENTS = (
    "opus-4-7",
    "opus-4.7",
    "opus_4_7",
    "opus-4-8",
    "opus-4.8",
    "opus_4_8",
    "fable-5",
    "fable_5",
    "mythos-5",
    "mythos_5",
)


# Endpoint capability cache for the /v1/responses bridge, keyed by
# (api_base, litellm_model).  ``True`` = the endpoint accepted a Responses
# request; ``False`` = it rejected one (404/400) and we stay on chat
# completions; absent = untried.  Module-level (not per-instance) so the
# one probe round-trip is paid once per endpoint+model per process, no
# matter how many LLM instances are constructed.
_RESPONSES_BRIDGE_SUPPORT: dict[tuple[str | None, str], bool] = {}

# Same shape, for the *native* /v1/responses path (``litellm.aresponses``).
# Kept separate from the bridge cache: an endpoint can accept LiteLLM's
# bridge translation and still choke on a request we build ourselves, and
# vice versa, so one verdict must never stand in for the other.
_RESPONSES_NATIVE_SUPPORT: dict[tuple[str | None, str], bool] = {}

# Which protocol GPT-5.x uses, via ``FIM_GPT5_RESPONSES_MODE``:
#   native — talk /v1/responses directly and replay reasoning items (default)
#   bridge — LiteLLM's chat→responses translation (pre-native behaviour)
#   off    — plain chat completions, reasoning disabled during tool use
_GPT5_MODE_NATIVE = "native"
_GPT5_MODE_BRIDGE = "bridge"
_GPT5_MODE_OFF = "off"


def _gpt5_responses_mode() -> str:
    """Read the GPT-5.x protocol switch, defaulting to ``native``.

    Read per call rather than cached at import so an operator can flip the
    variable and restart a worker without a code change, and so tests can
    monkeypatch it.  An unrecognised value falls back to ``native`` with a
    warning instead of failing the request.
    """
    mode = (os.getenv("FIM_GPT5_RESPONSES_MODE") or _GPT5_MODE_NATIVE).strip().lower()
    if mode not in (_GPT5_MODE_NATIVE, _GPT5_MODE_BRIDGE, _GPT5_MODE_OFF):
        logger.warning(
            "Unrecognised FIM_GPT5_RESPONSES_MODE=%r; using %r",
            mode,
            _GPT5_MODE_NATIVE,
        )
        return _GPT5_MODE_NATIVE
    return mode

# Errors that mean "this endpoint has no usable /v1/responses route" and
# trigger the silent fallback to chat completions.  Bound at import time so
# tests that patch the ``litellm`` module wholesale don't turn the except
# clause into a MagicMock.
_BRIDGE_FALLBACK_ERRORS: tuple[type[Exception], ...] = (
    NotFoundError,
    BadRequestError,
)


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
            base_url,
            model,
            provider,
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

        # Guardrail: a Claude adaptive-thinking model routed through the generic
        # OpenAI-compatible path can't receive adaptive thinking.  The OpenAI
        # Chat Completions schema has no thinking/output_config concept, so the
        # reasoning parameter is silently dropped (litellm.drop_params=True)
        # before the request leaves us — thinking never turns on, with no error.
        # Only the anthropic/ native route ("/v1/messages") supports it.
        if (
            self._litellm_model.startswith("openai/")
            and (self._reasoning_effort or self._reasoning_budget_tokens)
            and any(
                f in f"{self._model} {self._litellm_model}".lower()
                for f in _ANTHROPIC_ADAPTIVE_THINKING_FRAGMENTS
            )
        ):
            logger.warning(
                "Model %r uses Anthropic adaptive thinking but is routed through "
                "the generic OpenAI-compatible path (litellm_model=%r); extended "
                "thinking will NOT take effect — the reasoning parameter is dropped "
                "before reaching the provider. Set provider='anthropic' (or use an "
                "Anthropic base_url) to route natively and enable adaptive thinking.",
                self._model,
                self._litellm_model,
            )

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
        # Collapse any consecutive same-role runs — notably orphan user
        # messages left behind by stopped-then-retried turns — before the
        # message list hits the provider. Anthropic rejects such sequences
        # outright; other providers silently degrade.
        messages = normalize_alternating_messages(messages)
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

        if self._should_use_native_responses(reasoning_effort=reasoning_effort):
            native = await self._native_responses_chat(
                messages,
                tools=tools,
                tool_choice=tool_choice,
                max_tokens=max_tokens,
                response_format=response_format,
                reasoning_effort=reasoning_effort,
            )
            if native is not None:
                if self._rate_limiter is not None and native.usage.get("total_tokens"):
                    await self._rate_limiter.report_usage(native.usage["total_tokens"])
                return native

        response = await self._dispatch_acompletion(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            stream=False,
            reasoning_effort=reasoning_effort,
        )

        choice = response.choices[0]
        assistant_msg = self._parse_choice_message(choice)

        # Same truncation guard as the streaming path: a call whose arguments
        # were cut in half by the output limit must not be dispatched.
        finish_reason = getattr(choice, "finish_reason", None)
        truncated_tool_call = False
        if finish_reason == "length" and assistant_msg.tool_calls:
            raw_calls = getattr(choice.message, "tool_calls", None) or []
            if any(
                _incomplete_arguments(getattr(tc.function, "arguments", None))
                for tc in raw_calls
            ):
                logger.warning(
                    "Tool call truncated by the output limit (%s), "
                    "dropping %d partial call(s)",
                    self._model,
                    len(assistant_msg.tool_calls),
                )
                assistant_msg.tool_calls = None
                truncated_tool_call = True

        usage: dict[str, int] = {}
        if response.usage is not None:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
            _merge_cache_usage(usage, response.usage)

        # Report actual token usage back to the rate limiter.
        if self._rate_limiter is not None and usage.get("total_tokens"):
            await self._rate_limiter.report_usage(usage["total_tokens"])

        return LLMResult(
            message=assistant_msg,
            usage=usage,
            finish_reason=finish_reason,
            truncated_tool_call=truncated_tool_call,
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
        """Send a streaming chat completion request.

        The entire stream is retried from scratch on transient failures.
        Rate limiting is applied before each attempt.

        Yields ``StreamChunk`` instances as they arrive.  Tool-call deltas are
        accumulated and emitted as complete ``ToolCallRequest`` objects once the
        stream signals ``finish_reason`` of ``"tool_calls"`` or ``"stop"``.
        """
        # Same rationale as in ``chat()`` — normalize before retry so each
        # attempt sees a protocol-legal message sequence.
        messages = normalize_alternating_messages(messages)
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

        if self._should_use_native_responses():
            native = await self._native_responses_stream(
                messages,
                tools=tools,
                tool_choice=tool_choice,
                max_tokens=max_tokens,
            )
            if native is not None:
                return native

        stream = await self._dispatch_acompletion(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )

        async def _iterate() -> AsyncIterator[StreamChunk]:
            # Accumulate partial tool calls keyed by their index in the array.
            pending_tool_calls: dict[int, _PartialToolCall] = {}
            # Remap table: raw provider index → reallocated slot when a
            # collision is detected (e.g. provider reuses index=0).
            index_remap: dict[int, int] = {}
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
                    _merge_cache_usage(stream_usage, chunk.usage)

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta
                finish_reason = chunk.choices[0].finish_reason

                # --- content / reasoning fragments ---
                delta_content = getattr(delta, "content", None)
                delta_reasoning = getattr(delta, "reasoning_content", None)
                # Extract any thinking-block signature that arrived on
                # this delta (Anthropic streams it once per block).
                delta_signature = OpenAICompatibleLLM._extract_thinking_signature(
                    delta,
                )

                # Re-route <think>...</think> from content to reasoning.
                if delta_content:
                    parsed_content, parsed_reasoning = think_parser.feed(
                        delta_content,
                    )
                    delta_content = parsed_content or None
                    if parsed_reasoning:
                        delta_reasoning = ((delta_reasoning or "") + parsed_reasoning) or None

                # --- tool-call fragments ---
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        raw_idx = tc_delta.index
                        # Redirect through any active remap (handles
                        # providers that reuse index=0 for every call).
                        idx = index_remap.get(raw_idx, raw_idx)

                        tc_id = getattr(tc_delta, "id", None) or ""
                        tc_name = ""
                        if tc_delta.function:
                            tc_name = tc_delta.function.name or ""

                        # Boundary detection: if the slot already exists
                        # and the incoming delta carries a *different* id
                        # or name, a new tool call has started on the
                        # same raw index.
                        if idx in pending_tool_calls:
                            existing = pending_tool_calls[idx]
                            is_boundary = False
                            if tc_id and existing.id and tc_id != existing.id:
                                is_boundary = True
                            if tc_name and existing.name and tc_name != existing.name:
                                is_boundary = True
                            if is_boundary:
                                new_idx = max(pending_tool_calls.keys()) + 1
                                pending_tool_calls[new_idx] = _PartialToolCall()
                                index_remap[raw_idx] = new_idx
                                idx = new_idx

                        if idx not in pending_tool_calls:
                            pending_tool_calls[idx] = _PartialToolCall()
                        partial = pending_tool_calls[idx]
                        if tc_id:
                            partial.id = tc_id
                        if tc_delta.function:
                            if tc_name:
                                partial.name = tc_name
                            if tc_delta.function.arguments:
                                partial.arguments += tc_delta.function.arguments

                # Emit a chunk for every delta that carries content,
                # reasoning, or a thinking-block signature.
                if delta_content or delta_reasoning or delta_signature:
                    yield StreamChunk(
                        delta_content=delta_content,
                        delta_reasoning=delta_reasoning,
                        finish_reason=finish_reason,
                        signature=delta_signature,
                    )

                # When the stream finishes, flush any accumulated tool calls.
                # Every terminal reason must be forwarded, including "length"
                # and provider-specific ones — swallowing it here strands the
                # caller with a truncated turn it cannot detect.
                if finish_reason and pending_tool_calls:
                    truncated = finish_reason == "length" and any(
                        _incomplete_arguments(p.arguments)
                        for p in pending_tool_calls.values()
                    )
                    if truncated:
                        # The output limit landed inside the arguments, so the
                        # JSON is half-written.  Dispatching it would run a
                        # tool with garbage input; report the truncation and
                        # let the caller ask the model to retry smaller.
                        logger.warning(
                            "Tool call truncated by the output limit (%s), "
                            "dropping %d partial call(s)",
                            self._model,
                            len(pending_tool_calls),
                        )
                        yield StreamChunk(
                            finish_reason=finish_reason,
                            usage=stream_usage,
                            truncated_tool_call=True,
                        )
                    else:
                        completed = self._flush_tool_calls(pending_tool_calls)
                        yield StreamChunk(
                            finish_reason=finish_reason,
                            tool_calls=completed,
                            usage=stream_usage,
                        )
                    usage_yielded = stream_usage is not None
                    pending_tool_calls.clear()
                    index_remap.clear()
                elif finish_reason:
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

        ``thinking`` is True only for models that emit signed
        extended-thinking blocks — currently the Claude 4.x family.  Other
        reasoning-capable models (DeepSeek R1, GPT-5.x) surface CoT via
        ``reasoning_content`` without the Anthropic signature contract,
        so they don't need the thinking-block replay logic.
        """
        return {
            "tool_call": True,
            "tool_choice": self._tool_choice_enabled,
            "json_mode": self._json_mode_enabled,
            "vision": True,
            "streaming": True,
            "thinking": self._supports_thinking_blocks(),
        }

    def _supports_thinking_blocks(self) -> bool:
        """Return True when the model emits any reasoning / CoT content.

        The ``thinking`` capability drives two orthogonal behaviours:

        1. Streaming thinking tokens to the UI in real time (the caller
           wires up ``on_thinking_delta`` only when this flag is set).
        2. Capturing the signed ``signature`` so replay on subsequent
           turns stays valid (Anthropic-specific).

        (2) only applies to Claude 4.x, but (1) applies to any model
        that emits ``reasoning_content`` / ``<think>`` deltas — DeepSeek
        R1, Anthropic extended-thinking, OpenAI o-series, GPT-5.x with
        reasoning_effort, Gemini thinking, etc.  So we return True for
        the broad family: any Anthropic model, any model with a
        configured reasoning effort, or any known reasoning-first model
        by name.  Non-reasoning models return False and skip the
        streaming subscription entirely.
        """
        model = (self._model or "").lower()
        litellm_model = (self._litellm_model or "").lower()
        # Anthropic always supports extended thinking blocks when enabled.
        if litellm_model.startswith("anthropic/"):
            return True
        # Configured reasoning effort implies the user wants CoT surfaced.
        if self._reasoning_effort or self._reasoning_budget_tokens:
            return True
        # Known reasoning-first model name patterns.
        reasoning_tags = (
            "claude-opus-4",
            "claude-sonnet-4",
            "claude-haiku-4",
            "deepseek-r1",
            "deepseek-reasoner",
            "qwq",
            "o1",
            "o3",
            "o4",
            "gpt-5",
            "gemini-2.0-flash-thinking",
            "gemini-2.5-flash-thinking",
        )
        return any(tag in model for tag in reasoning_tags)

    def _anthropic_model_text(self) -> str | None:
        """Lower-cased model identifier, only for Anthropic-routed models.

        Returns ``None`` when the model is *not* routed natively through
        LiteLLM's Anthropic transformer (``anthropic/`` prefix).  The
        adaptive-thinking / sampling-param rules below are properties of the
        Anthropic ``/v1/messages`` API; a Claude model reached through a
        generic ``openai/`` proxy is the relay's translation problem, not
        ours, so we leave those requests untouched.
        """
        if not (self._litellm_model or "").startswith("anthropic/"):
            return None
        return f"{self._model} {self._litellm_model}".lower()

    def _uses_adaptive_thinking(self) -> bool:
        """True for Anthropic models on the adaptive-thinking protocol.

        These take ``thinking={"type": "adaptive"}`` + ``output_config.effort``;
        the legacy ``budget_tokens`` form 400s on 4.7/4.8/Fable.
        """
        text = self._anthropic_model_text()
        return bool(
            text and any(f in text for f in _ANTHROPIC_ADAPTIVE_THINKING_FRAGMENTS)
        )

    def _rejects_sampling_params(self) -> bool:
        """True for Anthropic models that 400 on temperature/top_p/top_k."""
        text = self._anthropic_model_text()
        return bool(
            text and any(f in text for f in _ANTHROPIC_NO_SAMPLING_FRAGMENTS)
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Native /v1/responses path (GPT-5.x)
    # ------------------------------------------------------------------

    def _should_use_native_responses(
        self,
        *,
        reasoning_effort: str | object | None = _REASONING_INHERIT,
    ) -> bool:
        """Decide whether this call talks /v1/responses directly.

        Four conditions, all narrow on purpose.  The native path exists to
        keep GPT-5.x reasoning alive across tool-call rounds, so anything
        that would not benefit stays on the protocol it already works on:

        1. An ``openai/``-routed GPT-5.x model.  Other families gain
           nothing and can be actively harmed by relay Responses shims
           (see :meth:`_dispatch_acompletion`).
        2. The endpoint has not already told us it has no /v1/responses
           route.
        3. ``FIM_GPT5_RESPONSES_MODE`` is ``native``.
        4. The caller did not explicitly suppress reasoning.  A call that
           passes ``reasoning_effort=None`` wants no thinking at all
           (``structured_llm_call`` and the finish-signal probes), so
           there is no reasoning state to preserve and the well-trodden
           completions path is the safer choice.
        """
        if not self._litellm_model.startswith("openai/"):
            return False
        if not self._model.lower().startswith("gpt-5"):
            return False
        if _gpt5_responses_mode() != _GPT5_MODE_NATIVE:
            return False
        if reasoning_effort is not _REASONING_INHERIT and reasoning_effort is None:
            return False
        key = (self._api_base, self._litellm_model)
        return _RESPONSES_NATIVE_SUPPORT.get(key) is not False

    def _build_responses_kwargs(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None,
        tool_choice: str | dict[str, Any] | None,
        max_tokens: int | None,
        response_format: dict[str, Any] | None = None,
        stream: bool = False,
        reasoning_effort: str | object | None = _REASONING_INHERIT,
    ) -> dict[str, Any]:
        """Build the keyword arguments for ``litellm.aresponses()``.

        Two settings are what make replay work at all.  ``store=False``
        keeps the conversation stateless on OpenAI's side, and
        ``include=["reasoning.encrypted_content"]`` asks for the encrypted
        payload to be handed back to us so we can replay it ourselves on
        the next request.  Without the include, reasoning items arrive
        empty and every tool round starts thinking from scratch.

        ``temperature`` is deliberately absent: GPT-5 reasoning models
        reject it outright.
        """
        effective_reasoning = (
            self._reasoning_effort
            if reasoning_effort is _REASONING_INHERIT
            else reasoning_effort
        )
        kwargs: dict[str, Any] = {
            # The bare model name plus an explicit provider — the
            # ``openai/`` prefix belongs to LiteLLM's completions router
            # and is not understood here.
            "model": self._model,
            "custom_llm_provider": "openai",
            "input": build_responses_input(messages),
            "store": False,
            "include": ["reasoning.encrypted_content"],
            "stream": stream,
            "api_key": self._api_key,
        }
        if self._api_base is not None:
            kwargs["api_base"] = self._api_base
        token_limit = max_tokens if max_tokens is not None else self._default_max_tokens
        if token_limit:
            kwargs["max_output_tokens"] = token_limit
        converted_tools = convert_tools(tools)
        if converted_tools:
            kwargs["tools"] = converted_tools
            converted_choice = convert_tool_choice(tool_choice)
            if converted_choice is not None:
                kwargs["tool_choice"] = converted_choice
        text_param = convert_response_format(response_format)
        if text_param is not None:
            kwargs["text"] = text_param
        reasoning: dict[str, Any] = {"summary": "auto"}
        if isinstance(effective_reasoning, str):
            reasoning["effort"] = effective_reasoning
        kwargs["reasoning"] = reasoning
        return kwargs

    def _remember_native_failure(self, exc: Exception) -> None:
        """Record a native-path failure and decide whether to blacklist.

        A ``NotFoundError`` is structural: the endpoint has no
        /v1/responses route and never will within this process, so cache
        the verdict and stop paying the probe.

        A ``BadRequestError`` is *not* cached.  The likeliest cause is one
        malformed request, typically a stale reasoning item replayed from
        an older conversation, and caching that would blacklist the native
        path permanently over a single bad turn.  This call falls back;
        the next one tries again.
        """
        key = (self._api_base, self._litellm_model)
        if isinstance(exc, NotFoundError):
            _RESPONSES_NATIVE_SUPPORT[key] = False
            logger.info(
                "Native Responses API unavailable for %s (%s); "
                "falling back to chat completions",
                self._model,
                type(exc).__name__,
            )
        else:
            logger.warning(
                "Native Responses request rejected for %s (%s); "
                "falling back to chat completions for this call only",
                self._model,
                type(exc).__name__,
            )

    async def _native_responses_chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None,
        tool_choice: str | dict[str, Any] | None,
        max_tokens: int | None,
        response_format: dict[str, Any] | None,
        reasoning_effort: str | object | None,
    ) -> LLMResult | None:
        """Run one non-streaming /v1/responses call.

        Returns ``None`` when the endpoint refused in a way that means
        "use chat completions instead"; any other error propagates so the
        retry layer can treat it as the transient failure it probably is.
        """
        kwargs = self._build_responses_kwargs(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            max_tokens=max_tokens,
            response_format=response_format,
            reasoning_effort=reasoning_effort,
        )
        _get_shared_http_client()
        try:
            response = await litellm.aresponses(**kwargs)
        except (NotFoundError, BadRequestError) as exc:
            self._remember_native_failure(exc)
            return None
        _RESPONSES_NATIVE_SUPPORT[(self._api_base, self._litellm_model)] = True
        result = parse_response(response)
        if result.finish_reason == "length" and result.message.tool_calls:
            # Same guard as the completions path: arguments cut in half by
            # the output limit must never be dispatched.
            logger.warning(
                "Tool call truncated by the output limit (%s), dropping %d call(s)",
                self._model,
                len(result.message.tool_calls),
            )
            result.message.tool_calls = None
            result.truncated_tool_call = True
        return result

    async def _native_responses_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None,
        tool_choice: str | dict[str, Any] | None,
        max_tokens: int | None,
    ) -> AsyncIterator[StreamChunk] | None:
        """Open one streaming /v1/responses call.

        Only failures raised while opening the stream can be recovered
        from here.  Once events start flowing the caller is committed, the
        same as on the completions path.
        """
        kwargs = self._build_responses_kwargs(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            max_tokens=max_tokens,
            stream=True,
        )
        _get_shared_http_client()
        try:
            stream = await litellm.aresponses(**kwargs)
        except (NotFoundError, BadRequestError) as exc:
            self._remember_native_failure(exc)
            return None
        _RESPONSES_NATIVE_SUPPORT[(self._api_base, self._litellm_model)] = True
        return stream_to_chunks(stream)

    async def _dispatch_acompletion(self, **build_args: Any) -> Any:
        """Call ``litellm.acompletion``, optionally via the Responses bridge.

        The bridge is LiteLLM's chat-completions → /v1/responses
        translation.  It is now a rollback path only, reached when
        ``FIM_GPT5_RESPONSES_MODE=bridge``: the default ``native`` mode
        talks the Responses protocol directly (see
        :meth:`_native_responses_chat`), which the bridge cannot do because
        its translation drops the reasoning items that let GPT-5.x carry
        its chain of thought across tool rounds.

        The bridge stays **benefit-gated** to GPT-5.x for the same reason
        the native path is.  Other models on openai/-compatible routes
        (e.g. Claude behind a proxy) gain nothing, and relays advertise
        /v1/responses for them through buffering shims that accept the
        request but hold the whole answer before replaying it (observed on
        Uniapi + Claude: ~4 min to first token, and in a second
        reproduction a call that returned cleanly but produced no tool
        calls at all).  Nothing errors, so an error-triggered fallback
        never fires and the user just sees a hang.

        Endpoints without /v1/responses reject the first probe with a
        404/400; we silently fall back to chat completions and remember the
        verdict in ``_RESPONSES_BRIDGE_SUPPORT``, so the probe costs one
        round-trip per endpoint+model per process.

        Native provider routes (anthropic/ etc.) never enter the bridge —
        their protocol (adaptive thinking, ...) is handled in
        ``_build_request_kwargs``.
        """
        key = (self._api_base, self._litellm_model)
        if (
            self._litellm_model.startswith("openai/")
            and self._model.lower().startswith("gpt-5")
            and _gpt5_responses_mode() == _GPT5_MODE_BRIDGE
            and _RESPONSES_BRIDGE_SUPPORT.get(key) is not False
        ):
            kwargs = self._build_request_kwargs(via_responses=True, **build_args)
            # Re-validate the shared pool before every attempt: if an
            # idle-TTL eviction closed it, recreate it (and flush LiteLLM's
            # stale clients) so this call uses a live session.
            _get_shared_http_client()
            try:
                result = await litellm.acompletion(**kwargs)
            except _BRIDGE_FALLBACK_ERRORS as exc:
                _RESPONSES_BRIDGE_SUPPORT[key] = False
                logger.info(
                    "Responses API unavailable for %s (%s); "
                    "falling back to chat completions",
                    self._model,
                    type(exc).__name__,
                )
            else:
                _RESPONSES_BRIDGE_SUPPORT[key] = True
                return result
        kwargs = self._build_request_kwargs(via_responses=False, **build_args)
        _get_shared_http_client()
        return await litellm.acompletion(**kwargs)

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
        via_responses: bool = False,
    ) -> dict[str, Any]:
        """Build the keyword arguments dict for ``litellm.acompletion()``.

        Args:
            reasoning_effort: Per-call override.  ``_REASONING_INHERIT``
                (default) falls back to the instance-level setting;
                ``None`` suppresses reasoning; a string overrides the level.
            via_responses: Route through LiteLLM's chat-completions →
                /v1/responses bridge (``responses/`` model prefix).  On the
                Responses API tools and reasoning may be enabled together,
                so the GPT-5.x reasoning_effort="none" patch is skipped.
        """
        effective_temperature = (
            temperature if temperature is not None else self._default_temperature
        )
        token_limit = max_tokens if max_tokens is not None else self._default_max_tokens

        # Provider-aware reasoning replay policy — ensures
        # ``reasoning_content`` + ``signature`` are dropped from history
        # messages for every provider except Anthropic.  Without this
        # gate, DeepSeek R1 / Qwen QwQ / Gemini thinking / o-series
        # receive replayed reasoning they never asked for, which
        # invalidates their prefix cache and may be rejected outright.
        # This is the single centralised enforcement point — do not
        # replicate the policy decision elsewhere.
        policy = reasoning_replay_policy(self.model_id)
        litellm_model = self._litellm_model
        if via_responses:
            # ``openai/responses/<model>`` triggers LiteLLM's bridge: the
            # request goes to /v1/responses while the response (including
            # streaming chunks and tool calls) keeps the chat-completions
            # shape, so everything downstream stays unchanged.
            litellm_model = "openai/responses/" + litellm_model.removeprefix("openai/")
        kwargs: dict[str, Any] = {
            "model": litellm_model,
            "messages": [m.to_openai_dict(replay_policy=policy) for m in messages],
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

        # Strict Anthropic models (Opus 4.7/4.8, Fable 5, Mythos 5) reject
        # temperature/top_p/top_k outright (400) — drop the temperature we
        # always set above, whether or not thinking is enabled.
        if self._rejects_sampling_params():
            kwargs.pop("temperature", None)

        # Resolve effective reasoning effort: per-call override > instance default.
        effective_reasoning = (
            self._reasoning_effort if reasoning_effort is _REASONING_INHERIT else reasoning_effort
        )
        if tools and not via_responses and self._model.lower().startswith("gpt-5"):
            # GPT-5.x /v1/chat/completions rejects function tools combined
            # with reasoning, and OpenAI requires an explicit
            # reasoning_effort="none" — merely omitting the field is not
            # equivalent (the upstream default is not "none", and LiteLLM
            # may inject one).  Tools win over reasoning on this path; the
            # Responses bridge above is what allows both at once.
            kwargs["reasoning_effort"] = "none"
            if effective_reasoning:
                logger.debug(
                    "Forcing reasoning_effort='none' for %s "
                    "(tools + reasoning unsupported in chat completions)",
                    self._model,
                )
        elif effective_reasoning:
            if self._uses_adaptive_thinking():
                # Opus 4.6+/Sonnet 4.6/Fable/Mythos use the adaptive-thinking
                # protocol.  The legacy thinking={type:"enabled", budget_tokens}
                # form is deprecated on 4.6 and returns a 400 on 4.7/4.8/Fable,
                # so emit adaptive directly instead of relying on LiteLLM's
                # reasoning_effort mapping (which only special-cases 4.6).
                kwargs["thinking"] = {"type": "adaptive"}
                if isinstance(effective_reasoning, str):
                    # output_config.effort: low | medium | high (LiteLLM passes
                    # it through to the Anthropic /v1/messages body verbatim).
                    kwargs["output_config"] = {"effort": effective_reasoning}
                # Adaptive-thinking models reject (4.7/4.8/Fable) or don't need
                # (4.6 defaults to 1.0) an explicit temperature — drop it.
                kwargs.pop("temperature", None)
            elif self._reasoning_budget_tokens and self._litellm_model.startswith("anthropic/"):
                # Legacy Anthropic models (Opus 4.5 / Sonnet 4.5 / Haiku 4.5):
                # explicit budget override — pass thinking directly, skip
                # reasoning_effort to avoid LiteLLM's auto-mapping conflict.
                kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": self._reasoning_budget_tokens,
                }
                # Bedrock rejects temperature != 1.0 when thinking is enabled
                kwargs["temperature"] = 1.0
            else:
                # Let LiteLLM handle the translation for each provider:
                #   - Anthropic (legacy): reasoning_effort → thinking (auto budget)
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
        reasoning_content = getattr(msg, "reasoning_content", None) or getattr(
            msg, "reasoning", None
        )
        # Guard against the field being a non-string (e.g. dict from some proxies).
        if reasoning_content and not isinstance(reasoning_content, str):
            reasoning_content = None
        signature = OpenAICompatibleLLM._extract_thinking_signature(msg)
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
                    reasoning_content + "\n" + extracted if reasoning_content else extracted
                )
        return ChatMessage(
            role=msg.role,
            content=content,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
            signature=signature,
        )

    @staticmethod
    def _extract_thinking_signature(source: Any) -> str | None:
        """Best-effort extraction of the Anthropic thinking-block signature.

        LiteLLM normalises Anthropic's native ``thinking`` content block
        into a few different shapes depending on version and transport:

        * ``message.thinking_blocks = [{"type": "thinking", "thinking":
          "...", "signature": "..."}, ...]`` — typical non-streaming shape.
        * ``message.signature`` / ``message.thinking_signature`` — some
          proxies flatten the field onto the message root.
        * ``delta.thinking_blocks[*].signature`` — streaming shape.

        Returns the signature string when found, ``None`` otherwise.  The
        value is opaque and MUST be echoed unchanged on subsequent turns
        for the Anthropic API to accept the history.
        """
        # Flat attributes — check both likely names.
        for attr in ("signature", "thinking_signature"):
            val = getattr(source, attr, None)
            if isinstance(val, str) and val:
                return val
        # Dict-style access (LiteLLM sometimes wraps responses in dicts).
        if isinstance(source, dict):
            for key in ("signature", "thinking_signature"):
                val = source.get(key)
                if isinstance(val, str) and val:
                    return val
            blocks = source.get("thinking_blocks")
        else:
            blocks = getattr(source, "thinking_blocks", None)
        # thinking_blocks is typically a list[dict]; walk it and prefer the
        # last non-empty signature (most recent thinking block).
        if isinstance(blocks, list):
            for block in reversed(blocks):
                sig: Any = None
                if isinstance(block, dict):
                    sig = block.get("signature") or block.get("thinking_signature")
                else:
                    sig = getattr(block, "signature", None) or getattr(
                        block,
                        "thinking_signature",
                        None,
                    )
                if isinstance(sig, str) and sig:
                    return sig
        return None

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
                self._buf = stripped[len(self._OPEN) :]
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
                after = self._buf[close_idx + len(self._CLOSE) :]
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

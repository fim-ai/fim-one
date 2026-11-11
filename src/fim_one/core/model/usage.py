"""Token usage tracking and aggregation.

Provides ``UsageSummary`` for reporting and ``UsageTracker`` for
thread-safe (async-safe) accumulation of token consumption across
multiple LLM calls.

It also holds the meter the usage ledger is fed from. A model client
reports what each call consumed with :func:`report_usage`; the report goes
to the meter the entry point opened for the turn, run or request being
served (:data:`current_meter`), which hands the totals to
``core.usage.meter`` to be written. The client neither knows nor decides
whose usage it is. Everything here is in memory, and the dependency points
one way: the ledger package imports this one.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import math
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "ModelUsage",
    "StreamProgress",
    "TextTokenCounter",
    "UnmeteredUsageError",
    "UsageMeter",
    "UsageReporter",
    "UsageSummary",
    "UsageTracker",
    "current_meter",
    "estimate_prompt_tokens",
    "estimate_tokens",
    "estimated_usage",
    "report_usage",
    "set_strict_metering",
    "unattributed_tokens",
]


@dataclass
class UsageSummary:
    """Aggregated token usage summary.

    Attributes:
        prompt_tokens: Total prompt (input) tokens consumed.
        completion_tokens: Total completion (output) tokens consumed.
        total_tokens: Sum of prompt and completion tokens.
        llm_calls: Number of LLM calls that contributed to this summary.
        cache_read_input_tokens: Total prompt tokens served from the
            provider-side cache (Anthropic ``cache_read_input_tokens``).
            ``0`` when the model/provider does not report cache usage.
        cache_creation_input_tokens: Total prompt tokens written to the
            provider-side cache on this conversation.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    llm_calls: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def __add__(self, other: UsageSummary) -> UsageSummary:
        """Merge two summaries by adding their counters."""
        return UsageSummary(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            llm_calls=self.llm_calls + other.llm_calls,
            cache_read_input_tokens=(self.cache_read_input_tokens + other.cache_read_input_tokens),
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens
            ),
        )

    def __iadd__(self, other: UsageSummary) -> UsageSummary:
        """In-place merge."""
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
        self.llm_calls += other.llm_calls
        self.cache_read_input_tokens += other.cache_read_input_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens
        return self


@dataclass
class _UsageRecord:
    """A single recorded LLM call's token usage."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    model: str | None = None


class UsageTracker:
    """Thread-safe (async-safe) accumulator for token usage.

    Records per-call usage data and produces an aggregated
    ``UsageSummary`` on demand.

    Example::

        tracker = UsageTracker()
        tracker.record({"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
        summary = tracker.get_summary()
    """

    def __init__(self) -> None:
        self._records: list[_UsageRecord] = []
        self._lock = asyncio.Lock()

    async def record(
        self,
        usage: dict[str, int],
        *,
        model: str | None = None,
    ) -> None:
        """Record token usage from a single LLM call.

        Args:
            usage: Dictionary with ``prompt_tokens``, ``completion_tokens``,
                and ``total_tokens`` keys (as returned by ``LLMResult.usage``).
            model: Optional model name for future multi-model tracking.
        """
        if not usage:
            return
        record = _UsageRecord(
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
            cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0),
            model=model,
        )
        async with self._lock:
            self._records.append(record)

    def get_summary(self) -> UsageSummary:
        """Compute an aggregated summary of all recorded usage.

        Returns:
            A ``UsageSummary`` totalling all recorded calls.
        """
        summary = UsageSummary()
        for rec in self._records:
            summary.prompt_tokens += rec.prompt_tokens
            summary.completion_tokens += rec.completion_tokens
            summary.total_tokens += rec.total_tokens
            summary.cache_read_input_tokens += rec.cache_read_input_tokens
            summary.cache_creation_input_tokens += rec.cache_creation_input_tokens
            summary.llm_calls += 1
        return summary

    async def reset(self) -> None:
        """Clear all recorded usage data."""
        async with self._lock:
            self._records.clear()


# ---------------------------------------------------------------------------
# The meter: what a turn, a run or a request consumed, by model
# ---------------------------------------------------------------------------


@dataclass
class ModelUsage:
    """What one model consumed in a metering scope since it was last drained."""

    model: str | None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    total_tokens: int = 0
    # The provider reported no usage (a stream cut short, a provider that
    # sends none): the numbers were estimated from the text.
    estimated: bool = False
    calls: int = 0


def _count(usage: Mapping[str, Any], key: str) -> int:
    """A token count out of a usage mapping: an integer, never negative."""
    value = usage.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(int(value), 0)


class UsageMeter:
    """Accumulates the usage reported inside one metering scope.

    ``add`` is synchronous and takes a lock, so a model client can report
    from a ``finally`` block, from a child task and from a worker thread.
    ``total`` is everything reported so far; ``drain`` hands out what was
    reported since the last drain, for the ledger to store.

    A streamed call reports its count when its stream ends. Until then the
    client keeps ``in_flight`` up to date with an estimate of what the
    stream has produced so far, so that a turn can watch a quota while its
    answer is being written.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[tuple[str | None, bool], ModelUsage] = {}
        self._total = 0
        self._in_flight = 0
        # Called (outside the lock) after every ``add``; ``core.usage.meter``
        # uses it to store usage that arrives once the scope has ended.
        self.on_add: Callable[[], None] | None = None

    def add(
        self,
        model: str | None,
        usage: Mapping[str, Any],
        *,
        estimated: bool = False,
    ) -> None:
        """Count one model call's usage. A call that consumed nothing is ignored."""
        prompt = _count(usage, "prompt_tokens")
        completion = _count(usage, "completion_tokens")
        # The provider's own total when it gives one; some count reasoning
        # tokens there that are in neither part.
        total = _count(usage, "total_tokens") or (prompt + completion)
        if total <= 0:
            return
        key = (model, estimated)
        with self._lock:
            entry = self._pending.get(key)
            if entry is None:
                entry = self._pending[key] = ModelUsage(model=model, estimated=estimated)
            entry.prompt_tokens += prompt
            entry.completion_tokens += completion
            entry.cache_read_tokens += _count(usage, "cache_read_input_tokens")
            entry.cache_creation_tokens += _count(usage, "cache_creation_input_tokens")
            entry.total_tokens += total
            entry.calls += 1
            self._total += total
        hook = self.on_add
        if hook is not None:
            hook()

    def total(self) -> int:
        """Tokens reported in this scope so far, drained or not."""
        with self._lock:
            return self._total

    def stream_progress(self, tokens: int) -> None:
        """A stream in flight produced *tokens* more (estimated); negative
        when the stream ends and its real count is about to be added."""
        with self._lock:
            self._in_flight = max(self._in_flight + tokens, 0)

    def in_flight(self) -> int:
        """Estimated output tokens of the streams that have not ended yet.

        Output only: what such a call was sent is counted when it ends.
        """
        with self._lock:
            return self._in_flight

    def drain(self) -> list[ModelUsage]:
        """Take what was reported since the last drain, one item per model."""
        with self._lock:
            items = list(self._pending.values())
            self._pending = {}
        return items


# The meter of the innermost scope being served. Copied into child tasks
# and worker threads with the rest of the context, so their calls land on
# the same meter.
current_meter: contextvars.ContextVar[UsageMeter | None] = contextvars.ContextVar(
    "fim_one_usage_meter",
    default=None,
)


class UnmeteredUsageError(RuntimeError):
    """A model call reported usage with no meter open (strict metering only)."""


_strict = False
_unattributed_lock = threading.Lock()
_unattributed_tokens = 0


def set_strict_metering(strict: bool) -> None:
    """Make a report with no open meter raise.

    For tests that hold every entry point to opening a meter. Outside them
    such a report is counted (:func:`unattributed_tokens`) and logged.
    """
    global _strict
    _strict = strict


def unattributed_tokens() -> int:
    """Tokens reported in this process while no meter was open."""
    with _unattributed_lock:
        return _unattributed_tokens


def _report_to(
    meter: UsageMeter | None,
    model: str | None,
    usage: Mapping[str, Any] | None,
    estimated: bool,
) -> None:
    if not usage:
        return
    if meter is not None:
        try:
            meter.add(model, usage, estimated=estimated)
        except Exception:
            logger.error("Usage of model %s could not be metered: %r", model, dict(usage))
        return
    total = _count(usage, "total_tokens") or (
        _count(usage, "prompt_tokens") + _count(usage, "completion_tokens")
    )
    if total <= 0:
        return
    global _unattributed_tokens
    with _unattributed_lock:
        _unattributed_tokens += total
    if _strict:
        raise UnmeteredUsageError(
            f"model {model!r} consumed {total} tokens with no usage meter open: "
            "the entry point that made this call must open one (core.usage.meter)"
        )
    _warn_unattributed(model, total)


# A model call that reports with no meter open comes from an entry point
# that opens none: its usage is in nobody's ledger. Said at WARNING, at most
# once a minute so that a busy unmetered path does not flood the log; the
# running total is in every message.
_UNATTRIBUTED_WARNING_INTERVAL = 60.0
_last_unattributed_warning = 0.0


def _warn_unattributed(model: str | None, total: int) -> None:
    global _last_unattributed_warning
    now = time.monotonic()
    if now - _last_unattributed_warning < _UNATTRIBUTED_WARNING_INTERVAL:
        return
    _last_unattributed_warning = now
    logger.warning(
        "Model usage outside any usage meter: model=%s total_tokens=%d "
        "(%d tokens unattributed in this process so far). The entry point that made "
        "the call does not open a metering scope, so it is recorded against nobody.",
        model,
        total,
        unattributed_tokens(),
    )


def report_usage(
    model: str | None,
    usage: Mapping[str, Any] | None,
    *,
    estimated: bool = False,
) -> None:
    """Report what one model call consumed to the meter of the current scope.

    Called by the model clients, once per call that reached the provider.
    Never raises outside strict metering: counting usage must not fail the
    call that produced it.
    """
    _report_to(current_meter.get(), model, usage, estimated)


class UsageReporter:
    """:func:`report_usage`, bound to the scope that is current now.

    A streamed call reports when its stream ends, and a stream that is
    abandoned is closed by the event loop in a context of its own. The
    client therefore takes the reporter when it opens the stream.
    """

    def __init__(self) -> None:
        self._meter = current_meter.get()

    def __call__(
        self,
        model: str | None,
        usage: Mapping[str, Any] | None,
        *,
        estimated: bool = False,
    ) -> None:
        _report_to(self._meter, model, usage, estimated)

    def progress(self, tokens: int) -> None:
        """The stream produced *tokens* more so far (see ``UsageMeter.in_flight``)."""
        if self._meter is not None and tokens:
            self._meter.stream_progress(tokens)


# Tokens per character, by kind of character, fitted against the
# ``o200k_base`` encoding on English and Chinese prose, JSON, tool schemas
# and Python source (each within 0.8x-1.2x of the real count). Letters run
# about four to a token; digits and punctuation, which dominate JSON and
# code, about two; CJK and other non-ASCII text about one each. Whitespace
# is absorbed into the tokens around it.
_TOKENS_PER_LETTER = 0.25
_TOKENS_PER_DIGIT = 0.55
_TOKENS_PER_PUNCTUATION = 0.65
_TOKENS_PER_OTHER = 0.85
# What a message costs beyond its content (role and separators), what a
# tool call in the history costs beyond its arguments (id, name, framing),
# and a flat figure per image: about what a 1024x1024 picture costs at high
# detail. The picture's size is not known here, and its base64 data is not
# what the provider counts.
_TOKENS_PER_MESSAGE = 4
_TOKENS_PER_TOOL_CALL = 12
_TOKENS_PER_IMAGE = 1100


class TextTokenCounter:
    """A running :func:`estimate_tokens` over text that arrives in pieces."""

    __slots__ = ("_digits", "_letters", "_other", "_punctuation")

    def __init__(self) -> None:
        self._letters = self._digits = self._punctuation = self._other = 0

    def add(self, text: str | None) -> None:
        for ch in text or ():
            if ord(ch) >= 128:
                self._other += 1
            elif ch.isalpha():
                self._letters += 1
            elif ch.isdigit():
                self._digits += 1
            elif not ch.isspace():
                self._punctuation += 1

    def tokens(self) -> int:
        """The estimate so far, rounded up."""
        exact = (
            self._letters * _TOKENS_PER_LETTER
            + self._digits * _TOKENS_PER_DIGIT
            + self._punctuation * _TOKENS_PER_PUNCTUATION
            + self._other * _TOKENS_PER_OTHER
        )
        return math.ceil(exact)


class StreamProgress:
    """What a stream has had from its provider that its reader has not seen yet.

    A streamed tool-call round yields nothing until the model has written
    every argument, so a stream cut short in that round would look as if
    it had never started. The client's inner iterator notes each raw chunk
    and the argument text it holds back here, and the metering wrapper
    counts both when the stream ends without the provider's count.
    """

    __slots__ = ("_held", "started")

    def __init__(self) -> None:
        self.started = False
        self._held: dict[Any, str] = {}

    def chunk(self) -> None:
        """A raw chunk arrived from the provider."""
        self.started = True

    def tool_arguments(self, key: Any, text: str | None) -> None:
        """Argument text of tool call *key* arrived and is held back."""
        self.started = True
        if text:
            self._held[key] = self._held.get(key, "") + text

    def released(self) -> None:
        """The held tool calls were yielded; their arguments are counted from there."""
        self._held.clear()

    def held_text(self) -> str:
        return "".join(self._held.values())


def estimate_tokens(text: str | None) -> int:
    """A token estimate for *text* when the provider gave no count.

    Rounded up: an estimate stands in for a count the provider would have
    sent, for a call its caller chose to cut short.
    """
    if not text:
        return 0
    counter = TextTokenCounter()
    counter.add(text)
    return counter.tokens()


def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _message_tokens(message: Any) -> int:
    counter = TextTokenCounter()
    tokens = _TOKENS_PER_MESSAGE
    content = _field(message, "content")
    if isinstance(content, str):
        counter.add(content)
    elif isinstance(content, list):
        for part in content:
            if not isinstance(part, Mapping):
                continue
            if part.get("type") in ("image_url", "input_image", "image"):
                tokens += _TOKENS_PER_IMAGE
            else:
                counter.add(str(part.get("text") or ""))
    reasoning = _field(message, "reasoning_content")
    if isinstance(reasoning, str):
        counter.add(reasoning)
    for call in _field(message, "tool_calls") or ():
        tokens += _TOKENS_PER_TOOL_CALL
        function = _field(call, "function")
        name = _field(call, "name") or _field(function, "name")
        arguments = _field(call, "arguments")
        if arguments is None:
            arguments = _field(function, "arguments")
        counter.add(str(name or ""))
        counter.add(
            arguments if isinstance(arguments, str) else json.dumps(arguments, default=str)
        )
    return tokens + counter.tokens()


def estimate_prompt_tokens(
    messages: Any, tools: Sequence[Mapping[str, Any]] | None = None
) -> int:
    """What a call sending *messages* and *tools* is billed for its input, estimated.

    Message text, the arguments of tool calls in the history, a flat figure
    per image, and the tool schemas sent with the call. Never raises: a
    message of a shape not known here counts its overhead only.
    """
    total = 0
    for message in messages or ():
        try:
            total += _message_tokens(message)
        except Exception:
            total += _TOKENS_PER_MESSAGE
    if tools:
        with contextlib.suppress(Exception):
            total += estimate_tokens(json.dumps(list(tools), default=str))
    return total


def estimated_usage(
    messages: Any,
    output_text: str,
    tools: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, int]:
    """Usage for a call whose provider reported none, estimated from its text.

    *messages* and *tools* are what was sent, *output_text* everything that
    came back (content, reasoning, tool-call arguments).
    """
    prompt = estimate_prompt_tokens(messages, tools)
    completion = estimate_tokens(output_text)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }

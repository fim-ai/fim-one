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
import contextvars
import logging
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "ModelUsage",
    "UnmeteredUsageError",
    "UsageMeter",
    "UsageReporter",
    "UsageSummary",
    "UsageTracker",
    "current_meter",
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
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[tuple[str | None, bool], ModelUsage] = {}
        self._total = 0
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


_STRICT_ENV = "FIM_USAGE_METER_STRICT"
_strict_override: bool | None = None
_unattributed_lock = threading.Lock()
_unattributed_tokens = 0


def set_strict_metering(strict: bool | None) -> None:
    """Make a report with no open meter raise (``None``: back to the environment).

    For tests that hold every entry point to opening a meter. Outside them
    such a report is counted (:func:`unattributed_tokens`) and logged.
    """
    global _strict_override
    _strict_override = strict


def _strict() -> bool:
    if _strict_override is not None:
        return _strict_override
    return os.environ.get(_STRICT_ENV, "").strip().lower() in {"1", "true", "yes"}


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
    if _strict():
        raise UnmeteredUsageError(
            f"model {model!r} consumed {total} tokens with no usage meter open: "
            "the entry point that made this call must open one (core.usage.meter)"
        )
    logger.debug("Usage outside any meter: model=%s total_tokens=%d", model, total)


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


def estimate_tokens(text: str | None) -> int:
    """A token estimate for *text* when the provider gave no count.

    The heuristic of ``core.memory.compact.CompactUtils.estimate_tokens``:
    about four ASCII characters to a token, and one and a half of anything
    else (CJK text runs one to two tokens a character).
    """
    if not text:
        return 0
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    other_chars = len(text) - ascii_chars
    return int(ascii_chars / 4 + other_chars / 1.5 + 0.999)


def _message_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, Mapping):
        content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(part.get("text", "")) for part in content if isinstance(part, Mapping)
        )
    return ""


def estimated_usage(messages: Any, output_text: str) -> dict[str, int]:
    """Usage for a call whose provider reported none, estimated from its text.

    *messages* is what was sent, *output_text* everything that came back
    (content, reasoning, tool-call arguments).
    """
    try:
        prompt = sum(estimate_tokens(_message_text(message)) for message in messages or ())
    except Exception:
        prompt = 0
    completion = estimate_tokens(output_text)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }

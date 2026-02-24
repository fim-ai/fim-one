"""Token usage tracking and aggregation.

Provides ``UsageSummary`` for reporting and ``UsageTracker`` for
thread-safe (async-safe) accumulation of token consumption across
multiple LLM calls.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class UsageSummary:
    """Aggregated token usage summary.

    Attributes:
        prompt_tokens: Total prompt (input) tokens consumed.
        completion_tokens: Total completion (output) tokens consumed.
        total_tokens: Sum of prompt and completion tokens.
        llm_calls: Number of LLM calls that contributed to this summary.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    llm_calls: int = 0

    def __add__(self, other: UsageSummary) -> UsageSummary:
        """Merge two summaries by adding their counters."""
        return UsageSummary(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            llm_calls=self.llm_calls + other.llm_calls,
        )

    def __iadd__(self, other: UsageSummary) -> UsageSummary:
        """In-place merge."""
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
        self.llm_calls += other.llm_calls
        return self


@dataclass
class _UsageRecord:
    """A single recorded LLM call's token usage."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
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
            summary.llm_calls += 1
        return summary

    async def reset(self) -> None:
        """Clear all recorded usage data."""
        async with self._lock:
            self._records.clear()

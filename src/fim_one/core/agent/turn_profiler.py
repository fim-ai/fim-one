"""Per-turn phase profiler for the ReAct agent (CC Insights I.16).

This module provides a light-weight :class:`TurnProfiler` that records
wall-clock timings for the distinct phases of a single ReAct "turn"
(one round of LLM call + tool execution).  The profiler is purely
observational: enabling or disabling it does not change agent behaviour.

Recorded phases
---------------

Each phase is stored as an elapsed ``float`` number of seconds.  A phase
that is not exercised in a given turn records ``0.0``.

- ``memory_load`` — loading messages from ``BaseMemory`` at turn start
- ``compact`` — time spent in ``ContextGuard.check_and_compact``
- ``tool_schema_build`` — building the tool schema / selection
- ``llm_first_token`` — time to the first streaming content token
- ``llm_total`` — total LLM call wall time
- ``tool_exec`` — sum of per-tool-call latencies in this turn

Environment
-----------

Profiling is gated by the ``REACT_TURN_PROFILE_ENABLED`` environment
variable (default: ``true``).  When disabled, :func:`make_profiler`
returns a :class:`NoOpTurnProfiler` whose methods are no-ops — the
context manager still yields, so wrapped code continues to run
normally, but nothing is recorded or logged.
"""

from __future__ import annotations

__fim_license__ = "FIM-SAL-1.1"
__fim_origin__ = "https://github.com/fim-ai/fim-one"

import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    """Parse a truthy/falsey environment variable.

    Accepts ``1``, ``true``, ``yes``, ``on`` (case-insensitive) as true
    and ``0``, ``false``, ``no``, ``off`` as false.  Unset or unknown
    values fall back to *default*.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    val = raw.strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


def is_profiling_enabled() -> bool:
    """Return whether turn profiling is enabled via env var.

    Evaluated lazily on every call so tests may toggle the env var
    between runs with :func:`monkeypatch.setenv`.
    """
    return _env_bool("REACT_TURN_PROFILE_ENABLED", True)


@dataclass
class TurnProfiler:
    """Records phase-level timings for a single ReAct turn.

    Attributes:
        turn_id: The 1-indexed iteration number this profiler belongs to.
        phases: Mapping of phase name to cumulative elapsed seconds.
    """

    turn_id: int = 0
    phases: dict[str, float] = field(default_factory=dict)

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        """Measure elapsed wall time of a code block under *name*.

        Multiple invocations with the same *name* accumulate — useful
        for phases that fire in several places within a turn (e.g.
        ``micro_compact`` + ``check_and_compact`` both counted as
        ``compact``).
        """
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self.phases[name] = self.phases.get(name, 0.0) + elapsed

    def add(self, name: str, seconds: float) -> None:
        """Add *seconds* to the cumulative total for phase *name*.

        Useful when the caller measured elapsed time manually (e.g. the
        first-token latency inside a streaming loop) instead of using
        the ``phase()`` context manager.
        """
        if seconds < 0:
            seconds = 0.0
        self.phases[name] = self.phases.get(name, 0.0) + seconds

    def emit(self, conversation_id: str | None = None) -> None:
        """Emit a structured log line summarising this turn's phases.

        The log format is a single-line key=value series sorted by
        phase name, suitable for ``grep``/``awk`` pipelines and log
        aggregators.  Durations are rendered in milliseconds.
        """
        parts = " ".join(
            f"{k}={v * 1000:.0f}ms" for k, v in sorted(self.phases.items())
        )
        logger.info(
            "turn_profile conv=%s turn=%d %s",
            conversation_id or "-",
            self.turn_id,
            parts,
        )

    def to_dict(self) -> dict[str, float]:
        """Return a shallow copy of the phases dict (mutation-safe)."""
        return dict(self.phases)


class NoOpTurnProfiler(TurnProfiler):
    """A profiler that records nothing.

    Returned by :func:`make_profiler` when profiling is disabled.
    Retains the same interface so wiring sites remain unchanged.
    """

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        yield

    def add(self, name: str, seconds: float) -> None:  # noqa: D401 — override
        return None

    def emit(self, conversation_id: str | None = None) -> None:
        return None

    def to_dict(self) -> dict[str, float]:
        return {}


def make_profiler(turn_id: int) -> TurnProfiler:
    """Return an enabled or no-op profiler depending on the env gate."""
    if is_profiling_enabled():
        return TurnProfiler(turn_id=turn_id)
    return NoOpTurnProfiler(turn_id=turn_id)

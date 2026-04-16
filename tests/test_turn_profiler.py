"""Tests for the per-turn phase profiler (CC Insights I.16)."""

from __future__ import annotations

import logging
import time

import pytest

from fim_one.core.agent.turn_profiler import (
    NoOpTurnProfiler,
    TurnProfiler,
    is_profiling_enabled,
    make_profiler,
)


class TestTurnProfiler:
    """Unit tests for the TurnProfiler dataclass."""

    def test_phase_context_manager_records_elapsed(self) -> None:
        """A single phase() block records a positive elapsed duration."""
        profiler = TurnProfiler(turn_id=1)
        with profiler.phase("compact"):
            time.sleep(0.01)
        assert "compact" in profiler.phases
        # Allow generous slack for CI jitter but ensure it fired.
        assert profiler.phases["compact"] >= 0.005
        assert profiler.phases["compact"] < 1.0

    def test_phase_multiple_invocations_accumulate(self) -> None:
        """Multiple phase() invocations under the same name sum up."""
        profiler = TurnProfiler(turn_id=2)
        with profiler.phase("llm_total"):
            time.sleep(0.005)
        first = profiler.phases["llm_total"]
        with profiler.phase("llm_total"):
            time.sleep(0.005)
        second = profiler.phases["llm_total"]
        # Second total must strictly exceed the first.
        assert second > first
        # Should be roughly twice the first (not one invocation overwriting).
        assert second >= 2 * first * 0.6

    def test_add_method_accumulates(self) -> None:
        """Direct .add() calls accumulate into the same key."""
        profiler = TurnProfiler(turn_id=3)
        profiler.add("tool_exec", 0.123)
        profiler.add("tool_exec", 0.456)
        assert profiler.phases["tool_exec"] == pytest.approx(0.579, abs=1e-6)

    def test_add_negative_seconds_clamped_to_zero(self) -> None:
        """Negative inputs are clamped to avoid nonsensical deltas."""
        profiler = TurnProfiler(turn_id=4)
        profiler.add("compact", -0.5)
        assert profiler.phases["compact"] == 0.0

    def test_emit_logs_structured_line(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """emit() writes a single-line structured log at INFO level."""
        profiler = TurnProfiler(turn_id=7)
        profiler.add("compact", 0.123)
        profiler.add("llm_total", 0.456)
        profiler.add("tool_exec", 0.789)

        with caplog.at_level(logging.INFO, logger="fim_one.core.agent.turn_profiler"):
            profiler.emit(conversation_id="abc123")

        # Exactly one record from this profiler.
        records = [r for r in caplog.records if "turn_profile" in r.getMessage()]
        assert len(records) == 1
        msg = records[0].getMessage()
        assert "conv=abc123" in msg
        assert "turn=7" in msg
        assert "compact=123ms" in msg
        assert "llm_total=456ms" in msg
        assert "tool_exec=789ms" in msg

    def test_emit_without_conversation_id_uses_placeholder(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Missing conversation id renders as ``conv=-``."""
        profiler = TurnProfiler(turn_id=1)
        profiler.add("compact", 0.01)
        with caplog.at_level(logging.INFO, logger="fim_one.core.agent.turn_profiler"):
            profiler.emit(conversation_id=None)
        records = [r for r in caplog.records if "turn_profile" in r.getMessage()]
        assert len(records) == 1
        assert "conv=-" in records[0].getMessage()

    def test_to_dict_returns_copy(self) -> None:
        """to_dict() returns a shallow copy — mutating it is safe."""
        profiler = TurnProfiler(turn_id=5)
        profiler.add("compact", 0.1)
        snapshot = profiler.to_dict()
        snapshot["compact"] = 99.9
        # Original must be untouched.
        assert profiler.phases["compact"] == pytest.approx(0.1, abs=1e-6)

    def test_disabled_profiler_is_noop(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When REACT_TURN_PROFILE_ENABLED=false, make_profiler yields a no-op."""
        monkeypatch.setenv("REACT_TURN_PROFILE_ENABLED", "false")
        assert is_profiling_enabled() is False

        profiler = make_profiler(turn_id=10)
        assert isinstance(profiler, NoOpTurnProfiler)

        # The context manager still yields (wrapped code runs unchanged).
        sentinel = []
        with profiler.phase("compact"):
            sentinel.append("ran")
        assert sentinel == ["ran"]

        # Nothing was recorded.
        profiler.add("tool_exec", 1.23)
        assert profiler.phases == {}
        assert profiler.to_dict() == {}

        # emit() produces no log record.
        with caplog.at_level(logging.INFO, logger="fim_one.core.agent.turn_profiler"):
            profiler.emit(conversation_id="x")
        assert not [r for r in caplog.records if "turn_profile" in r.getMessage()]

    def test_enabled_profiler_default_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unset env var defaults to enabled."""
        monkeypatch.delenv("REACT_TURN_PROFILE_ENABLED", raising=False)
        assert is_profiling_enabled() is True
        profiler = make_profiler(turn_id=1)
        assert not isinstance(profiler, NoOpTurnProfiler)
        assert isinstance(profiler, TurnProfiler)

    def test_env_var_accepts_various_truthy_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Accept 1/true/yes/on as truthy; 0/false/no/off as falsey."""
        for val in ("1", "true", "True", "YES", "on"):
            monkeypatch.setenv("REACT_TURN_PROFILE_ENABLED", val)
            assert is_profiling_enabled() is True, val
        for val in ("0", "false", "False", "no", "off"):
            monkeypatch.setenv("REACT_TURN_PROFILE_ENABLED", val)
            assert is_profiling_enabled() is False, val

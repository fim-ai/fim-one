"""Tests for workflow scheduled trigger config CRUD and cron validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fim_one.web.schemas.workflow import (
    WorkflowScheduleResponse,
    WorkflowScheduleUpdate,
    _compute_next_run,
    _validate_cron,
)


# ---------------------------------------------------------------------------
# _validate_cron
# ---------------------------------------------------------------------------


class TestValidateCron:
    """Unit tests for the 5-field cron expression validator."""

    @pytest.mark.parametrize(
        "expr",
        [
            "* * * * *",
            "0 9 * * MON-FRI",
            "*/15 * * * *",
            "0 0 1 1 *",
            "5,10,15 * * * *",
            "0 0 1,15 * *",
            "0 */2 * * *",
            "30 4 1-7 * 1",
            "0 9 * * 0",
            "0 9 * * 7",
            "0 0 * JAN *",
            "0 0 * * MON",
            "1-30/5 * * * *",
        ],
    )
    def test_valid_expressions(self, expr: str) -> None:
        assert _validate_cron(expr) is None

    @pytest.mark.parametrize(
        "expr,expected_fragment",
        [
            ("* *", "5 fields"),
            ("* * * * * *", "5 fields"),
            ("", "5 fields"),
            ("a b c d e f g", "5 fields"),
        ],
    )
    def test_invalid_field_count(self, expr: str, expected_fragment: str) -> None:
        result = _validate_cron(expr)
        assert result is not None
        assert expected_fragment in result

    def test_empty_token_rejected(self) -> None:
        result = _validate_cron("0 9 , * *")
        assert result is not None
        assert "Empty token" in result


# ---------------------------------------------------------------------------
# WorkflowScheduleUpdate schema validation
# ---------------------------------------------------------------------------


class TestScheduleUpdateSchema:
    """Tests for the Pydantic schedule update schema."""

    def test_valid_schedule(self) -> None:
        s = WorkflowScheduleUpdate(
            cron="0 9 * * MON-FRI",
            enabled=True,
            inputs={"key": "value"},
            timezone="America/New_York",
        )
        assert s.cron == "0 9 * * MON-FRI"
        assert s.enabled is True
        assert s.inputs == {"key": "value"}
        assert s.timezone == "America/New_York"

    def test_defaults(self) -> None:
        s = WorkflowScheduleUpdate()
        assert s.cron is None
        assert s.enabled is False
        assert s.inputs is None
        assert s.timezone == "UTC"

    def test_none_cron_allowed(self) -> None:
        s = WorkflowScheduleUpdate(cron=None, enabled=False)
        assert s.cron is None

    def test_invalid_cron_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            WorkflowScheduleUpdate(cron="not a cron", enabled=True)
        errors = exc_info.value.errors()
        assert any("cron" in str(e).lower() for e in errors)

    def test_cron_with_wrong_field_count_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowScheduleUpdate(cron="* *", enabled=False)

    def test_cron_whitespace_stripped(self) -> None:
        s = WorkflowScheduleUpdate(cron="  0 9 * * *  ", enabled=False)
        assert s.cron == "0 9 * * *"


# ---------------------------------------------------------------------------
# WorkflowScheduleResponse
# ---------------------------------------------------------------------------


class TestScheduleResponseSchema:
    """Tests for the schedule response schema."""

    def test_defaults(self) -> None:
        r = WorkflowScheduleResponse()
        assert r.schedule_cron is None
        assert r.schedule_enabled is False
        assert r.schedule_inputs is None
        assert r.schedule_timezone == "UTC"
        assert r.next_run_at is None

    def test_full_response(self) -> None:
        r = WorkflowScheduleResponse(
            schedule_cron="0 9 * * 1-5",
            schedule_enabled=True,
            schedule_inputs={"x": 1},
            schedule_timezone="Asia/Tokyo",
            next_run_at="2026-03-16T09:00:00+09:00",
        )
        assert r.schedule_cron == "0 9 * * 1-5"
        assert r.schedule_enabled is True
        assert r.next_run_at is not None


# ---------------------------------------------------------------------------
# _compute_next_run (requires croniter)
# ---------------------------------------------------------------------------


class TestComputeNextRun:
    """Tests for the next-run-time calculator."""

    def test_returns_iso_string(self) -> None:
        result = _compute_next_run("* * * * *", "UTC")
        assert result is not None
        # Should be a valid ISO 8601 datetime
        assert "T" in result

    def test_respects_timezone(self) -> None:
        utc_result = _compute_next_run("0 9 * * *", "UTC")
        tokyo_result = _compute_next_run("0 9 * * *", "Asia/Tokyo")
        # Both should return results but with different offsets
        assert utc_result is not None
        assert tokyo_result is not None
        # UTC result should end with +00:00, Tokyo with +09:00
        assert "+00:00" in utc_result
        assert "+09:00" in tokyo_result

    def test_invalid_cron_returns_none(self) -> None:
        result = _compute_next_run("invalid", "UTC")
        assert result is None

    def test_invalid_timezone_falls_back_to_utc(self) -> None:
        result = _compute_next_run("0 9 * * *", "Not/A/Timezone")
        # Should still work, falling back to UTC
        assert result is not None

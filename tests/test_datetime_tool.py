"""Tests for the built-in ``DateTimeTool``."""

from __future__ import annotations

import re
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from fim_one.core.tool.builtin.datetime_tool import DateTimeTool


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture()
def tool() -> DateTimeTool:
    return DateTimeTool()


# ======================================================================
# Tool protocol compliance
# ======================================================================


class TestDateTimeToolProperties:
    """Verify tool protocol properties."""

    def test_name(self, tool: DateTimeTool) -> None:
        assert tool.name == "datetime"

    def test_category(self, tool: DateTimeTool) -> None:
        assert tool.category == "general"

    def test_description_mentions_timezone(self, tool: DateTimeTool) -> None:
        assert "timezone" in tool.description.lower()

    def test_parameters_schema(self, tool: DateTimeTool) -> None:
        schema = tool.parameters_schema
        assert schema["type"] == "object"
        assert "timezone" in schema["properties"]
        # No required keys — timezone is optional, defaults to UTC.
        assert "required" not in schema or schema.get("required") in ([], None)


# ======================================================================
# Current time formatting (mocked clock for determinism)
# ======================================================================


class TestCurrentTimeFormatting:
    """Tests for the formatted output using a frozen clock."""

    async def test_default_is_utc(self, tool: DateTimeTool) -> None:
        frozen = datetime(2026, 5, 30, 12, 34, 56, tzinfo=ZoneInfo("UTC"))
        with patch(
            "fim_one.core.tool.builtin.datetime_tool.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = frozen
            result = await tool.run()

        assert "Timezone : UTC (UTC+0000)" in result
        assert "Date     : 2026-05-30" in result
        assert "Time     : 12:34:56" in result

    async def test_iso_8601_line_present(self, tool: DateTimeTool) -> None:
        frozen = datetime(2026, 5, 30, 12, 34, 56, tzinfo=ZoneInfo("UTC"))
        with patch(
            "fim_one.core.tool.builtin.datetime_tool.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = frozen
            result = await tool.run()

        assert "ISO 8601 : 2026-05-30T12:34:56+00:00" in result

    async def test_weekday_line(self, tool: DateTimeTool) -> None:
        # 2026-05-30 is a Saturday.
        frozen = datetime(2026, 5, 30, 0, 0, 0, tzinfo=ZoneInfo("UTC"))
        with patch(
            "fim_one.core.tool.builtin.datetime_tool.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = frozen
            result = await tool.run()

        assert "Weekday  : Saturday" in result

    async def test_now_called_with_resolved_zoneinfo(self, tool: DateTimeTool) -> None:
        frozen = datetime(2026, 5, 30, 12, 0, 0, tzinfo=ZoneInfo("UTC"))
        with patch(
            "fim_one.core.tool.builtin.datetime_tool.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = frozen
            await tool.run(timezone="UTC")

        # datetime.now must be invoked with a tzinfo object.
        assert mock_dt.now.call_count == 1
        (passed_tz,), _ = mock_dt.now.call_args
        assert isinstance(passed_tz, ZoneInfo)


# ======================================================================
# Timezone handling (real ZoneInfo, mocked clock)
# ======================================================================


class TestTimezoneHandling:
    """Tests for explicit timezone offsets."""

    async def test_named_timezone_offset(self, tool: DateTimeTool) -> None:
        frozen = datetime(
            2026, 1, 15, 8, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")
        )
        with patch(
            "fim_one.core.tool.builtin.datetime_tool.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = frozen
            result = await tool.run(timezone="Asia/Shanghai")

        # Shanghai is UTC+8 year-round.
        assert "Asia/Shanghai (UTC+0800)" in result

    async def test_us_eastern_winter_offset(self, tool: DateTimeTool) -> None:
        # Mid-January -> EST, UTC-5.
        frozen = datetime(
            2026, 1, 15, 9, 0, 0, tzinfo=ZoneInfo("America/New_York")
        )
        with patch(
            "fim_one.core.tool.builtin.datetime_tool.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = frozen
            result = await tool.run(timezone="America/New_York")

        assert "America/New_York (UTC-0500)" in result

    async def test_empty_timezone_falls_back_to_utc(self, tool: DateTimeTool) -> None:
        frozen = datetime(2026, 5, 30, 0, 0, 0, tzinfo=ZoneInfo("UTC"))
        with patch(
            "fim_one.core.tool.builtin.datetime_tool.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = frozen
            # Falsy timezone -> the `or "UTC"` guard kicks in.
            result = await tool.run(timezone="")

        assert "Timezone : UTC" in result

    async def test_none_timezone_falls_back_to_utc(self, tool: DateTimeTool) -> None:
        frozen = datetime(2026, 5, 30, 0, 0, 0, tzinfo=ZoneInfo("UTC"))
        with patch(
            "fim_one.core.tool.builtin.datetime_tool.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = frozen
            result = await tool.run(timezone=None)

        assert "Timezone : UTC" in result


# ======================================================================
# Error handling
# ======================================================================


class TestErrorHandling:
    """Tests for invalid timezone input."""

    async def test_unknown_timezone(self, tool: DateTimeTool) -> None:
        result = await tool.run(timezone="Mars/Olympus_Mons")
        assert "[Error]" in result
        assert "Unknown timezone" in result
        assert "Mars/Olympus_Mons" in result

    async def test_garbage_timezone(self, tool: DateTimeTool) -> None:
        result = await tool.run(timezone="not a tz")
        assert "[Error]" in result
        assert "Unknown timezone" in result


# ======================================================================
# Real-clock smoke test (no mock)
# ======================================================================


class TestRealClock:
    """Sanity checks against the actual system clock."""

    async def test_real_utc_output_shape(self, tool: DateTimeTool) -> None:
        result = await tool.run(timezone="UTC")
        assert "Current date/time:" in result
        # Date line matches YYYY-MM-DD.
        assert re.search(r"Date     : \d{4}-\d{2}-\d{2}", result)
        # Time line matches HH:MM:SS.
        assert re.search(r"Time     : \d{2}:\d{2}:\d{2}", result)
        assert "Timezone : UTC (UTC+0000)" in result

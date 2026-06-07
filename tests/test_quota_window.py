"""Tests for billing-aligned quota-window resolution.

Covers the pure month arithmetic and the ``resolve_quota_window``
entitlement branching. The key invariant under test: the usage window is
always *monthly*, even for an annually-billed subscription, because the
quota is ``monthly_token_quota`` — anchoring to the Stripe billing period
would give an annual subscriber a one-year window.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from fim_one.web.services.quota_window import (
    add_months,
    calendar_month_window,
    monthly_anniversary_window,
    resolve_quota_window,
)


# ---------------------------------------------------------------------------
# add_months — day-clamping is the subtle part
# ---------------------------------------------------------------------------


class TestAddMonths:
    def test_simple_forward(self) -> None:
        assert add_months(datetime(2026, 1, 15, tzinfo=UTC), 1) == datetime(
            2026, 2, 15, tzinfo=UTC
        )

    def test_year_rollover(self) -> None:
        assert add_months(datetime(2026, 12, 15, tzinfo=UTC), 1) == datetime(
            2027, 1, 15, tzinfo=UTC
        )

    def test_day_31_clamps_into_february(self) -> None:
        # No 31st in Feb 2026 (non-leap) → clamp to the 28th.
        assert add_months(datetime(2026, 1, 31, tzinfo=UTC), 1) == datetime(
            2026, 2, 28, tzinfo=UTC
        )

    def test_day_31_clamps_into_leap_february(self) -> None:
        assert add_months(datetime(2028, 1, 31, tzinfo=UTC), 1) == datetime(
            2028, 2, 29, tzinfo=UTC
        )

    def test_multi_month_span(self) -> None:
        assert add_months(datetime(2026, 1, 31, tzinfo=UTC), 13) == datetime(
            2027, 2, 28, tzinfo=UTC
        )


# ---------------------------------------------------------------------------
# calendar_month_window
# ---------------------------------------------------------------------------


class TestCalendarMonthWindow:
    def test_brackets_the_current_month(self) -> None:
        start, end = calendar_month_window(datetime(2026, 6, 8, 14, 0, tzinfo=UTC))
        assert start == datetime(2026, 6, 1, tzinfo=UTC)
        assert end == datetime(2026, 7, 1, tzinfo=UTC)

    def test_december_rolls_into_next_year(self) -> None:
        start, end = calendar_month_window(datetime(2026, 12, 20, tzinfo=UTC))
        assert start == datetime(2026, 12, 1, tzinfo=UTC)
        assert end == datetime(2027, 1, 1, tzinfo=UTC)

    def test_naive_now_is_coerced_to_utc(self) -> None:
        start, end = calendar_month_window(datetime(2026, 6, 8, 14, 0))
        assert start == datetime(2026, 6, 1, tzinfo=UTC)
        assert end == datetime(2026, 7, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# monthly_anniversary_window
# ---------------------------------------------------------------------------


class TestMonthlyAnniversaryWindow:
    def test_now_just_after_anchor(self) -> None:
        anchor = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        start, end = monthly_anniversary_window(anchor, datetime(2026, 1, 20, tzinfo=UTC))
        assert start == anchor
        assert end == datetime(2026, 2, 15, 12, 0, tzinfo=UTC)

    def test_now_just_before_next_anniversary(self) -> None:
        anchor = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        start, end = monthly_anniversary_window(anchor, datetime(2026, 2, 10, tzinfo=UTC))
        assert start == anchor
        assert end == datetime(2026, 2, 15, 12, 0, tzinfo=UTC)

    def test_now_after_crossing_anniversary_advances(self) -> None:
        anchor = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        start, end = monthly_anniversary_window(anchor, datetime(2026, 2, 20, tzinfo=UTC))
        assert start == datetime(2026, 2, 15, 12, 0, tzinfo=UTC)
        assert end == datetime(2026, 3, 15, 12, 0, tzinfo=UTC)

    def test_window_always_contains_now(self) -> None:
        anchor = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        now = datetime(2026, 9, 3, 7, 30, tzinfo=UTC)
        start, end = monthly_anniversary_window(anchor, now)
        assert start <= now < end

    def test_annual_subscription_still_resets_monthly(self) -> None:
        # Anchor 11 months back (an annually-billed sub mid-year). The
        # window must be ~1 month wide, NOT a year — the regression this
        # whole change exists to prevent.
        anchor = datetime(2026, 1, 15, tzinfo=UTC)
        now = datetime(2026, 11, 20, tzinfo=UTC)
        start, end = monthly_anniversary_window(anchor, now)
        assert start == datetime(2026, 11, 15, tzinfo=UTC)
        assert end == datetime(2026, 12, 15, tzinfo=UTC)
        assert end - start <= timedelta(days=31)

    def test_day_31_anchor_across_february(self) -> None:
        anchor = datetime(2026, 1, 31, tzinfo=UTC)
        # Mid-Feb: the Jan-31 → Feb-28 window contains it.
        start, end = monthly_anniversary_window(anchor, datetime(2026, 2, 15, tzinfo=UTC))
        assert start == datetime(2026, 1, 31, tzinfo=UTC)
        assert end == datetime(2026, 2, 28, tzinfo=UTC)
        # Early March: the next window opens at the clamped Feb-28.
        start2, end2 = monthly_anniversary_window(
            anchor, datetime(2026, 3, 1, tzinfo=UTC)
        )
        assert start2 == datetime(2026, 2, 28, tzinfo=UTC)
        assert end2 == datetime(2026, 3, 31, tzinfo=UTC)

    def test_anchor_in_future_returns_anchor_window(self) -> None:
        anchor = datetime(2026, 6, 10, tzinfo=UTC)
        start, end = monthly_anniversary_window(anchor, datetime(2026, 6, 1, tzinfo=UTC))
        assert start == anchor
        assert end == datetime(2026, 7, 10, tzinfo=UTC)


# ---------------------------------------------------------------------------
# resolve_quota_window — entitlement branching
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _FakeSession:
    """Returns a canned ``.first()`` row for the subscription lookup."""

    def __init__(self, row: Any, *, raise_on_execute: bool = False) -> None:
        self._row = row
        self._raise = raise_on_execute

    async def execute(self, _stmt: Any) -> _FakeResult:
        if self._raise:
            raise RuntimeError("boom")
        return _FakeResult(self._row)


NOW = datetime(2026, 6, 8, tzinfo=UTC)


@pytest.mark.asyncio
async def test_no_subscription_falls_back_to_calendar_month() -> None:
    start, end = await resolve_quota_window(_FakeSession(None), "u1", now=NOW)
    assert start == datetime(2026, 6, 1, tzinfo=UTC)
    assert end == datetime(2026, 7, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_active_subscription_uses_anniversary_window() -> None:
    # Anchored on the 15th; period_end is a month out (monthly plan).
    row = (datetime(2026, 1, 15, tzinfo=UTC), "active", datetime(2026, 7, 15, tzinfo=UTC))
    start, end = await resolve_quota_window(_FakeSession(row), "u1", now=NOW)
    assert start == datetime(2026, 5, 15, tzinfo=UTC)
    assert end == datetime(2026, 6, 15, tzinfo=UTC)


@pytest.mark.asyncio
async def test_annual_active_subscription_resets_monthly_not_yearly() -> None:
    # period_end a full year out (annually-billed) — window must still be
    # the current monthly slice, reset ~1 month out.
    row = (datetime(2026, 1, 15, tzinfo=UTC), "active", datetime(2027, 1, 15, tzinfo=UTC))
    start, end = await resolve_quota_window(_FakeSession(row), "u1", now=NOW)
    assert start == datetime(2026, 5, 15, tzinfo=UTC)
    assert end == datetime(2026, 6, 15, tzinfo=UTC)
    assert end - start <= timedelta(days=31)


@pytest.mark.asyncio
async def test_canceled_but_within_paid_window_keeps_anniversary() -> None:
    # Canceled, but period_end still in the future → entitled.
    row = (
        datetime(2026, 1, 15, tzinfo=UTC),
        "canceled",
        datetime(2026, 6, 20, tzinfo=UTC),
    )
    start, end = await resolve_quota_window(_FakeSession(row), "u1", now=NOW)
    assert start == datetime(2026, 5, 15, tzinfo=UTC)
    assert end == datetime(2026, 6, 15, tzinfo=UTC)


@pytest.mark.asyncio
async def test_canceled_and_expired_falls_back_to_calendar_month() -> None:
    row = (
        datetime(2026, 1, 15, tzinfo=UTC),
        "canceled",
        datetime(2026, 5, 1, tzinfo=UTC),  # already passed
    )
    start, end = await resolve_quota_window(_FakeSession(row), "u1", now=NOW)
    assert start == datetime(2026, 6, 1, tzinfo=UTC)
    assert end == datetime(2026, 7, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_past_due_is_still_entitled() -> None:
    row = (
        datetime(2026, 1, 15, tzinfo=UTC),
        "past_due",
        datetime(2026, 5, 1, tzinfo=UTC),
    )
    start, end = await resolve_quota_window(_FakeSession(row), "u1", now=NOW)
    # Not canceled → keep the anniversary window even past period_end.
    assert start == datetime(2026, 5, 15, tzinfo=UTC)
    assert end == datetime(2026, 6, 15, tzinfo=UTC)


@pytest.mark.asyncio
async def test_naive_stored_datetimes_do_not_raise() -> None:
    # SQLite hands back naive datetimes for tz-aware columns; the
    # resolver must coerce rather than raise on comparison.
    row = (
        datetime(2026, 1, 15),  # naive
        "active",
        datetime(2026, 7, 15),  # naive
    )
    start, end = await resolve_quota_window(_FakeSession(row), "u1", now=NOW)
    assert start == datetime(2026, 5, 15, tzinfo=UTC)
    assert end == datetime(2026, 6, 15, tzinfo=UTC)


@pytest.mark.asyncio
async def test_lookup_failure_falls_back_to_calendar_month() -> None:
    session = _FakeSession(None, raise_on_execute=True)
    start, end = await resolve_quota_window(session, "u1", now=NOW)
    assert start == datetime(2026, 6, 1, tzinfo=UTC)
    assert end == datetime(2026, 7, 1, tzinfo=UTC)

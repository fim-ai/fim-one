"""Quota usage-window resolution.

Decides the ``[window_start, reset_at)`` interval a user's monthly token
quota is measured over. Two regimes:

- **Paid (live entitlement)** — a *monthly* window anchored to the
  subscription's billing day-of-month. The window is monthly **even on
  annually-billed plans**, because the quota itself is
  :attr:`BillingPlan.monthly_token_quota`: an annual subscriber's tokens
  refill every month, not once a year. Anchoring to the Stripe billing
  *period* would hand an annual subscriber a one-year window and lock
  them out after the first month — so we deliberately do not.
- **Free / no entitlement** — the calendar month (1st 00:00 UTC → next
  1st). Stateless and self-healing; free users have no billing anchor to
  align to, so the cheapest correct thing wins.

**Read-only.** This module never mutates subscription rows. It reads
``current_period_start`` purely for its *day-of-month* — the stable
billing anchor day — so it does **not** depend on that column being
refreshed on renewal. (The renewal webhook advances
``current_period_end`` but not ``current_period_start``; the
monthly-anniversary math rolls the window forward on its own regardless.)

Enforcement semantics for a canceled subscription mirror the rest of the
billing layer: a user keeps their paid-for window until
``current_period_end`` passes, after which the lifecycle sweep downgrades
them to Free and they fall back to the calendar month here.
"""

from __future__ import annotations

import calendar
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db.models import Subscription

logger = logging.getLogger(__name__)


def _aware(dt: datetime) -> datetime:
    """Coerce a possibly-naive datetime to tz-aware UTC.

    SQLite returns naive datetimes for ``DateTime(timezone=True)``
    columns; PG returns aware ones. Normalise so comparisons never raise
    ``TypeError: can't compare offset-naive and offset-aware``.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def add_months(dt: datetime, months: int) -> datetime:
    """Return *dt* shifted by *months* calendar months, clamping the day.

    Day-clamping handles short months: an anchor on the 31st maps to the
    28th/29th in February, the 30th in April, etc. — matching how Stripe
    anchors a monthly subscription created on the 31st.
    """
    index = dt.month - 1 + months
    year = dt.year + index // 12
    month = index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def calendar_month_window(now: datetime) -> tuple[datetime, datetime]:
    """``[1st of this month, 1st of next month)`` in UTC."""
    now = _aware(now)
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    return start, add_months(start, 1)


def monthly_anniversary_window(
    anchor: datetime, now: datetime
) -> tuple[datetime, datetime]:
    """The monthly slice containing *now*, anchored to *anchor*'s day.

    Returns ``[anchor + k months, anchor + (k+1) months)`` for the unique
    integer ``k`` with ``start <= now < end``. Robust to day-clamping
    drift around short months (the explicit ``while`` re-checks instead of
    trusting the arithmetic estimate of ``k``).
    """
    anchor = _aware(anchor)
    now = _aware(now)

    # Anchor in the future (clock skew / unexpected data): treat the
    # anchor itself as the window start rather than producing a window
    # that ends before it begins.
    if anchor >= now:
        return anchor, add_months(anchor, 1)

    # Estimate k from the month delta, then correct for day-clamping.
    k = (now.year - anchor.year) * 12 + (now.month - anchor.month)
    start = add_months(anchor, k)
    if start > now:
        k -= 1
        start = add_months(anchor, k)
    while add_months(anchor, k + 1) <= now:
        k += 1
        start = add_months(anchor, k)
    return start, add_months(anchor, k + 1)


async def resolve_quota_window(
    session: AsyncSession,
    user_id: str,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Resolve the ``(window_start, reset_at)`` for *user_id*.

    Paid users with a live entitlement get a monthly-anniversary window
    anchored to their subscription's billing day; everyone else gets the
    calendar month. Any lookup failure falls back to the calendar month —
    this keeps mock/fake sessions (and degraded DBs) on a safe, correct
    default rather than blocking enforcement.
    """
    now = _aware(now) if now is not None else datetime.now(UTC)

    try:
        row = (
            await session.execute(
                select(
                    Subscription.current_period_start,
                    Subscription.status,
                    Subscription.current_period_end,
                )
                .where(Subscription.user_id == user_id)
                # A user may have stale rows from past cancel/resubscribe
                # cycles; the newest period is the live one.
                .order_by(Subscription.current_period_end.desc())
                .limit(1)
            )
        ).first()
    except Exception:  # pragma: no cover - defensive; see docstring
        logger.debug(
            "resolve_quota_window: subscription lookup failed for %s; "
            "falling back to calendar month",
            user_id,
            exc_info=True,
        )
        return calendar_month_window(now)

    if row is None:
        return calendar_month_window(now)

    period_start, status, period_end = row
    if period_start is None:
        return calendar_month_window(now)

    # Entitlement mirrors the billing layer: keep the paid window until
    # current_period_end passes; only a *canceled-and-expired* sub loses
    # it (the lifecycle sweep then flips the user to Free).
    expired = period_end is not None and _aware(period_end) <= now
    if status == "canceled" and expired:
        return calendar_month_window(now)

    return monthly_anniversary_window(period_start, now)


__all__ = [
    "add_months",
    "calendar_month_window",
    "monthly_anniversary_window",
    "resolve_quota_window",
]

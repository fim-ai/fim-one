"""Periodic downgrade of canceled subscriptions to the Free plan.

Stripe webhooks set ``Subscription.status='canceled'`` and stamp
``canceled_at`` the moment the user cancels — but the user must keep
their Pro features until ``current_period_end`` (they paid for it).
This module owns the "cliff drop" at period-end:

- A periodic job sweeps canceled subscriptions whose ``current_period_end``
  has passed and resets the owning ``User.plan_id`` to the free plan.
- Wired into the FastAPI lifespan in :func:`start_lifecycle_loop` so it
  runs once an hour without external schedulers.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db.models import Subscription, User
from fim_one.web.services.billing_flag import (
    ACCESS_MODEL_FREEMIUM,
    ACCESS_MODEL_PAID_ONLY,
    get_access_model,
)
from fim_one.web.services.default_plan import get_default_plan_id

logger = logging.getLogger(__name__)

#: Kept as a published name so existing imports do not break. New
#: code should resolve the downgrade target via ``default_plan_id``.
FREE_PLAN_SLUG = "free"

#: Default sweep interval. One hour matches Stripe's webhook-retry SLA
#: and is short enough that a missed period_end is corrected within the
#: next billing-relevant interaction.
DEFAULT_INTERVAL_SECONDS: int = 3600


async def downgrade_expired_canceled_subscriptions(db: AsyncSession) -> int:
    """Reset users with expired canceled subs back to Free.

    A subscription is "expired" when ``status == 'canceled'`` AND
    ``current_period_end < utcnow()``. The lifecycle job is the only
    place that flips ``User.plan_id`` to free — the webhook handler
    deliberately leaves it untouched on ``customer.subscription.deleted``.

    Args:
        db: Async session managed by the caller (commit happens here).

    Returns:
        The number of users downgraded in this pass. ``0`` is the steady
        state.
    """
    model = await get_access_model(db)
    target_plan_id: int | None
    if model == ACCESS_MODEL_FREEMIUM:
        target_plan_id = await get_default_plan_id(db)
        if target_plan_id is None:
            logger.warning(
                "Freemium default plan missing — skipping subscription "
                "downgrade sweep"
            )
            return 0
    elif model == ACCESS_MODEL_PAID_ONLY:
        # Period-end cancel becomes unentitled.
        target_plan_id = None
    else:
        return 0

    now = datetime.now(UTC)
    result = await db.execute(
        select(Subscription).where(
            and_(
                Subscription.status == "canceled",
                Subscription.current_period_end < now,
            )
        )
    )
    expired = list(result.scalars().all())

    if not expired:
        return 0

    downgraded = 0
    for sub in expired:
        user = await db.get(User, sub.user_id)
        if user is None:
            continue
        if user.plan_id == target_plan_id:
            continue
        user.plan_id = target_plan_id
        downgraded += 1
        logger.info(
            "Downgraded user %s to plan_id=%s after canceled subscription %s expired",
            sub.user_id,
            target_plan_id,
            sub.stripe_subscription_id,
        )

    if downgraded > 0:
        await db.commit()
    return downgraded


async def _run_loop(interval_seconds: int) -> None:
    """Internal loop body — sweeps once per ``interval_seconds``.

    Errors are logged but never crash the loop; the lifecycle job is
    best-effort and a transient DB blip should not poison the FastAPI
    process.
    """
    from fim_one.db import create_session

    logger.info(
        "Subscription lifecycle loop started (interval=%ds)", interval_seconds
    )
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            async with create_session() as db:
                await downgrade_expired_canceled_subscriptions(db)
        except asyncio.CancelledError:
            logger.info("Subscription lifecycle loop stopped")
            break
        except Exception:
            logger.exception("Subscription lifecycle sweep failed")


def start_lifecycle_loop(
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
) -> asyncio.Task[None]:
    """Schedule the sweep as a background task.

    Returns the :class:`asyncio.Task` so the lifespan can ``cancel()`` it
    on shutdown.
    """
    return asyncio.create_task(_run_loop(interval_seconds))


__all__ = [
    "DEFAULT_INTERVAL_SECONDS",
    "FREE_PLAN_SLUG",
    "downgrade_expired_canceled_subscriptions",
    "start_lifecycle_loop",
]

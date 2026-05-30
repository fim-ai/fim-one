"""Tests for ``fim_one.web.services.subscription_lifecycle``.

Validates that the periodic sweep:
- Demotes users with canceled subs whose ``current_period_end`` has passed.
- Leaves users with canceled subs whose period is still active alone.
- Leaves active subscriptions untouched.
- Skips when the free plan row is missing.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401 — register all models with metadata
from fim_one.db.base import Base
from fim_one.db.models import BillingPlan, Subscription, User
from fim_one.web.services.subscription_lifecycle import (
    downgrade_expired_canceled_subscriptions,
)


@pytest_asyncio.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _seed_plans(session: AsyncSession) -> tuple[BillingPlan, BillingPlan]:
    free = BillingPlan(slug="free", name="Free", monthly_token_quota=100_000)
    pro = BillingPlan(
        slug="pro",
        name="Pro",
        stripe_price_id="price_test_pro",
        monthly_token_quota=5_000_000,
    )
    session.add_all([free, pro])
    await session.commit()
    return free, pro


async def _make_user_with_sub(
    session: AsyncSession,
    *,
    plan: BillingPlan,
    sub_status: str,
    period_end: datetime,
) -> tuple[User, Subscription]:
    user = User(
        id=str(uuid.uuid4()),
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:6]}@example.com",
        plan_id=plan.id,
    )
    session.add(user)
    await session.flush()

    sub = Subscription(
        user_id=user.id,
        plan_id=plan.id,
        stripe_subscription_id=f"sub_{uuid.uuid4().hex[:10]}",
        stripe_price_id=plan.stripe_price_id or "price_test",
        status=sub_status,
        current_period_start=period_end - timedelta(days=30),
        current_period_end=period_end,
        cancel_at_period_end=(sub_status == "canceled"),
        canceled_at=datetime.now(UTC) if sub_status == "canceled" else None,
        updated_at=datetime.now(UTC),
    )
    session.add(sub)
    await session.commit()
    return user, sub


class TestDowngradeExpiredCanceledSubscriptions:
    """The sweep is the only owner of the canceled→free flip."""

    @pytest.mark.asyncio
    async def test_canceled_and_period_passed_demotes_to_free(
        self, session: AsyncSession
    ) -> None:
        free, pro = await _seed_plans(session)
        user, _sub = await _make_user_with_sub(
            session,
            plan=pro,
            sub_status="canceled",
            period_end=datetime.now(UTC) - timedelta(hours=1),
        )

        downgraded = await downgrade_expired_canceled_subscriptions(session)
        await session.refresh(user)

        assert downgraded == 1
        assert user.plan_id == free.id

    @pytest.mark.asyncio
    async def test_canceled_but_period_still_active_keeps_pro(
        self, session: AsyncSession
    ) -> None:
        _free, pro = await _seed_plans(session)
        user, _sub = await _make_user_with_sub(
            session,
            plan=pro,
            sub_status="canceled",
            period_end=datetime.now(UTC) + timedelta(days=5),
        )

        downgraded = await downgrade_expired_canceled_subscriptions(session)
        await session.refresh(user)

        assert downgraded == 0
        assert user.plan_id == pro.id

    @pytest.mark.asyncio
    async def test_active_subscription_untouched(
        self, session: AsyncSession
    ) -> None:
        _free, pro = await _seed_plans(session)
        user, _sub = await _make_user_with_sub(
            session,
            plan=pro,
            sub_status="active",
            period_end=datetime.now(UTC) + timedelta(days=10),
        )

        downgraded = await downgrade_expired_canceled_subscriptions(session)
        await session.refresh(user)

        assert downgraded == 0
        assert user.plan_id == pro.id

    @pytest.mark.asyncio
    async def test_already_on_free_plan_not_double_counted(
        self, session: AsyncSession
    ) -> None:
        # Edge case: a sweep ran, demoted the user, but we kept the
        # canceled sub row around. The next sweep must not count the
        # already-downgraded user.
        free, pro = await _seed_plans(session)
        user, _sub = await _make_user_with_sub(
            session,
            plan=pro,
            sub_status="canceled",
            period_end=datetime.now(UTC) - timedelta(days=1),
        )
        user.plan_id = free.id
        await session.commit()

        downgraded = await downgrade_expired_canceled_subscriptions(session)
        await session.refresh(user)

        assert downgraded == 0
        assert user.plan_id == free.id

    @pytest.mark.asyncio
    async def test_returns_zero_when_free_plan_missing(
        self, session: AsyncSession
    ) -> None:
        # No plans seeded — function must short-circuit cleanly.
        result = await downgrade_expired_canceled_subscriptions(session)
        assert result == 0

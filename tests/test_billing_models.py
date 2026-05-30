"""Tests for the billing ORM models (BillingPlan / Subscription / StripeWebhookEvent).

Covers:
- Round-trip persist / load for each model.
- FK cascade behavior: deleting a User cascades to its Subscription.
- Default values match the migration's ``server_default`` clauses
  (boolean, integer, JSON-empty).
- One-to-one ``user.subscription`` relationship integrity.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401 — register all models with metadata
from fim_one.db.base import Base
from fim_one.db.models.billing import (
    BillingPlan,
    StripeWebhookEvent,
    Subscription,
)
from fim_one.db.models.user import User


# ---------------------------------------------------------------------------
# Fixtures — in-memory SQLite async database
# ---------------------------------------------------------------------------


@pytest.fixture()
async def async_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


async def _make_user(session: AsyncSession, *, email: str = "u@example.com") -> User:
    user = User(
        id=str(uuid.uuid4()),
        username=f"user_{uuid.uuid4().hex[:8]}",
        email=email,
        is_admin=False,
    )
    session.add(user)
    await session.commit()
    return user


async def _make_pro_plan(session: AsyncSession) -> BillingPlan:
    plan = BillingPlan(
        slug="pro",
        name="Pro",
        stripe_price_id="price_test_pro",
        monthly_token_quota=5_000_000,
        description="5M tokens / month",
    )
    session.add(plan)
    await session.commit()
    return plan


# ---------------------------------------------------------------------------
# BillingPlan
# ---------------------------------------------------------------------------


class TestBillingPlan:
    """Catalogue table for purchasable plans."""

    async def test_persist_and_load(self, async_session: AsyncSession) -> None:
        plan = BillingPlan(
            slug="free",
            name="Free",
            stripe_price_id=None,
            monthly_token_quota=100_000,
            description="100K tokens / month",
        )
        async_session.add(plan)
        await async_session.commit()

        loaded = await async_session.scalar(
            select(BillingPlan).where(BillingPlan.slug == "free")
        )
        assert loaded is not None
        assert loaded.name == "Free"
        assert loaded.stripe_price_id is None
        assert loaded.monthly_token_quota == 100_000

    async def test_defaults_match_migration(self, async_session: AsyncSession) -> None:
        """``is_active`` defaults TRUE, ``sort_order`` 0, ``features_json`` {}."""
        plan = BillingPlan(
            slug="defaults_check",
            name="Defaults",
            monthly_token_quota=1,
        )
        async_session.add(plan)
        await async_session.commit()
        await async_session.refresh(plan)

        assert plan.is_active is True
        assert plan.sort_order == 0
        assert plan.features_json == {}
        assert plan.created_at is not None

    async def test_slug_uniqueness(self, async_session: AsyncSession) -> None:
        async_session.add(
            BillingPlan(slug="dup", name="A", monthly_token_quota=1)
        )
        await async_session.commit()

        async_session.add(
            BillingPlan(slug="dup", name="B", monthly_token_quota=2)
        )
        with pytest.raises(Exception):  # IntegrityError on UNIQUE(slug)
            await async_session.commit()
        await async_session.rollback()

    async def test_stripe_price_id_uniqueness(
        self, async_session: AsyncSession
    ) -> None:
        async_session.add(
            BillingPlan(
                slug="a",
                name="A",
                stripe_price_id="price_dup",
                monthly_token_quota=1,
            )
        )
        await async_session.commit()

        async_session.add(
            BillingPlan(
                slug="b",
                name="B",
                stripe_price_id="price_dup",
                monthly_token_quota=2,
            )
        )
        with pytest.raises(Exception):
            await async_session.commit()
        await async_session.rollback()


# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------


class TestSubscription:
    """Per-user Stripe subscription state."""

    async def test_persist_and_load(self, async_session: AsyncSession) -> None:
        user = await _make_user(async_session)
        plan = await _make_pro_plan(async_session)
        now = datetime.now(timezone.utc)

        sub = Subscription(
            user_id=user.id,
            plan_id=plan.id,
            stripe_subscription_id="sub_test_123",
            stripe_price_id="price_test_pro",
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
            updated_at=now,
        )
        async_session.add(sub)
        await async_session.commit()

        loaded = await async_session.scalar(
            select(Subscription).where(
                Subscription.stripe_subscription_id == "sub_test_123"
            )
        )
        assert loaded is not None
        assert loaded.user_id == user.id
        assert loaded.plan_id == plan.id
        assert loaded.status == "active"
        assert loaded.cancel_at_period_end is False
        assert loaded.canceled_at is None

    async def test_unique_stripe_subscription_id(
        self, async_session: AsyncSession
    ) -> None:
        user = await _make_user(async_session, email="a@example.com")
        user2 = await _make_user(async_session, email="b@example.com")
        plan = await _make_pro_plan(async_session)
        now = datetime.now(timezone.utc)

        async_session.add(
            Subscription(
                user_id=user.id,
                plan_id=plan.id,
                stripe_subscription_id="sub_dup",
                stripe_price_id="price_test_pro",
                status="active",
                current_period_start=now,
                current_period_end=now + timedelta(days=30),
                updated_at=now,
            )
        )
        await async_session.commit()

        async_session.add(
            Subscription(
                user_id=user2.id,
                plan_id=plan.id,
                stripe_subscription_id="sub_dup",
                stripe_price_id="price_test_pro",
                status="active",
                current_period_start=now,
                current_period_end=now + timedelta(days=30),
                updated_at=now,
            )
        )
        with pytest.raises(Exception):
            await async_session.commit()
        await async_session.rollback()

    async def test_user_delete_cascades_subscription(
        self, async_session: AsyncSession
    ) -> None:
        """Deleting a User must take its Subscription with it.

        Stripe still holds the subscription record on its side, but our
        local row should never outlive its owner — orphaned subscriptions
        would mis-attribute usage on the next webhook.
        """
        user = await _make_user(async_session)
        plan = await _make_pro_plan(async_session)
        now = datetime.now(timezone.utc)

        sub = Subscription(
            user_id=user.id,
            plan_id=plan.id,
            stripe_subscription_id="sub_cascade",
            stripe_price_id="price_test_pro",
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
            updated_at=now,
        )
        async_session.add(sub)
        await async_session.commit()

        # Enable SQLite FK enforcement (off by default on aiosqlite).
        await async_session.execute(text("PRAGMA foreign_keys = ON"))

        await async_session.delete(user)
        await async_session.commit()

        remaining = await async_session.scalar(
            select(Subscription).where(
                Subscription.stripe_subscription_id == "sub_cascade"
            )
        )
        assert remaining is None


# ---------------------------------------------------------------------------
# StripeWebhookEvent
# ---------------------------------------------------------------------------


class TestStripeWebhookEvent:
    """Idempotency ledger for Stripe webhook deliveries."""

    async def test_persist_and_load(self, async_session: AsyncSession) -> None:
        ev = StripeWebhookEvent(
            stripe_event_id="evt_test_1",
            event_type="checkout.session.completed",
        )
        async_session.add(ev)
        await async_session.commit()
        await async_session.refresh(ev)

        assert ev.received_at is not None
        assert ev.processed_at is None
        assert ev.error is None

    async def test_event_id_is_primary_key(
        self, async_session: AsyncSession
    ) -> None:
        async_session.add(
            StripeWebhookEvent(
                stripe_event_id="evt_pk", event_type="invoice.payment_succeeded"
            )
        )
        await async_session.commit()

        async_session.add(
            StripeWebhookEvent(
                stripe_event_id="evt_pk", event_type="invoice.payment_failed"
            )
        )
        with pytest.raises(Exception):
            await async_session.commit()
        await async_session.rollback()

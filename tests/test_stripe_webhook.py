"""Tests for the Stripe webhook ingestion endpoint and dispatcher.

Validates:
- All 5 supported event types apply the right state changes.
- Signature verification rejects bad / missing headers.
- Idempotency: replaying the same event id is a no-op (returns
  ``duplicate=True`` and does not double-apply state).
- Unknown event types return 200 and never raise.
- ``invoice.payment_succeeded`` only resets tokens on
  ``billing_reason='subscription_cycle'`` (not ``subscription_create``).
- ``customer.subscription.deleted`` does NOT immediately set the user's
  ``plan_id`` to free — that's the lifecycle sweep's job.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import fim_one.db.models  # noqa: F401 — register all models with metadata
from fim_one.db.base import Base
from fim_one.web.api import webhooks as webhooks_module
from fim_one.web.app import create_app
from fim_one.db.models import (
    BillingPlan,
    StripeWebhookEvent,
    Subscription,
    User,
)


TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


# ---------------------------------------------------------------------------
# Stripe configuration
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _enable_billing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_webhook")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_webhooktest")

    from fim_one.web.config import settings as cfg
    from fim_one.web.services import stripe_client

    cfg.reset()
    stripe_client.reset_for_testing()


# ---------------------------------------------------------------------------
# DB / app fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def engine() -> AsyncIterator:
    eng = create_async_engine(TEST_DB_URL)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture()
async def db_session(engine) -> AsyncIterator[AsyncSession]:
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture(autouse=True)
async def _enable_billing_flag(db_session: AsyncSession) -> None:
    """Persist ``billing_enabled='true'`` so the webhook gate passes."""
    from fim_one.db.models import SystemSetting

    db_session.add(SystemSetting(key="billing_enabled", value="true"))
    await db_session.commit()


@pytest_asyncio.fixture()
async def free_plan(db_session: AsyncSession) -> BillingPlan:
    plan = BillingPlan(slug="free", name="Free", monthly_token_quota=100_000)
    db_session.add(plan)
    await db_session.commit()
    return plan


@pytest_asyncio.fixture()
async def pro_plan(db_session: AsyncSession) -> BillingPlan:
    plan = BillingPlan(
        slug="pro",
        name="Pro",
        stripe_price_id="price_test_pro",
        monthly_token_quota=5_000_000,
    )
    db_session.add(plan)
    await db_session.commit()
    return plan


@pytest_asyncio.fixture()
async def user_alice(
    db_session: AsyncSession, free_plan: BillingPlan
) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username="alice",
        email="alice@example.com",
        password_hash="hashed",
        plan_id=free_plan.id,
        tokens_used_this_period=12345,
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest_asyncio.fixture()
async def client(engine, db_session, free_plan, pro_plan, user_alice):  # noqa: ARG001
    from fim_one.db import get_session

    @asynccontextmanager
    async def _noop_lifespan(app):
        yield

    with patch("fim_one.web.app.lifespan", _noop_lifespan):
        app = create_app()

    async def _override_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_session

    @asynccontextmanager
    async def _mock_create_session():
        yield db_session

    with patch("fim_one.db.create_session", _mock_create_session), \
         patch("fim_one.db.engine.create_session", _mock_create_session):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Event builders
# ---------------------------------------------------------------------------


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


def _checkout_completed_event(*, user_id: str, sub_id: str, customer_id: str = "cus_alice") -> dict[str, Any]:
    return {
        "id": "evt_checkout_1",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_123",
                "client_reference_id": user_id,
                "customer": customer_id,
                "subscription": sub_id,
            }
        },
    }


def _subscription_updated_event(*, sub_id: str, status: str = "active", cancel: bool = False, price_id: str | None = None, period_end: datetime | None = None) -> dict[str, Any]:
    period_end = period_end or (datetime.now(UTC) + timedelta(days=30))
    period_start = period_end - timedelta(days=30)
    items_data = []
    if price_id is not None:
        items_data.append({"price": {"id": price_id}})
    return {
        "id": "evt_sub_updated_1",
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": sub_id,
                "status": status,
                "cancel_at_period_end": cancel,
                "current_period_start": _ts(period_start),
                "current_period_end": _ts(period_end),
                "canceled_at": _ts(datetime.now(UTC)) if cancel else None,
                "items": {"data": items_data},
            }
        },
    }


def _subscription_deleted_event(*, sub_id: str, period_end: datetime) -> dict[str, Any]:
    return {
        "id": "evt_sub_deleted_1",
        "type": "customer.subscription.deleted",
        "data": {
            "object": {
                "id": sub_id,
                "status": "canceled",
                "current_period_end": _ts(period_end),
                "canceled_at": _ts(datetime.now(UTC)),
            }
        },
    }


def _invoice_paid_event(*, sub_id: str, billing_reason: str, period_end: datetime) -> dict[str, Any]:
    return {
        "id": f"evt_invoice_paid_{billing_reason}",
        "type": "invoice.payment_succeeded",
        "data": {
            "object": {
                "id": "in_123",
                "subscription": sub_id,
                "billing_reason": billing_reason,
                "period_end": _ts(period_end),
            }
        },
    }


def _invoice_failed_event(*, sub_id: str) -> dict[str, Any]:
    return {
        "id": "evt_invoice_failed_1",
        "type": "invoice.payment_failed",
        "data": {"object": {"id": "in_456", "subscription": sub_id}},
    }


def _post_event(client: AsyncClient, event: dict[str, Any], *, signature: str = "valid"):
    """POST an event to the webhook with ``construct_event`` patched."""
    return client.post(
        "/api/webhooks/stripe",
        headers={"stripe-signature": signature},
        content=json.dumps(event).encode(),
    )


def _patch_construct(event: dict[str, Any] | None = None, *, raises: bool = False):
    """Patch ``stripe.Webhook.construct_event`` to return ``event`` or raise."""
    import stripe

    if raises:
        return patch.object(
            stripe.Webhook,
            "construct_event",
            side_effect=stripe.error.SignatureVerificationError(
                "Invalid sig", "sigheader"
            ),
        )
    return patch.object(stripe.Webhook, "construct_event", return_value=event)


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_signature_returns_400(
    client: AsyncClient,
) -> None:
    with _patch_construct(raises=True):
        resp = await _post_event(client, {"id": "evt_x", "type": "test"})
    assert resp.status_code == 400
    assert "Invalid signature" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_missing_signature_header_returns_400(
    client: AsyncClient,
) -> None:
    resp = await client.post(
        "/api/webhooks/stripe", content=b"{}",
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# checkout.session.completed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkout_completed_creates_subscription_and_sets_plan(
    client: AsyncClient,
    db_session: AsyncSession,
    user_alice: User,
    pro_plan: BillingPlan,
) -> None:
    period_end = datetime.now(UTC) + timedelta(days=30)
    event = _checkout_completed_event(user_id=user_alice.id, sub_id="sub_test_1")

    fake_sub = {
        "id": "sub_test_1",
        "status": "active",
        "current_period_start": _ts(period_end - timedelta(days=30)),
        "current_period_end": _ts(period_end),
        "cancel_at_period_end": False,
        "canceled_at": None,
        "items": {"data": [{"price": {"id": pro_plan.stripe_price_id}}]},
    }
    with _patch_construct(event), \
         patch("stripe.Subscription.retrieve", return_value=fake_sub):
        resp = await _post_event(client, event)

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"received": True}

    await db_session.refresh(user_alice)
    assert user_alice.plan_id == pro_plan.id
    assert user_alice.stripe_customer_id == "cus_alice"

    sub_row = (
        await db_session.execute(
            select(Subscription).where(
                Subscription.stripe_subscription_id == "sub_test_1"
            )
        )
    ).scalar_one()
    assert sub_row.status == "active"
    assert sub_row.plan_id == pro_plan.id


# ---------------------------------------------------------------------------
# customer.subscription.updated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscription_updated_syncs_status_and_price(
    client: AsyncClient,
    db_session: AsyncSession,
    user_alice: User,
    pro_plan: BillingPlan,
) -> None:
    now = datetime.now(UTC)
    sub = Subscription(
        user_id=user_alice.id,
        plan_id=pro_plan.id,
        stripe_subscription_id="sub_test_2",
        stripe_price_id=pro_plan.stripe_price_id or "price_test_pro",
        status="active",
        current_period_start=now - timedelta(days=20),
        current_period_end=now + timedelta(days=10),
        cancel_at_period_end=False,
        updated_at=now,
    )
    db_session.add(sub)
    await db_session.commit()

    new_period_end = now + timedelta(days=40)
    event = _subscription_updated_event(
        sub_id="sub_test_2",
        status="active",
        cancel=True,
        price_id=pro_plan.stripe_price_id,
        period_end=new_period_end,
    )
    with _patch_construct(event):
        resp = await _post_event(client, event)
    assert resp.status_code == 200

    await db_session.refresh(sub)
    assert sub.cancel_at_period_end is True
    # SQLite drops timezone info; compare on the wall-clock fields.
    stored = sub.current_period_end
    expected = new_period_end
    assert (stored.year, stored.month, stored.day, stored.hour) == (
        expected.year, expected.month, expected.day, expected.hour,
    )


# ---------------------------------------------------------------------------
# customer.subscription.deleted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscription_deleted_marks_canceled_but_does_not_demote(
    client: AsyncClient,
    db_session: AsyncSession,
    user_alice: User,
    pro_plan: BillingPlan,
    free_plan: BillingPlan,
) -> None:
    now = datetime.now(UTC)
    user_alice.plan_id = pro_plan.id
    sub = Subscription(
        user_id=user_alice.id,
        plan_id=pro_plan.id,
        stripe_subscription_id="sub_test_3",
        stripe_price_id=pro_plan.stripe_price_id or "price_test_pro",
        status="active",
        current_period_start=now - timedelta(days=10),
        current_period_end=now + timedelta(days=20),
        updated_at=now,
    )
    db_session.add(sub)
    await db_session.commit()

    event = _subscription_deleted_event(
        sub_id="sub_test_3", period_end=now + timedelta(days=20)
    )
    with _patch_construct(event):
        resp = await _post_event(client, event)
    assert resp.status_code == 200

    await db_session.refresh(sub)
    await db_session.refresh(user_alice)
    assert sub.status == "canceled"
    # Critical contract: the webhook does NOT demote — that's the
    # lifecycle sweep's job.
    assert user_alice.plan_id == pro_plan.id
    assert user_alice.plan_id != free_plan.id


# ---------------------------------------------------------------------------
# invoice.payment_succeeded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoice_paid_renewal_resets_tokens(
    client: AsyncClient,
    db_session: AsyncSession,
    user_alice: User,
    pro_plan: BillingPlan,
) -> None:
    now = datetime.now(UTC)
    sub = Subscription(
        user_id=user_alice.id,
        plan_id=pro_plan.id,
        stripe_subscription_id="sub_test_4",
        stripe_price_id=pro_plan.stripe_price_id or "price_test_pro",
        status="active",
        current_period_start=now - timedelta(days=30),
        current_period_end=now + timedelta(days=1),
        updated_at=now,
    )
    db_session.add(sub)
    await db_session.commit()

    new_period_end = now + timedelta(days=31)
    event = _invoice_paid_event(
        sub_id="sub_test_4",
        billing_reason="subscription_cycle",
        period_end=new_period_end,
    )
    with _patch_construct(event):
        resp = await _post_event(client, event)
    assert resp.status_code == 200

    await db_session.refresh(user_alice)
    assert user_alice.tokens_used_this_period == 0


@pytest.mark.asyncio
async def test_invoice_paid_subscription_create_does_not_reset(
    client: AsyncClient,
    db_session: AsyncSession,
    user_alice: User,
    pro_plan: BillingPlan,
) -> None:
    now = datetime.now(UTC)
    sub = Subscription(
        user_id=user_alice.id,
        plan_id=pro_plan.id,
        stripe_subscription_id="sub_test_5",
        stripe_price_id=pro_plan.stripe_price_id or "price_test_pro",
        status="active",
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        updated_at=now,
    )
    db_session.add(sub)
    await db_session.commit()
    initial = user_alice.tokens_used_this_period

    event = _invoice_paid_event(
        sub_id="sub_test_5",
        billing_reason="subscription_create",
        period_end=now + timedelta(days=30),
    )
    with _patch_construct(event):
        resp = await _post_event(client, event)
    assert resp.status_code == 200

    await db_session.refresh(user_alice)
    assert user_alice.tokens_used_this_period == initial


# ---------------------------------------------------------------------------
# invoice.payment_failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoice_failed_marks_past_due(
    client: AsyncClient,
    db_session: AsyncSession,
    user_alice: User,
    pro_plan: BillingPlan,
) -> None:
    now = datetime.now(UTC)
    sub = Subscription(
        user_id=user_alice.id,
        plan_id=pro_plan.id,
        stripe_subscription_id="sub_test_6",
        stripe_price_id=pro_plan.stripe_price_id or "price_test_pro",
        status="active",
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        updated_at=now,
    )
    db_session.add(sub)
    await db_session.commit()

    event = _invoice_failed_event(sub_id="sub_test_6")
    with _patch_construct(event):
        resp = await _post_event(client, event)
    assert resp.status_code == 200

    await db_session.refresh(sub)
    assert sub.status == "past_due"


# ---------------------------------------------------------------------------
# Idempotency / unknown events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replayed_event_returns_duplicate(
    client: AsyncClient,
    db_session: AsyncSession,
    user_alice: User,
    pro_plan: BillingPlan,
) -> None:
    period_end = datetime.now(UTC) + timedelta(days=30)
    event = _checkout_completed_event(user_id=user_alice.id, sub_id="sub_idem_1")
    fake_sub = {
        "id": "sub_idem_1",
        "status": "active",
        "current_period_start": _ts(period_end - timedelta(days=30)),
        "current_period_end": _ts(period_end),
        "cancel_at_period_end": False,
        "canceled_at": None,
        "items": {"data": [{"price": {"id": pro_plan.stripe_price_id}}]},
    }
    with _patch_construct(event), \
         patch("stripe.Subscription.retrieve", return_value=fake_sub):
        resp1 = await _post_event(client, event)
        resp2 = await _post_event(client, event)

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp2.json() == {"received": True, "duplicate": True}

    # And there must be exactly one Subscription row for that sub id.
    rows = (
        await db_session.execute(
            select(Subscription).where(
                Subscription.stripe_subscription_id == "sub_idem_1"
            )
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_unknown_event_type_returns_200_and_persists_record(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    event = {
        "id": "evt_unknown_1",
        "type": "customer.tax_id.created",  # not in our handler map
        "data": {"object": {"id": "txi_test"}},
    }
    with _patch_construct(event):
        resp = await _post_event(client, event)
    assert resp.status_code == 200

    record = await db_session.get(StripeWebhookEvent, "evt_unknown_1")
    assert record is not None
    assert record.processed_at is not None

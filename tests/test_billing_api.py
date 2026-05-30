"""Tests for the user-facing billing API.

Exercises the four endpoints in ``api/billing.py``:
- ``GET  /api/billing/plans``
- ``GET  /api/billing/subscription``
- ``POST /api/billing/checkout``
- ``POST /api/billing/portal``

All Stripe SDK calls are mocked. ``billing_enabled`` is toggled via the
config singleton (``settings.reset()`` after env mutation).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import fim_one.db.models  # noqa: F401 — register all models with metadata
from fim_one.db.base import Base
from fim_one.web.app import create_app
from fim_one.db.models import BillingPlan, Subscription, User


TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


# ---------------------------------------------------------------------------
# Stripe configuration / mocking
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _enable_billing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure valid Stripe credentials for every test in this module.

    A handful of tests opt out by calling ``_disable_billing(monkeypatch)``
    after the fact; the rest assume billing is on.
    """
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_billingapi")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_billingapi")

    from fim_one.web.config import settings as cfg
    from fim_one.web.services import stripe_client

    cfg.reset()
    stripe_client.reset_for_testing()


def _disable_billing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
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
    """Set ``system_settings.billing_enabled='true'`` so the gate passes.

    The user-facing billing routes are wrapped in
    :func:`require_billing_enabled`; without this row they all 503.
    Tests that specifically exercise the disabled-Stripe path call
    ``_disable_billing(monkeypatch)`` to drop the env vars (which
    flips the in-handler ``billing_enabled()`` check); the flag
    column stays on so we still reach the endpoint code path.
    """
    from fim_one.db.models import SystemSetting

    db_session.add(SystemSetting(key="billing_enabled", value="true"))
    await db_session.commit()


@pytest_asyncio.fixture()
async def free_plan(db_session: AsyncSession) -> BillingPlan:
    plan = BillingPlan(
        slug="free",
        name="Free",
        monthly_token_quota=100_000,
        sort_order=0,
    )
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
        sort_order=1,
    )
    db_session.add(plan)
    await db_session.commit()
    return plan


@pytest_asyncio.fixture()
async def regular_user(
    db_session: AsyncSession, free_plan: BillingPlan
) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username="alice",
        email="alice@example.com",
        password_hash="hashed",
        plan_id=free_plan.id,
    )
    db_session.add(user)
    await db_session.commit()
    return user


def _create_jwt(user_id: str) -> str:
    from fim_one.web.auth import ALGORITHM, SECRET_KEY

    payload = {
        "sub": user_id,
        "type": "access",
        "exp": datetime.now(UTC) + timedelta(hours=1),
    }
    return pyjwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _auth_headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {_create_jwt(user.id)}"}


@pytest_asyncio.fixture()
async def client(engine, db_session, regular_user, pro_plan):  # noqa: ARG001
    """ASGI test client. Reset price cache between tests to avoid leakage."""
    from fim_one.db import get_session
    from fim_one.web.api.billing import _reset_price_cache

    _reset_price_cache()

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
# /plans
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plans_returns_seeded_plans_with_current_flag(
    client: AsyncClient, regular_user: User, free_plan: BillingPlan, pro_plan: BillingPlan
) -> None:
    fake_price = SimpleNamespace(
        unit_amount=2000,
        currency="usd",
        recurring={"interval": "month"},
    )
    # Price.retrieve runs inside the shared stripe_pricing helper —
    # patch it where it actually lives, not at the legacy billing
    # import site.
    with patch(
        "fim_one.web.services.stripe_pricing.get_stripe",
        return_value=SimpleNamespace(
            Price=SimpleNamespace(retrieve=MagicMock(return_value=fake_price))
        ),
    ):
        resp = await client.get("/api/billing/plans", headers=_auth_headers(regular_user))

    assert resp.status_code == 200
    body = resp.json()
    slugs = [p["slug"] for p in body["plans"]]
    assert "free" in slugs
    assert "pro" in slugs

    free_row = next(p for p in body["plans"] if p["slug"] == "free")
    pro_row = next(p for p in body["plans"] if p["slug"] == "pro")
    assert free_row["current"] is True  # seeded user is on free
    assert pro_row["current"] is False
    assert free_row["price_display"] == "Free"
    assert pro_row["price_display"] == "$20.00 USD/month"


@pytest.mark.asyncio
async def test_plans_returns_503_when_billing_disabled(
    client: AsyncClient, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_billing(monkeypatch)
    resp = await client.get("/api/billing/plans", headers=_auth_headers(regular_user))
    assert resp.status_code == 503
    assert "Billing is not configured" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# /subscription
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscription_returns_null_when_no_sub(
    client: AsyncClient, regular_user: User
) -> None:
    resp = await client.get(
        "/api/billing/subscription", headers=_auth_headers(regular_user)
    )
    assert resp.status_code == 200
    assert resp.json() is None


@pytest.mark.asyncio
async def test_subscription_returns_envelope_when_present(
    client: AsyncClient,
    db_session: AsyncSession,
    regular_user: User,
    pro_plan: BillingPlan,
) -> None:
    now = datetime.now(UTC)
    sub = Subscription(
        user_id=regular_user.id,
        plan_id=pro_plan.id,
        stripe_subscription_id="sub_demo_123",
        stripe_price_id=pro_plan.stripe_price_id or "price_test_pro",
        status="active",
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        cancel_at_period_end=False,
        updated_at=now,
    )
    db_session.add(sub)
    await db_session.commit()

    resp = await client.get(
        "/api/billing/subscription", headers=_auth_headers(regular_user)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["plan_slug"] == "pro"
    assert body["status"] == "active"
    assert body["cancel_at_period_end"] is False
    assert body["stripe_subscription_id"] == "sub_demo_123"


# ---------------------------------------------------------------------------
# /checkout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkout_creates_customer_and_returns_url(
    client: AsyncClient,
    db_session: AsyncSession,
    regular_user: User,
    pro_plan: BillingPlan,
) -> None:
    customer_create = MagicMock(
        return_value=SimpleNamespace(id="cus_alice", email="alice@example.com")
    )
    session_create = MagicMock(
        return_value=SimpleNamespace(url="https://checkout.stripe.com/c/test")
    )
    fake_stripe = SimpleNamespace(
        Customer=SimpleNamespace(create=customer_create),
        checkout=SimpleNamespace(
            Session=SimpleNamespace(create=session_create)
        ),
    )
    with patch("fim_one.web.api.billing.get_stripe", return_value=fake_stripe):
        resp = await client.post(
            "/api/billing/checkout",
            headers=_auth_headers(regular_user),
            json={"plan_slug": "pro"},
        )

    assert resp.status_code == 200
    assert resp.json()["url"] == "https://checkout.stripe.com/c/test"
    customer_create.assert_called_once()
    session_create.assert_called_once()
    kwargs = session_create.call_args.kwargs
    assert kwargs["mode"] == "subscription"
    assert kwargs["line_items"] == [
        {"price": pro_plan.stripe_price_id, "quantity": 1}
    ]
    assert kwargs["client_reference_id"] == regular_user.id

    await db_session.refresh(regular_user)
    assert regular_user.stripe_customer_id == "cus_alice"


@pytest.mark.asyncio
async def test_checkout_skips_customer_create_when_already_present(
    client: AsyncClient,
    db_session: AsyncSession,
    regular_user: User,
    pro_plan: BillingPlan,  # noqa: ARG001 — fixture pulls plan into the DB
) -> None:
    regular_user.stripe_customer_id = "cus_existing"
    await db_session.commit()

    customer_create = MagicMock()
    session_create = MagicMock(
        return_value=SimpleNamespace(url="https://checkout.stripe.com/c/test")
    )
    fake_stripe = SimpleNamespace(
        Customer=SimpleNamespace(create=customer_create),
        checkout=SimpleNamespace(
            Session=SimpleNamespace(create=session_create)
        ),
    )
    with patch("fim_one.web.api.billing.get_stripe", return_value=fake_stripe):
        resp = await client.post(
            "/api/billing/checkout",
            headers=_auth_headers(regular_user),
            json={"plan_slug": "pro"},
        )
    assert resp.status_code == 200
    customer_create.assert_not_called()
    session_create.assert_called_once()
    assert session_create.call_args.kwargs["customer"] == "cus_existing"


@pytest.mark.asyncio
async def test_checkout_rejects_unknown_plan(
    client: AsyncClient, regular_user: User
) -> None:
    resp = await client.post(
        "/api/billing/checkout",
        headers=_auth_headers(regular_user),
        json={"plan_slug": "enterprise"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_checkout_rejects_free_plan_with_null_price(
    client: AsyncClient, regular_user: User, free_plan: BillingPlan  # noqa: ARG001
) -> None:
    resp = await client.post(
        "/api/billing/checkout",
        headers=_auth_headers(regular_user),
        json={"plan_slug": "free"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /portal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_portal_returns_400_when_no_customer(
    client: AsyncClient, regular_user: User
) -> None:
    resp = await client.post(
        "/api/billing/portal", headers=_auth_headers(regular_user)
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_portal_returns_url_when_customer_exists(
    client: AsyncClient,
    db_session: AsyncSession,
    regular_user: User,
) -> None:
    regular_user.stripe_customer_id = "cus_alice"
    await db_session.commit()

    portal_create = MagicMock(
        return_value=SimpleNamespace(url="https://billing.stripe.com/p/test")
    )
    fake_stripe = SimpleNamespace(
        billing_portal=SimpleNamespace(
            Session=SimpleNamespace(create=portal_create)
        ),
    )
    with patch("fim_one.web.api.billing.get_stripe", return_value=fake_stripe):
        resp = await client.post(
            "/api/billing/portal", headers=_auth_headers(regular_user)
        )
    assert resp.status_code == 200
    assert resp.json()["url"] == "https://billing.stripe.com/p/test"
    portal_create.assert_called_once_with(
        customer="cus_alice",
        return_url="http://localhost:3000/settings?tab=billing",
    )

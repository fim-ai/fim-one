"""Tests for the admin billing management API.

Exercises the admin-only CRUD + read endpoints in ``api/admin.py``:

- ``GET    /api/admin/billing/plans``
- ``POST   /api/admin/billing/plans``
- ``GET    /api/admin/billing/plans/{plan_id}``
- ``PATCH  /api/admin/billing/plans/{plan_id}``
- ``DELETE /api/admin/billing/plans/{plan_id}``
- ``GET    /api/admin/billing/subscriptions``
- ``GET    /api/admin/billing/subscriptions/{sub_id}``

Auth, persistence, soft-delete semantics, immutability of ``slug``, and
filter/search behaviour are all covered.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

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
# Billing-config fixture (admin endpoints don't actually need Stripe but
# create_app may register webhook routes that demand env vars).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _enable_billing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_adminbilling")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_adminbilling")
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

    Every admin-billing endpoint is now wrapped in
    :func:`require_billing_enabled`; without this row the routes 503.
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
        features_json={"features": ["100k tokens / month"]},
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
        features_json={"features": ["5M tokens / month", "Priority support"]},
    )
    db_session.add(plan)
    await db_session.commit()
    return plan


@pytest_asyncio.fixture()
async def admin_user(db_session: AsyncSession) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username="root_admin",
        email="root@example.com",
        password_hash="hashed",
        is_admin=True,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest_asyncio.fixture()
async def regular_user(db_session: AsyncSession) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username="alice",
        email="alice@example.com",
        password_hash="hashed",
        is_admin=False,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest_asyncio.fixture()
async def bob_user(db_session: AsyncSession) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username="bob",
        email="bob@example.com",
        password_hash="hashed",
        is_admin=False,
        is_active=True,
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
async def client(engine, db_session, admin_user, regular_user, bob_user, free_plan, pro_plan):  # noqa: ARG001
    """ASGI test client wired up against the ephemeral SQLite session."""
    from fim_one.db import get_session

    @asynccontextmanager
    async def _noop_lifespan(app):  # type: ignore[no-untyped-def]
        yield

    with patch("fim_one.web.app.lifespan", _noop_lifespan):
        app = create_app()

    async def _override_session():  # type: ignore[no-untyped-def]
        yield db_session

    app.dependency_overrides[get_session] = _override_session

    @asynccontextmanager
    async def _mock_create_session():  # type: ignore[no-untyped-def]
        yield db_session

    with patch("fim_one.db.create_session", _mock_create_session), \
         patch("fim_one.db.engine.create_session", _mock_create_session):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


class TestAuthGating:
    @pytest.mark.asyncio
    async def test_list_plans_rejects_anonymous(self, client: AsyncClient) -> None:
        resp = await client.get("/api/admin/billing/plans")
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_list_plans_rejects_non_admin(
        self, client: AsyncClient, regular_user: User
    ) -> None:
        resp = await client.get(
            "/api/admin/billing/plans", headers=_auth_headers(regular_user)
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_create_plan_rejects_non_admin(
        self, client: AsyncClient, regular_user: User
    ) -> None:
        resp = await client.post(
            "/api/admin/billing/plans",
            headers=_auth_headers(regular_user),
            json={"slug": "team", "name": "Team", "monthly_token_quota": 1000},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_subscriptions_list_rejects_non_admin(
        self, client: AsyncClient, regular_user: User
    ) -> None:
        resp = await client.get(
            "/api/admin/billing/subscriptions", headers=_auth_headers(regular_user)
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Plans — list / get
# ---------------------------------------------------------------------------


class TestPlansListAndGet:
    @pytest.mark.asyncio
    async def test_list_returns_all_plans(
        self,
        client: AsyncClient,
        admin_user: User,
        free_plan: BillingPlan,
        pro_plan: BillingPlan,  # noqa: ARG002
    ) -> None:
        resp = await client.get(
            "/api/admin/billing/plans", headers=_auth_headers(admin_user)
        )
        assert resp.status_code == 200
        body = resp.json()
        slugs = [p["slug"] for p in body]
        assert "free" in slugs
        assert "pro" in slugs

    @pytest.mark.asyncio
    async def test_list_includes_active_subscription_count(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        admin_user: User,
        regular_user: User,
        pro_plan: BillingPlan,
    ) -> None:
        now = datetime.now(UTC)
        sub = Subscription(
            user_id=regular_user.id,
            plan_id=pro_plan.id,
            stripe_subscription_id="sub_count_1",
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
            "/api/admin/billing/plans", headers=_auth_headers(admin_user)
        )
        assert resp.status_code == 200
        pro_row = next(p for p in resp.json() if p["slug"] == "pro")
        assert pro_row["active_subscription_count"] == 1

    @pytest.mark.asyncio
    async def test_get_one_plan(
        self, client: AsyncClient, admin_user: User, pro_plan: BillingPlan
    ) -> None:
        resp = await client.get(
            f"/api/admin/billing/plans/{pro_plan.id}",
            headers=_auth_headers(admin_user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["slug"] == "pro"
        assert body["stripe_price_id"] == "price_test_pro"
        assert "Priority support" in body["features"]

    @pytest.mark.asyncio
    async def test_get_404_for_unknown_plan(
        self, client: AsyncClient, admin_user: User
    ) -> None:
        resp = await client.get(
            "/api/admin/billing/plans/999999",
            headers=_auth_headers(admin_user),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Plans — create
# ---------------------------------------------------------------------------


class TestPlansCreate:
    @pytest.mark.asyncio
    async def test_create_plan_with_valid_data(
        self, client: AsyncClient, admin_user: User
    ) -> None:
        resp = await client.post(
            "/api/admin/billing/plans",
            headers=_auth_headers(admin_user),
            json={
                "slug": "team",
                "name": "Team",
                "monthly_token_quota": 10_000_000,
                "stripe_price_id": "price_team_xyz",
                "description": "For squads",
                "features": ["10M tokens / month", "Shared workspaces"],
                "sort_order": 5,
                "is_active": True,
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["slug"] == "team"
        assert body["monthly_token_quota"] == 10_000_000
        assert body["stripe_price_id"] == "price_team_xyz"
        assert "Shared workspaces" in body["features"]

    @pytest.mark.asyncio
    async def test_create_plan_rejects_duplicate_slug(
        self, client: AsyncClient, admin_user: User, pro_plan: BillingPlan  # noqa: ARG002
    ) -> None:
        resp = await client.post(
            "/api/admin/billing/plans",
            headers=_auth_headers(admin_user),
            json={
                "slug": "pro",
                "name": "Pro Duplicate",
                "monthly_token_quota": 1,
            },
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body.get("error_code") == "billing_plan_slug_taken"

    @pytest.mark.asyncio
    async def test_create_plan_rejects_invalid_slug(
        self, client: AsyncClient, admin_user: User
    ) -> None:
        resp = await client.post(
            "/api/admin/billing/plans",
            headers=_auth_headers(admin_user),
            json={
                "slug": "Has Space!",
                "name": "Bad",
                "monthly_token_quota": 1,
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_plan_rejects_duplicate_stripe_price(
        self, client: AsyncClient, admin_user: User, pro_plan: BillingPlan  # noqa: ARG002
    ) -> None:
        resp = await client.post(
            "/api/admin/billing/plans",
            headers=_auth_headers(admin_user),
            json={
                "slug": "pro2",
                "name": "Pro Two",
                "monthly_token_quota": 1,
                "stripe_price_id": "price_test_pro",
            },
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Plans — update
# ---------------------------------------------------------------------------


class TestPlansUpdate:
    @pytest.mark.asyncio
    async def test_update_mutable_fields(
        self,
        client: AsyncClient,
        admin_user: User,
        pro_plan: BillingPlan,
    ) -> None:
        resp = await client.patch(
            f"/api/admin/billing/plans/{pro_plan.id}",
            headers=_auth_headers(admin_user),
            json={
                "name": "Pro Plus",
                "monthly_token_quota": 10_000_000,
                "stripe_price_id": "price_test_pro_v2",
                "sort_order": 9,
                "features": ["10M tokens", "Priority support", "SSO"],
                "price_cents": 2999,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Pro Plus"
        assert body["monthly_token_quota"] == 10_000_000
        assert body["stripe_price_id"] == "price_test_pro_v2"
        assert body["sort_order"] == 9
        assert "SSO" in body["features"]
        assert body["price_cents"] == 2999

    @pytest.mark.asyncio
    async def test_update_rejects_slug_change_attempt(
        self, client: AsyncClient, admin_user: User, pro_plan: BillingPlan
    ) -> None:
        resp = await client.patch(
            f"/api/admin/billing/plans/{pro_plan.id}",
            headers=_auth_headers(admin_user),
            json={"slug": "pro_renamed"},
        )
        # Pydantic drops unknown fields silently; verify the slug was NOT
        # mutated and the response still says "pro".
        assert resp.status_code == 200
        body = resp.json()
        assert body["slug"] == "pro"

    @pytest.mark.asyncio
    async def test_update_toggles_is_active(
        self, client: AsyncClient, admin_user: User, pro_plan: BillingPlan
    ) -> None:
        resp = await client.patch(
            f"/api/admin/billing/plans/{pro_plan.id}",
            headers=_auth_headers(admin_user),
            json={"is_active": False},
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    @pytest.mark.asyncio
    async def test_update_rejects_disabling_default_plan(
        self, client: AsyncClient, admin_user: User, free_plan: BillingPlan
    ) -> None:
        resp = await client.patch(
            f"/api/admin/billing/plans/{free_plan.id}",
            headers=_auth_headers(admin_user),
            json={"is_active": False},
        )
        assert resp.status_code == 409
        assert resp.json().get("error_code") == "billing_plan_is_default"

    @pytest.mark.asyncio
    async def test_update_404_for_unknown_plan(
        self, client: AsyncClient, admin_user: User
    ) -> None:
        resp = await client.patch(
            "/api/admin/billing/plans/999999",
            headers=_auth_headers(admin_user),
            json={"name": "Ghost"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_rejects_duplicate_stripe_price(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        admin_user: User,
        pro_plan: BillingPlan,
    ) -> None:
        extra = BillingPlan(
            slug="team",
            name="Team",
            monthly_token_quota=10_000_000,
            stripe_price_id="price_test_team",
        )
        db_session.add(extra)
        await db_session.commit()
        resp = await client.patch(
            f"/api/admin/billing/plans/{pro_plan.id}",
            headers=_auth_headers(admin_user),
            json={"stripe_price_id": extra.stripe_price_id},
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Plans — delete (soft)
# ---------------------------------------------------------------------------


class TestPlansDelete:
    @pytest.mark.asyncio
    async def test_delete_soft_deletes_when_no_active_subs(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        admin_user: User,
        pro_plan: BillingPlan,
    ) -> None:
        resp = await client.delete(
            f"/api/admin/billing/plans/{pro_plan.id}",
            headers=_auth_headers(admin_user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_active"] is False

        # Row still exists in the DB (soft delete, not hard delete).
        from sqlalchemy import select

        result = await db_session.execute(
            select(BillingPlan).where(BillingPlan.id == pro_plan.id)
        )
        row = result.scalar_one()
        await db_session.refresh(row)
        assert row.is_active is False

    @pytest.mark.asyncio
    async def test_delete_rejects_default_plan(
        self,
        client: AsyncClient,
        admin_user: User,
        free_plan: BillingPlan,
    ) -> None:
        resp = await client.delete(
            f"/api/admin/billing/plans/{free_plan.id}",
            headers=_auth_headers(admin_user),
        )
        assert resp.status_code == 409
        assert resp.json().get("error_code") == "billing_plan_is_default"

    @pytest.mark.asyncio
    async def test_delete_rejects_when_active_subs_exist(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        admin_user: User,
        regular_user: User,
        pro_plan: BillingPlan,
    ) -> None:
        now = datetime.now(UTC)
        sub = Subscription(
            user_id=regular_user.id,
            plan_id=pro_plan.id,
            stripe_subscription_id="sub_blocking_1",
            stripe_price_id=pro_plan.stripe_price_id or "price_test_pro",
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
            cancel_at_period_end=False,
            updated_at=now,
        )
        db_session.add(sub)
        await db_session.commit()

        resp = await client.delete(
            f"/api/admin/billing/plans/{pro_plan.id}",
            headers=_auth_headers(admin_user),
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body.get("error_code") == "billing_plan_has_active_subscriptions"

        # Plan must remain active — refusal must be all-or-nothing.
        await db_session.refresh(pro_plan)
        assert pro_plan.is_active is True

    @pytest.mark.asyncio
    async def test_delete_allows_when_subs_all_canceled(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        admin_user: User,
        regular_user: User,
        pro_plan: BillingPlan,
    ) -> None:
        now = datetime.now(UTC)
        sub = Subscription(
            user_id=regular_user.id,
            plan_id=pro_plan.id,
            stripe_subscription_id="sub_already_dead",
            stripe_price_id=pro_plan.stripe_price_id or "price_test_pro",
            status="canceled",
            current_period_start=now - timedelta(days=60),
            current_period_end=now - timedelta(days=30),
            cancel_at_period_end=True,
            canceled_at=now - timedelta(days=30),
            updated_at=now,
        )
        db_session.add(sub)
        await db_session.commit()

        resp = await client.delete(
            f"/api/admin/billing/plans/{pro_plan.id}",
            headers=_auth_headers(admin_user),
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    @pytest.mark.asyncio
    async def test_delete_404_for_unknown_plan(
        self, client: AsyncClient, admin_user: User
    ) -> None:
        resp = await client.delete(
            "/api/admin/billing/plans/999999",
            headers=_auth_headers(admin_user),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Subscriptions — list filters + detail
# ---------------------------------------------------------------------------


class TestSubscriptions:
    @pytest_asyncio.fixture()
    async def seeded_subs(
        self,
        db_session: AsyncSession,
        regular_user: User,
        bob_user: User,
        pro_plan: BillingPlan,
        free_plan: BillingPlan,
    ) -> dict[str, Subscription]:
        now = datetime.now(UTC)
        alice_active = Subscription(
            user_id=regular_user.id,
            plan_id=pro_plan.id,
            stripe_subscription_id="sub_alice_active",
            stripe_price_id=pro_plan.stripe_price_id or "price_test_pro",
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
            cancel_at_period_end=False,
            updated_at=now,
        )
        bob_canceled = Subscription(
            user_id=bob_user.id,
            plan_id=free_plan.id,
            stripe_subscription_id="sub_bob_canceled",
            stripe_price_id="price_free_legacy",
            status="canceled",
            current_period_start=now - timedelta(days=60),
            current_period_end=now - timedelta(days=30),
            cancel_at_period_end=True,
            canceled_at=now - timedelta(days=30),
            updated_at=now - timedelta(days=30),
        )
        db_session.add_all([alice_active, bob_canceled])
        await db_session.commit()
        return {"alice": alice_active, "bob": bob_canceled}

    @pytest.mark.asyncio
    async def test_list_returns_all_subscriptions(
        self,
        client: AsyncClient,
        admin_user: User,
        seeded_subs: dict[str, Subscription],  # noqa: ARG002
    ) -> None:
        resp = await client.get(
            "/api/admin/billing/subscriptions", headers=_auth_headers(admin_user)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert {item["stripe_subscription_id"] for item in body["items"]} == {
            "sub_alice_active",
            "sub_bob_canceled",
        }

    @pytest.mark.asyncio
    async def test_list_filters_by_status(
        self,
        client: AsyncClient,
        admin_user: User,
        seeded_subs: dict[str, Subscription],  # noqa: ARG002
    ) -> None:
        resp = await client.get(
            "/api/admin/billing/subscriptions?status=active",
            headers=_auth_headers(admin_user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["status"] == "active"

    @pytest.mark.asyncio
    async def test_list_filters_by_plan_slug(
        self,
        client: AsyncClient,
        admin_user: User,
        seeded_subs: dict[str, Subscription],  # noqa: ARG002
    ) -> None:
        resp = await client.get(
            "/api/admin/billing/subscriptions?plan_slug=pro",
            headers=_auth_headers(admin_user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["plan_slug"] == "pro"

    @pytest.mark.asyncio
    async def test_list_filters_by_search_email(
        self,
        client: AsyncClient,
        admin_user: User,
        seeded_subs: dict[str, Subscription],  # noqa: ARG002
    ) -> None:
        resp = await client.get(
            "/api/admin/billing/subscriptions?search=bob",
            headers=_auth_headers(admin_user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["user_email"] == "bob@example.com"

    @pytest.mark.asyncio
    async def test_list_filters_compose(
        self,
        client: AsyncClient,
        admin_user: User,
        seeded_subs: dict[str, Subscription],  # noqa: ARG002
    ) -> None:
        # alice + free => zero matches
        resp = await client.get(
            "/api/admin/billing/subscriptions?plan_slug=free&search=alice",
            headers=_auth_headers(admin_user),
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    @pytest.mark.asyncio
    async def test_list_pagination(
        self,
        client: AsyncClient,
        admin_user: User,
        seeded_subs: dict[str, Subscription],  # noqa: ARG002
    ) -> None:
        resp = await client.get(
            "/api/admin/billing/subscriptions?limit=1&offset=0",
            headers=_auth_headers(admin_user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert len(body["items"]) == 1
        assert body["limit"] == 1
        assert body["offset"] == 0

    @pytest.mark.asyncio
    async def test_get_one_subscription(
        self,
        client: AsyncClient,
        admin_user: User,
        seeded_subs: dict[str, Subscription],
    ) -> None:
        sub = seeded_subs["alice"]
        resp = await client.get(
            f"/api/admin/billing/subscriptions/{sub.id}",
            headers=_auth_headers(admin_user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["stripe_subscription_id"] == "sub_alice_active"
        assert body["user_email"] == "alice@example.com"
        assert body["plan_slug"] == "pro"

    @pytest.mark.asyncio
    async def test_get_subscription_404(
        self, client: AsyncClient, admin_user: User
    ) -> None:
        resp = await client.get(
            "/api/admin/billing/subscriptions/999999",
            headers=_auth_headers(admin_user),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# default_token_quota → Free plan quota sync (Scheme A)
# ---------------------------------------------------------------------------


class TestDefaultTokenQuotaSyncsFreePlan:
    """``PATCH /api/admin/settings`` with ``default_token_quota`` must
    keep the Free plan's ``monthly_token_quota`` in sync — Scheme A
    treats the Free plan as the canonical "default" for unsubscribed
    users, so a single admin write propagates to both knobs."""

    @pytest.mark.asyncio
    async def test_patch_default_quota_updates_free_plan_quota(
        self,
        client: AsyncClient,
        admin_user: User,
        free_plan: BillingPlan,
        db_session: AsyncSession,
    ) -> None:
        from sqlalchemy import select

        resp = await client.patch(
            "/api/admin/settings",
            headers=_auth_headers(admin_user),
            json={"default_token_quota": 2_000_000},
        )
        assert resp.status_code == 200
        # System setting reflects the new value.
        assert resp.json()["default_token_quota"] == 2_000_000

        # Free plan's quota was updated in the same request.
        db_session.expire_all()
        row = (
            await db_session.execute(
                select(BillingPlan).where(BillingPlan.slug == "free")
            )
        ).scalar_one()
        assert row.monthly_token_quota == 2_000_000

    @pytest.mark.asyncio
    async def test_patch_other_setting_does_not_touch_free_plan(
        self,
        client: AsyncClient,
        admin_user: User,
        free_plan: BillingPlan,
        db_session: AsyncSession,
    ) -> None:
        from sqlalchemy import select

        original_quota = free_plan.monthly_token_quota

        resp = await client.patch(
            "/api/admin/settings",
            headers=_auth_headers(admin_user),
            json={"announcement_text": "hello"},
        )
        assert resp.status_code == 200

        db_session.expire_all()
        row = (
            await db_session.execute(
                select(BillingPlan).where(BillingPlan.slug == "free")
            )
        ).scalar_one()
        assert row.monthly_token_quota == original_quota


# ---------------------------------------------------------------------------
# Free plan PATCH guard — quota field is read-only on slug='free'
# ---------------------------------------------------------------------------


class TestFreePlanQuotaLock:
    """The Free plan's ``monthly_token_quota`` is sourced from
    ``system_settings.default_token_quota`` (kept in sync from the
    settings PATCH handler). A ``PATCH /admin/billing/plans/{free.id}``
    with ``monthly_token_quota`` in the body must be a quiet no-op
    rather than a 400, so curl users get forgiveness.
    """

    @pytest.mark.asyncio
    async def test_patch_free_plan_ignores_quota_field(
        self,
        client: AsyncClient,
        admin_user: User,
        free_plan: BillingPlan,
        db_session: AsyncSession,
    ) -> None:
        from sqlalchemy import select as _select

        original_quota = free_plan.monthly_token_quota
        resp = await client.patch(
            f"/api/admin/billing/plans/{free_plan.id}",
            headers=_auth_headers(admin_user),
            json={
                "monthly_token_quota": 99_999_999,
                "name": "Free Tier (Renamed)",
            },
        )
        # Other fields still apply, request returns 200.
        assert resp.status_code == 200
        body = resp.json()
        assert body["monthly_token_quota"] == original_quota
        assert body["name"] == "Free Tier (Renamed)"

        # And the row really is unchanged in the database. Rebind to
        # the same session via a fresh query so we read what the
        # endpoint committed, not the in-memory instance.
        row = (
            await db_session.execute(
                _select(BillingPlan).where(BillingPlan.id == free_plan.id)
            )
        ).scalar_one()
        await db_session.refresh(row)
        assert row.monthly_token_quota == original_quota

    @pytest.mark.asyncio
    async def test_patch_pro_plan_quota_still_works(
        self,
        client: AsyncClient,
        admin_user: User,
        pro_plan: BillingPlan,
        db_session: AsyncSession,  # noqa: ARG002
    ) -> None:
        # The lock applies to slug='free' only; Pro is editable.
        resp = await client.patch(
            f"/api/admin/billing/plans/{pro_plan.id}",
            headers=_auth_headers(admin_user),
            json={"monthly_token_quota": 7_500_000},
        )
        assert resp.status_code == 200
        assert resp.json()["monthly_token_quota"] == 7_500_000

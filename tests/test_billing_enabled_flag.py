"""Tests for the ``billing_enabled`` feature flag pipeline.

Covers the activation/deactivation flow, the front-loaded data
guarantees, the API gating, and the quota chain's flag-aware skip
behaviour.

The "front-loaded + switch-only" architectural rule asserts:

- Activation seeds plans, sets the default_plan_id pointer, and
  backfills user plan bindings exactly once. Re-running activation on
  an already-activated install touches no rows.
- Deactivation is a pure flag flip — no rows deleted, no plan_ids
  cleared, no plans soft-deleted.
- All billing endpoints (user-facing, webhook, admin CRUD) return 503
  when the flag is off.
- The quota chain skips the plan tier when the flag is off, so private
  deployments without Stripe rely purely on the legacy
  ``default_token_quota`` setting.
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import fim_one.db.models  # noqa: F401 — register all models with metadata
from fim_one.db.base import Base
from fim_one.web.app import create_app
from fim_one.db.models import BillingPlan, SystemSetting, User
from fim_one.web.services.billing_flag import (
    SETTING_BILLING_ENABLED,
    SETTING_DEFAULT_PLAN_ID,
    activate_billing,
    deactivate_billing,
    is_billing_enabled,
)
from fim_one.web.services.quota_enforcer import (
    get_user_quota,
    get_user_quota_by_id,
)

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture()
def stripe_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure Stripe env so activate_billing's gate passes."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_billingflag")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_billingflag")
    from fim_one.web.config import settings as cfg
    from fim_one.web.services import stripe_client

    cfg.reset()
    stripe_client.reset_for_testing()


# ---------------------------------------------------------------------------
# Activation — idempotent seed + backfill
# ---------------------------------------------------------------------------


class TestActivation:
    @pytest.mark.asyncio
    async def test_activation_without_stripe_env_raises_400(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No env set → activate_billing raises 400 *and* leaves flag off.
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
        from fim_one.web.config import settings as cfg
        from fim_one.web.services import stripe_client

        cfg.reset()
        stripe_client.reset_for_testing()

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await activate_billing(session)
        assert exc.value.status_code == 400

        assert await is_billing_enabled(session) is False

    @pytest.mark.asyncio
    async def test_activation_seeds_plans_backfills_users(
        self, session: AsyncSession, stripe_env: None
    ) -> None:
        # User exists with NULL plan_id pre-activation.
        u = User(
            id=str(uuid.uuid4()),
            email="alice@example.com",
            password_hash="x",
        )
        session.add(u)
        await session.commit()

        result = await activate_billing(session)

        assert result["plans_seeded"] == 2
        assert result["users_backfilled"] == 1
        assert result["billing_enabled"] is True

        # Free + Pro now exist.
        plans = (await session.execute(select(BillingPlan))).scalars().all()
        slugs = {p.slug for p in plans}
        assert slugs == {"free", "pro"}

        # User now bound to Free.
        await session.refresh(u)
        free_id = next(p.id for p in plans if p.slug == "free")
        assert u.plan_id == free_id

        # default_plan_id pointer is set.
        ptr = (
            await session.execute(
                select(SystemSetting.value).where(
                    SystemSetting.key == SETTING_DEFAULT_PLAN_ID
                )
            )
        ).scalar_one_or_none()
        assert ptr == str(free_id)

        # Flag is on.
        assert await is_billing_enabled(session) is True

    @pytest.mark.asyncio
    async def test_re_activation_is_idempotent(
        self, session: AsyncSession, stripe_env: None
    ) -> None:
        # First activation seeds + backfills.
        await activate_billing(session)
        first_count = (
            (await session.execute(select(BillingPlan))).scalars().all()
        )

        # Second activation — should be a no-op.
        result = await activate_billing(session)

        assert result["plans_seeded"] == 0
        assert result["users_backfilled"] == 0
        # Plan rows untouched.
        second_count = (
            (await session.execute(select(BillingPlan))).scalars().all()
        )
        assert len(first_count) == len(second_count)

    @pytest.mark.asyncio
    async def test_activation_syncs_default_token_quota_to_free(
        self, session: AsyncSession, stripe_env: None
    ) -> None:
        # Legacy install: default_token_quota was previously set via the
        # admin UI. Activation should seed Free directly from it.
        session.add(SystemSetting(key="default_token_quota", value="250000"))
        await session.commit()

        await activate_billing(session)

        free = (
            await session.execute(
                select(BillingPlan).where(BillingPlan.slug == "free")
            )
        ).scalar_one()
        assert free.monthly_token_quota == 250_000

    @pytest.mark.asyncio
    async def test_activation_self_heals_dangling_default_pointer(
        self, session: AsyncSession, stripe_env: None
    ) -> None:
        # Operator left a stale pointer behind — e.g. an old Free row
        # that was hard-deleted out-of-band, or a soft-deleted row that
        # the pointer was never refreshed past. Re-activation should
        # detect the dangling target and re-bind the pointer to the
        # current Free row.
        session.add(
            SystemSetting(key=SETTING_DEFAULT_PLAN_ID, value="9999")
        )
        await session.commit()

        await activate_billing(session)

        free = (
            await session.execute(
                select(BillingPlan).where(BillingPlan.slug == "free")
            )
        ).scalar_one()
        ptr = (
            await session.execute(
                select(SystemSetting.value).where(
                    SystemSetting.key == SETTING_DEFAULT_PLAN_ID
                )
            )
        ).scalar_one()
        assert ptr == str(free.id)

    @pytest.mark.asyncio
    async def test_activation_self_heals_pointer_to_inactive_plan(
        self, session: AsyncSession, stripe_env: None
    ) -> None:
        # First activation seeds Free + sets pointer.
        await activate_billing(session)
        free = (
            await session.execute(
                select(BillingPlan).where(BillingPlan.slug == "free")
            )
        ).scalar_one()

        # Admin soft-deletes Free and recreates it under a new id (the
        # only path that surfaces a stale pointer in practice).
        free.is_active = False
        new_free = BillingPlan(
            slug="free-replacement",
            name="Free (recreated)",
            stripe_price_id=None,
            monthly_token_quota=100_000,
            description="recreated",
            features_json={},
            sort_order=0,
            is_active=True,
        )
        session.add(new_free)
        await session.flush()
        # Repoint the slug so the seed loop finds the new row.
        free.slug = "free-old"
        new_free.slug = "free"
        await session.commit()

        # Re-activate: the pointer still references the inactive row →
        # self-heal kicks in and rebinds to the new Free.
        await activate_billing(session)

        ptr = (
            await session.execute(
                select(SystemSetting.value).where(
                    SystemSetting.key == SETTING_DEFAULT_PLAN_ID
                )
            )
        ).scalar_one()
        assert ptr == str(new_free.id)


# ---------------------------------------------------------------------------
# Deactivation — pure flag flip
# ---------------------------------------------------------------------------


class TestDeactivation:
    @pytest.mark.asyncio
    async def test_deactivation_does_not_touch_data(
        self, session: AsyncSession, stripe_env: None
    ) -> None:
        u = User(
            id=str(uuid.uuid4()),
            email="alice@example.com",
            password_hash="x",
        )
        session.add(u)
        await session.commit()

        await activate_billing(session)
        await session.refresh(u)
        plan_id_before = u.plan_id
        plans_before = len(
            (await session.execute(select(BillingPlan))).scalars().all()
        )

        result = await deactivate_billing(session)

        assert result["billing_enabled"] is False
        assert await is_billing_enabled(session) is False

        # Data invariant: plans and users are unchanged.
        plans_after = len(
            (await session.execute(select(BillingPlan))).scalars().all()
        )
        assert plans_before == plans_after

        await session.refresh(u)
        assert u.plan_id == plan_id_before

    @pytest.mark.asyncio
    async def test_reactivate_after_deactivate_is_noop(
        self, session: AsyncSession, stripe_env: None
    ) -> None:
        await activate_billing(session)
        await deactivate_billing(session)

        # Re-activation: data already seeded → no-op.
        result = await activate_billing(session)
        assert result["plans_seeded"] == 0
        assert result["users_backfilled"] == 0
        assert result["billing_enabled"] is True


# ---------------------------------------------------------------------------
# Quota chain — flag-aware plan skip
# ---------------------------------------------------------------------------


class TestQuotaChain:
    @pytest.mark.asyncio
    async def test_plan_tier_used_when_flag_on(
        self, session: AsyncSession, stripe_env: None
    ) -> None:
        await activate_billing(session)
        free = (
            await session.execute(
                select(BillingPlan).where(BillingPlan.slug == "free")
            )
        ).scalar_one()

        u = User(
            id=str(uuid.uuid4()),
            email="alice@example.com",
            password_hash="x",
            plan_id=free.id,
        )
        session.add(u)
        await session.commit()

        # Flag is on, plan tier is honoured.
        quota = await get_user_quota(u, session)
        assert quota == free.monthly_token_quota

        # And the by-id variant agrees.
        quota_id = await get_user_quota_by_id(u.id, session)
        assert quota_id == free.monthly_token_quota

    @pytest.mark.asyncio
    async def test_plan_tier_skipped_when_flag_off(
        self, session: AsyncSession, stripe_env: None
    ) -> None:
        await activate_billing(session)
        free = (
            await session.execute(
                select(BillingPlan).where(BillingPlan.slug == "free")
            )
        ).scalar_one()

        u = User(
            id=str(uuid.uuid4()),
            email="bob@example.com",
            password_hash="x",
            plan_id=free.id,
        )
        session.add(u)
        # default_token_quota seeded as a legacy fallback.
        session.add(SystemSetting(key="default_token_quota", value="42"))
        await session.commit()

        await deactivate_billing(session)

        # Plan tier skipped → defensive default takes over.
        quota = await get_user_quota(u, session)
        assert quota == 42

        quota_id = await get_user_quota_by_id(u.id, session)
        assert quota_id == 42

    @pytest.mark.asyncio
    async def test_unlimited_when_flag_off_and_no_default(
        self, session: AsyncSession, stripe_env: None
    ) -> None:
        await activate_billing(session)
        free = (
            await session.execute(
                select(BillingPlan).where(BillingPlan.slug == "free")
            )
        ).scalar_one()

        u = User(
            id=str(uuid.uuid4()),
            email="carol@example.com",
            password_hash="x",
            plan_id=free.id,
        )
        session.add(u)
        await session.commit()
        await deactivate_billing(session)

        # No default_token_quota row → None (unlimited).
        quota = await get_user_quota(u, session)
        assert quota is None

    @pytest.mark.asyncio
    async def test_user_override_still_wins_when_flag_off(
        self, session: AsyncSession, stripe_env: None
    ) -> None:
        u = User(
            id=str(uuid.uuid4()),
            email="dave@example.com",
            password_hash="x",
            token_quota=999,
        )
        session.add(u)
        await session.commit()

        # Flag off; override wins.
        quota = await get_user_quota(u, session)
        assert quota == 999

    @pytest.mark.asyncio
    async def test_paid_only_without_plan_is_unentitled_zero(
        self, session: AsyncSession, stripe_env: None
    ) -> None:
        await activate_billing(session, access_model="paid_only")
        session.add(SystemSetting(key="default_token_quota", value="0"))
        u = User(
            id=str(uuid.uuid4()),
            email="eve@example.com",
            password_hash="x",
        )
        session.add(u)
        await session.commit()

        quota = await get_user_quota(u, session)
        assert quota == 0
        from fim_one.web.services.quota_enforcer import is_user_unentitled

        assert await is_user_unentitled(u.id, session) is True


class TestAccessModel:
    @pytest.mark.asyncio
    async def test_paid_only_does_not_seed_free_or_backfill(
        self, session: AsyncSession, stripe_env: None
    ) -> None:
        u = User(
            id=str(uuid.uuid4()),
            email="alice@example.com",
            password_hash="x",
        )
        session.add(u)
        await session.commit()

        result = await activate_billing(session, access_model="paid_only")

        assert result["access_model"] == "paid_only"
        assert result["users_backfilled"] == 0
        slugs = {
            p.slug
            for p in (await session.execute(select(BillingPlan))).scalars()
        }
        assert "pro" in slugs
        assert "free" not in slugs
        await session.refresh(u)
        assert u.plan_id is None

    @pytest.mark.asyncio
    async def test_paid_template_has_no_hardcoded_price(
        self, session: AsyncSession, stripe_env: None
    ) -> None:
        await activate_billing(session)
        pro = (
            await session.execute(
                select(BillingPlan).where(BillingPlan.slug == "pro")
            )
        ).scalar_one()
        assert pro.stripe_price_id is None

    @pytest.mark.asyncio
    async def test_legacy_flag_maps_to_freemium(
        self, session: AsyncSession
    ) -> None:
        from fim_one.web.services.billing_flag import get_access_model

        session.add(SystemSetting(key="billing_enabled", value="true"))
        await session.commit()
        assert await get_access_model(session) == "freemium"


# ---------------------------------------------------------------------------
# API gating — 503 when flag is off
# ---------------------------------------------------------------------------


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
async def gated_client(
    stripe_env: None,
) -> AsyncIterator[tuple[AsyncClient, AsyncSession, User, User]]:
    """Spin up an ASGI client with billing flag explicitly OFF.

    Yields ``(client, session, regular_user, admin_user)``.
    """
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        # Seed users — flag deliberately *not* set so the row is
        # missing and the gate treats it as off.
        regular = User(
            id=str(uuid.uuid4()),
            username="alice",
            email="alice@example.com",
            password_hash="x",
            is_active=True,
        )
        admin = User(
            id=str(uuid.uuid4()),
            username="root",
            email="root@example.com",
            password_hash="x",
            is_admin=True,
            is_active=True,
        )
        session.add_all([regular, admin])
        await session.commit()

        from fim_one.db import get_session

        @asynccontextmanager
        async def _noop_lifespan(app):  # type: ignore[no-untyped-def]
            yield

        with patch("fim_one.web.app.lifespan", _noop_lifespan):
            app = create_app()

        async def _override_session():  # type: ignore[no-untyped-def]
            yield session

        app.dependency_overrides[get_session] = _override_session

        @asynccontextmanager
        async def _mock_create_session():  # type: ignore[no-untyped-def]
            yield session

        with patch("fim_one.db.create_session", _mock_create_session), patch(
            "fim_one.db.engine.create_session", _mock_create_session
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as c:
                yield c, session, regular, admin

        app.dependency_overrides.clear()
    await engine.dispose()


class TestApiGating:
    @pytest.mark.asyncio
    async def test_user_billing_routes_503_when_off(
        self,
        gated_client: tuple[AsyncClient, AsyncSession, User, User],
    ) -> None:
        client, _session, regular, _admin = gated_client
        for path in ("/api/billing/plans", "/api/billing/subscription"):
            resp = await client.get(path, headers=_auth_headers(regular))
            assert resp.status_code == 503, (path, resp.text)
            assert "not enabled" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_user_billing_post_routes_503_when_off(
        self,
        gated_client: tuple[AsyncClient, AsyncSession, User, User],
    ) -> None:
        client, _session, regular, _admin = gated_client
        for path, body in (
            ("/api/billing/checkout", {"plan_slug": "pro"}),
            ("/api/billing/portal", {}),
        ):
            resp = await client.post(
                path, headers=_auth_headers(regular), json=body
            )
            assert resp.status_code == 503, (path, resp.text)

    @pytest.mark.asyncio
    async def test_admin_billing_routes_503_when_off(
        self,
        gated_client: tuple[AsyncClient, AsyncSession, User, User],
    ) -> None:
        client, _session, _regular, admin = gated_client
        resp = await client.get(
            "/api/admin/billing/plans", headers=_auth_headers(admin)
        )
        assert resp.status_code == 503

        resp = await client.get(
            "/api/admin/billing/subscriptions", headers=_auth_headers(admin)
        )
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_webhook_503_when_off(
        self,
        gated_client: tuple[AsyncClient, AsyncSession, User, User],
    ) -> None:
        client, _session, _regular, _admin = gated_client
        resp = await client.post(
            "/api/webhooks/stripe",
            headers={"stripe-signature": "t=1,v1=anything"},
            content=b"{}",
        )
        # 503 from the gate beats 400 from signature verification.
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_routes_unblocked_after_activation(
        self,
        gated_client: tuple[AsyncClient, AsyncSession, User, User],
    ) -> None:
        client, session, _regular, admin = gated_client
        resp = await client.post(
            "/api/admin/system/billing/activate",
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["billing_enabled"] is True
        assert body["plans_seeded"] >= 1

        # The flag is on, so admin /billing/plans now reaches the
        # actual handler instead of the gate.
        resp = await client.get(
            "/api/admin/billing/plans", headers=_auth_headers(admin)
        )
        assert resp.status_code == 200

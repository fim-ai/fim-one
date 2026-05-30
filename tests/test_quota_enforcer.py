"""Tests for ``fim_one.web.services.quota_enforcer``.

Validates Scheme A precedence (override-first + 3-state):

  1. ``user.token_quota == 0``  → ``None`` (unlimited; admin VIP gift)
  2. ``user.token_quota >  0``  → that value (admin hard cap)
  3. ``user.token_quota IS NULL`` → ``user.plan.monthly_token_quota``
  4. *(fallback)* the system-wide ``default_token_quota`` setting
  5. ``None`` (last-resort unlimited)
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401 — register all models with metadata
from fim_one.db.base import Base
from fim_one.db.models import BillingPlan, SystemSetting, User
from fim_one.web.services.quota_enforcer import (
    get_user_quota,
    get_user_quota_by_id,
)


@pytest_asyncio.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        # The plan tier of the quota chain is gated on
        # ``system_settings.billing_enabled``. These tests pre-date the
        # feature flag and assume the legacy behaviour where the plan
        # tier is always honoured, so we seed the flag on by default
        # at the start of every test. Tests that exercise the flag-off
        # branch flip it back via :func:`_set_billing_enabled`.
        s.add(SystemSetting(key="billing_enabled", value="true"))
        await s.commit()
        yield s
    await engine.dispose()


async def _set_billing_enabled(
    session: AsyncSession, enabled: bool
) -> None:
    row = await session.get(SystemSetting, "billing_enabled")
    if row is None:
        session.add(
            SystemSetting(
                key="billing_enabled",
                value="true" if enabled else "false",
            )
        )
    else:
        row.value = "true" if enabled else "false"
    await session.commit()


async def _make_user(
    session: AsyncSession,
    *,
    token_quota: int | None = None,
    plan_id: int | None = None,
) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:6]}@example.com",
        token_quota=token_quota,
        plan_id=plan_id,
    )
    session.add(user)
    await session.commit()
    return user


async def _make_plan(
    session: AsyncSession, *, slug: str, quota: int
) -> BillingPlan:
    plan = BillingPlan(
        slug=slug,
        name=slug.title(),
        monthly_token_quota=quota,
    )
    session.add(plan)
    await session.commit()
    return plan


async def _set_default_quota(session: AsyncSession, value: str) -> None:
    """Insert / replace the ``default_token_quota`` system setting."""
    existing = await session.get(SystemSetting, "default_token_quota")
    if existing is None:
        session.add(SystemSetting(key="default_token_quota", value=value))
    else:
        existing.value = value
    await session.commit()


# ---------------------------------------------------------------------------
# get_user_quota — uses ORM relationship + db.get(BillingPlan)
# ---------------------------------------------------------------------------


class TestGetUserQuota:
    """Scheme A precedence chain on the ORM-relationship variant."""

    @pytest.mark.asyncio
    async def test_token_quota_zero_returns_none_unlimited(
        self, session: AsyncSession
    ) -> None:
        # Tier 1: ``users.token_quota == 0`` is the admin VIP gift —
        # unlimited regardless of plan tier.
        plan = await _make_plan(session, slug="pro", quota=5_000_000)
        user = await _make_user(session, token_quota=0, plan_id=plan.id)

        quota = await get_user_quota(user, session)
        assert quota is None

    @pytest.mark.asyncio
    async def test_token_quota_positive_overrides_plan(
        self, session: AsyncSession
    ) -> None:
        # Tier 2: admin hard cap wins over plan quota — VIP throttling.
        plan = await _make_plan(session, slug="pro", quota=5_000_000)
        user = await _make_user(session, token_quota=100_000, plan_id=plan.id)

        quota = await get_user_quota(user, session)
        assert quota == 100_000

    @pytest.mark.asyncio
    async def test_pro_plan_quota_when_token_quota_null(
        self, session: AsyncSession
    ) -> None:
        # Tier 3: Pro plan tier returned when no override is set.
        plan = await _make_plan(session, slug="pro", quota=5_000_000)
        user = await _make_user(session, token_quota=None, plan_id=plan.id)

        quota = await get_user_quota(user, session)
        assert quota == 5_000_000

    @pytest.mark.asyncio
    async def test_free_plan_quota_when_token_quota_null(
        self, session: AsyncSession
    ) -> None:
        # Tier 3: Free plan tier returned for unsubscribed users
        # (post-MVP every user is at least on Free).
        plan = await _make_plan(session, slug="free", quota=100_000)
        user = await _make_user(session, token_quota=None, plan_id=plan.id)

        quota = await get_user_quota(user, session)
        assert quota == 100_000

    @pytest.mark.asyncio
    async def test_default_setting_used_when_no_plan(
        self, session: AsyncSession
    ) -> None:
        # Tier 4: defensive fallback when both ``token_quota`` and
        # ``plan_id`` are NULL — pre-backfill state shouldn't normally
        # exist, but the resolver handles it gracefully.
        await _set_default_quota(session, "1000000")
        user = await _make_user(session, token_quota=None, plan_id=None)

        quota = await get_user_quota(user, session)
        assert quota == 1_000_000

    @pytest.mark.asyncio
    async def test_no_plan_no_default_returns_none(
        self, session: AsyncSession
    ) -> None:
        # Tier 5: nothing configured anywhere → unlimited (last resort).
        user = await _make_user(session, token_quota=None, plan_id=None)

        quota = await get_user_quota(user, session)
        assert quota is None

    @pytest.mark.asyncio
    async def test_default_setting_zero_treated_as_unlimited(
        self, session: AsyncSession
    ) -> None:
        # ``default_token_quota = 0`` is the legacy "disabled" sentinel.
        # The resolver must treat it as "no cap" — same as missing.
        await _set_default_quota(session, "0")
        user = await _make_user(session, token_quota=None, plan_id=None)

        quota = await get_user_quota(user, session)
        assert quota is None

    @pytest.mark.asyncio
    async def test_default_setting_non_numeric_treated_as_missing(
        self, session: AsyncSession
    ) -> None:
        # Defensive: a corrupt setting value must not raise — treat
        # as missing so the resolver still returns ``None``.
        await _set_default_quota(session, "not-a-number")
        user = await _make_user(session, token_quota=None, plan_id=None)

        quota = await get_user_quota(user, session)
        assert quota is None

    @pytest.mark.asyncio
    async def test_plan_id_set_but_plan_missing_falls_through(
        self, session: AsyncSession
    ) -> None:
        # Defensive: plan_id points at a row that no longer exists.
        # Skip plan tier; use system default (or unlimited).
        await _set_default_quota(session, "777")
        user = await _make_user(session, token_quota=None, plan_id=99999)
        quota = await get_user_quota(user, session)
        assert quota == 777


# ---------------------------------------------------------------------------
# get_user_quota_by_id — single JOIN'd query for chat.py
# ---------------------------------------------------------------------------


class TestGetUserQuotaById:
    """Scheme A precedence on the by-id variant (mirror of above)."""

    @pytest.mark.asyncio
    async def test_token_quota_zero_returns_none_unlimited(
        self, session: AsyncSession
    ) -> None:
        plan = await _make_plan(session, slug="pro", quota=5_000_000)
        user = await _make_user(session, token_quota=0, plan_id=plan.id)
        quota = await get_user_quota_by_id(user.id, session)
        assert quota is None

    @pytest.mark.asyncio
    async def test_token_quota_positive_overrides_plan(
        self, session: AsyncSession
    ) -> None:
        plan = await _make_plan(session, slug="pro", quota=5_000_000)
        user = await _make_user(session, token_quota=100_000, plan_id=plan.id)
        quota = await get_user_quota_by_id(user.id, session)
        assert quota == 100_000

    @pytest.mark.asyncio
    async def test_pro_plan_quota_when_token_quota_null(
        self, session: AsyncSession
    ) -> None:
        plan = await _make_plan(session, slug="pro", quota=5_000_000)
        user = await _make_user(session, token_quota=None, plan_id=plan.id)
        quota = await get_user_quota_by_id(user.id, session)
        assert quota == 5_000_000

    @pytest.mark.asyncio
    async def test_free_plan_quota_when_token_quota_null(
        self, session: AsyncSession
    ) -> None:
        plan = await _make_plan(session, slug="free", quota=100_000)
        user = await _make_user(session, token_quota=None, plan_id=plan.id)
        quota = await get_user_quota_by_id(user.id, session)
        assert quota == 100_000

    @pytest.mark.asyncio
    async def test_default_setting_used_when_no_plan(
        self, session: AsyncSession
    ) -> None:
        await _set_default_quota(session, "1000000")
        user = await _make_user(session, token_quota=None, plan_id=None)
        quota = await get_user_quota_by_id(user.id, session)
        assert quota == 1_000_000

    @pytest.mark.asyncio
    async def test_no_plan_no_default_returns_none(
        self, session: AsyncSession
    ) -> None:
        user = await _make_user(session, token_quota=None, plan_id=None)
        quota = await get_user_quota_by_id(user.id, session)
        assert quota is None

    @pytest.mark.asyncio
    async def test_unknown_user_returns_none(
        self, session: AsyncSession
    ) -> None:
        quota = await get_user_quota_by_id("does-not-exist", session)
        assert quota is None

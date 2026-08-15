"""Tests for ``fim_one.web.services.default_plan``.

The helper resolves the default plan id at registration time so new
users auto-bind to it. It must:

- Prefer ``system_settings.default_plan_id`` over the legacy
  ``WHERE slug='free'`` lookup.
- Fall back to slug='free' for transition safety on installs that
  pre-date the pointer.
- Return ``None`` when neither the pointer nor a Free row exists.
- Never raise — the backfill migration / activation flow covers any
  orphans.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401 — register all models with metadata
from fim_one.db.base import Base
from fim_one.db.models import BillingPlan, SystemSetting
from fim_one.web.services.default_plan import (
    get_default_plan_id,
    get_free_plan_id,
)


@pytest_asyncio.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        # Default-plan lookup only binds users under freemium. The
        # legacy flag maps to that posture so existing pointer/slug
        # tests keep their meaning.
        s.add(SystemSetting(key="billing_enabled", value="true"))
        await s.commit()
        yield s
    await engine.dispose()


class TestGetDefaultPlanId:
    @pytest.mark.asyncio
    async def test_pointer_takes_precedence_over_slug(
        self, session: AsyncSession
    ) -> None:
        # Both Free and a custom Pro exist; pointer points at Pro so
        # the helper must surface Pro's id, not Free's.
        free = BillingPlan(slug="free", name="Free", monthly_token_quota=100)
        pro = BillingPlan(slug="pro", name="Pro", monthly_token_quota=5000)
        session.add_all([free, pro])
        await session.commit()
        session.add(SystemSetting(key="default_plan_id", value=str(pro.id)))
        await session.commit()

        assert await get_default_plan_id(session) == pro.id

    @pytest.mark.asyncio
    async def test_falls_back_to_free_slug_when_pointer_missing(
        self, session: AsyncSession
    ) -> None:
        plan = BillingPlan(slug="free", name="Free", monthly_token_quota=100_000)
        session.add(plan)
        await session.commit()

        plan_id = await get_default_plan_id(session)
        assert plan_id == plan.id

    @pytest.mark.asyncio
    async def test_returns_none_when_pointer_invalid(
        self, session: AsyncSession
    ) -> None:
        # Pointer references a non-existent plan id and no Free slug.
        session.add(SystemSetting(key="default_plan_id", value="not-an-int"))
        await session.commit()

        # Invalid pointer falls through to slug='free' (also missing) → None.
        plan_id = await get_default_plan_id(session)
        assert plan_id is None

    @pytest.mark.asyncio
    async def test_returns_none_when_free_plan_missing(
        self, session: AsyncSession
    ) -> None:
        # Only Pro exists — Free row was somehow not seeded and no pointer.
        session.add(
            BillingPlan(slug="pro", name="Pro", monthly_token_quota=5_000_000)
        )
        await session.commit()

        plan_id = await get_default_plan_id(session)
        assert plan_id is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_table(
        self, session: AsyncSession
    ) -> None:
        plan_id = await get_default_plan_id(session)
        assert plan_id is None

    @pytest.mark.asyncio
    async def test_returns_none_when_paid_only(
        self, session: AsyncSession
    ) -> None:
        plan = BillingPlan(slug="free", name="Free", monthly_token_quota=100)
        session.add(plan)
        session.add(SystemSetting(key="access_model", value="paid_only"))
        await session.commit()

        assert await get_default_plan_id(session) is None


class TestBackwardCompatAlias:
    """``get_free_plan_id`` is preserved as an alias to avoid regressing
    any external caller. New code should use ``get_default_plan_id``.
    """

    @pytest.mark.asyncio
    async def test_alias_returns_same_id(self, session: AsyncSession) -> None:
        plan = BillingPlan(slug="free", name="Free", monthly_token_quota=100)
        session.add(plan)
        await session.commit()

        assert await get_free_plan_id(session) == plan.id

"""Published workflows appear in the Market and the admin Market list.

A workflow's ``status`` is its own draft/active lifecycle and publishing only
changes its visibility, so listings that required ``status == "published"``
hid every workflow anyone published.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.db.base import Base
from fim_one.db.models.system_setting import SystemSetting
from fim_one.db.models.user import User
from fim_one.db.models.workflow import Workflow
from fim_one.web.api import admin_market, market
from fim_one.web.platform import MARKET_ORG_ID
from fim_one.web.services.feature_flags import SETTING_FEATURE_WORKFLOWS


@pytest.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    import fim_one.db.models  # noqa: F401

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _user(*, is_admin: bool = False) -> User:
    uid = str(uuid.uuid4())
    return User(id=uid, email=f"{uid}@x.io", username=uid[:8], is_admin=is_admin)


async def _seed(session: AsyncSession, status: str) -> tuple[User, User]:
    owner = _user()
    browser = _user()
    session.add_all([owner, browser])
    session.add(SystemSetting(key=SETTING_FEATURE_WORKFLOWS, value="true"))
    session.add(
        Workflow(
            id="wf-pub",
            user_id=owner.id,
            name="Shared flow",
            blueprint={"nodes": [], "edges": []},
            status=status,
            visibility="org",
            org_id=MARKET_ORG_ID,
            publish_status="approved",
        )
    )
    await session.commit()
    return owner, browser


class TestWorkflowMarketListing:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["active", "draft"])
    async def test_browse_lists_published_workflow(
        self, session: AsyncSession, status: str
    ) -> None:
        _, browser = await _seed(session, status)
        result = await market.browse_market(
            resource_type="workflow",
            scope="market",
            category=None,
            page=1,
            size=20,
            current_user=browser,
            db=session,
        )
        assert result.data is not None
        assert [item["id"] for item in result.data["items"]] == ["wf-pub"]

    @pytest.mark.asyncio
    async def test_admin_market_lists_workflow(self, session: AsyncSession) -> None:
        await _seed(session, "active")
        data = await admin_market.list_market_resources(
            resource_type="workflow", db=session, admin=_user(is_admin=True)
        )
        assert [item["id"] for item in data["items"]] == ["wf-pub"]

    def test_status_filter_skips_workflow_only(self) -> None:
        assert admin_market._STATUS_FILTERED_TYPES == {"agent", "skill", "connector"}

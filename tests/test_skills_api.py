"""Published skills must reach the org / Market listing.

The Market listing filtered skills on ``status == "published"`` but
``POST /api/skills/{id}/publish`` never set ``status`` (it stayed "draft"),
so a skill shared to an org never showed up for anyone to subscribe to.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.db.base import Base
from fim_one.db.models.organization import Organization, OrgMembership
from fim_one.db.models.skill import Skill
from fim_one.db.models.system_setting import SystemSetting
from fim_one.db.models.user import User
from fim_one.web.api.market import browse_market
from fim_one.web.api.skills import publish_skill, unpublish_skill
from fim_one.web.schemas.common import PublishRequest
from fim_one.web.services.feature_flags import SETTING_FEATURE_SKILLS

OWNER = "owner-1"
MEMBER = "member-1"
ORG = "org-1"


@pytest.fixture()
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        for uid in (OWNER, MEMBER):
            session.add(User(id=uid, email=f"{uid}@example.com"))
        session.add(Organization(id=ORG, name="Acme", slug="acme", owner_id=OWNER))
        session.add(OrgMembership(org_id=ORG, user_id=OWNER, role="owner"))
        session.add(OrgMembership(org_id=ORG, user_id=MEMBER, role="member"))
        session.add(SystemSetting(key=SETTING_FEATURE_SKILLS, value="true"))
        session.add(Skill(id="sk1", user_id=OWNER, name="refund-sop", content="x"))
        await session.commit()
        yield session
    await engine.dispose()


def _user(user_id: str) -> Any:
    return SimpleNamespace(id=user_id, is_admin=False, username=None, email=None)


async def _org_skill_names(db: AsyncSession) -> list[str]:
    resp = await browse_market(
        resource_type="skill",
        scope=f"org:{ORG}",
        category=None,
        page=1,
        size=20,
        current_user=_user(MEMBER),
        db=db,
    )
    assert resp.data is not None
    return [item["name"] for item in resp.data["items"]]


class TestSkillPublishLifecycle:
    @pytest.mark.asyncio
    async def test_published_skill_listed_for_org_member(self, db: AsyncSession) -> None:
        resp = await publish_skill(
            "sk1", PublishRequest(scope="org", org_id=ORG), current_user=_user(OWNER), db=db
        )

        assert resp.data is not None
        assert resp.data["status"] == "published"
        assert await _org_skill_names(db) == ["refund-sop"]

    @pytest.mark.asyncio
    async def test_unpublish_reverts_status(self, db: AsyncSession) -> None:
        await publish_skill(
            "sk1", PublishRequest(scope="org", org_id=ORG), current_user=_user(OWNER), db=db
        )

        resp = await unpublish_skill("sk1", current_user=_user(OWNER), db=db)

        assert resp.data is not None
        assert resp.data["status"] == "draft"
        assert await _org_skill_names(db) == []

    @pytest.mark.asyncio
    async def test_skill_shared_before_fix_still_listed(self, db: AsyncSession) -> None:
        """Rows published by the old endpoint kept status='draft'."""
        skill = await db.get(Skill, "sk1")
        assert skill is not None
        skill.visibility = "org"
        skill.org_id = ORG
        skill.status = "draft"
        await db.commit()

        assert await _org_skill_names(db) == ["refund-sop"]

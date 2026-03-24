"""Tests for the skill fork (clone) feature."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.db.base import Base
from fim_one.web.models.skill import Skill
from fim_one.web.models.user import User
from fim_one.web.schemas.skill import SkillForkRequest


# ---------------------------------------------------------------------------
# Fixtures — in-memory SQLite async database
# ---------------------------------------------------------------------------


@pytest.fixture()
async def async_session():
    """Create an in-memory SQLite database with all required tables."""
    import fim_one.web.models  # noqa: F401

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


@pytest.fixture()
async def owner_user(async_session: AsyncSession) -> User:
    """Create and return a user who owns the source skill."""
    user = User(
        id=str(uuid.uuid4()),
        username="owner",
        email="owner@test.com",
        is_admin=False,
    )
    async_session.add(user)
    await async_session.commit()
    return user


@pytest.fixture()
async def other_user(async_session: AsyncSession) -> User:
    """Create and return a second user who will fork skills."""
    user = User(
        id=str(uuid.uuid4()),
        username="forker",
        email="forker@test.com",
        is_admin=False,
    )
    async_session.add(user)
    await async_session.commit()
    return user


@pytest.fixture()
async def source_skill(
    async_session: AsyncSession, owner_user: User
) -> Skill:
    """Create a skill to serve as the fork source."""
    skill = Skill(
        user_id=owner_user.id,
        name="Data Analysis SOP",
        description="Step-by-step data analysis procedure",
        content="1. Load data\n2. Clean data\n3. Analyze\n4. Report",
        script="import pandas as pd\ndf = pd.read_csv('data.csv')",
        script_type="python",
        resource_refs=[
            {"type": "connector", "id": "conn-123", "name": "DB Connector", "alias": "@db"},
        ],
        is_active=True,
        status="published",
        visibility="personal",
    )
    async_session.add(skill)
    await async_session.commit()

    result = await async_session.execute(
        select(Skill).where(Skill.id == skill.id)
    )
    return result.scalar_one()


# ---------------------------------------------------------------------------
# Helper: simulate the fork logic (mirrors the endpoint)
# ---------------------------------------------------------------------------


async def _do_fork(
    source: Skill,
    current_user: User,
    db: AsyncSession,
    fork_name: str | None = None,
) -> Skill:
    """Replicate the fork_skill endpoint logic for testing."""
    name = (fork_name or f"{source.name} (Fork)")[:200]

    forked = Skill(
        user_id=current_user.id,
        name=name,
        description=source.description,
        content=source.content,
        script=source.script,
        script_type=source.script_type,
        resource_refs=source.resource_refs,
        is_active=True,
        status="draft",
        forked_from=source.id,
        visibility="personal",
        org_id=None,
        publish_status=None,
    )
    db.add(forked)
    await db.commit()

    result = await db.execute(
        select(Skill).where(Skill.id == forked.id)
    )
    return result.scalar_one()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSkillForkRequest:
    """Schema validation for SkillForkRequest."""

    def test_default_name_is_none(self) -> None:
        req = SkillForkRequest()
        assert req.name is None

    def test_custom_name(self) -> None:
        req = SkillForkRequest(name="My Custom Fork")
        assert req.name == "My Custom Fork"


class TestForkCreatesNewSkill:
    """Fork creates a new skill with a different ID."""

    async def test_fork_has_different_id(
        self,
        async_session: AsyncSession,
        source_skill: Skill,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_skill, other_user, async_session)
        assert forked.id != source_skill.id
        assert forked.id  # not empty

    async def test_fork_sets_forked_from(
        self,
        async_session: AsyncSession,
        source_skill: Skill,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_skill, other_user, async_session)
        assert forked.forked_from == source_skill.id


class TestForkCopiesContentFields:
    """Fork copies all relevant content fields."""

    async def test_copies_name_with_fork_suffix(
        self,
        async_session: AsyncSession,
        source_skill: Skill,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_skill, other_user, async_session)
        assert forked.name == f"{source_skill.name} (Fork)"

    async def test_copies_description(
        self,
        async_session: AsyncSession,
        source_skill: Skill,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_skill, other_user, async_session)
        assert forked.description == source_skill.description

    async def test_copies_content(
        self,
        async_session: AsyncSession,
        source_skill: Skill,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_skill, other_user, async_session)
        assert forked.content == source_skill.content

    async def test_copies_script(
        self,
        async_session: AsyncSession,
        source_skill: Skill,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_skill, other_user, async_session)
        assert forked.script == source_skill.script

    async def test_copies_script_type(
        self,
        async_session: AsyncSession,
        source_skill: Skill,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_skill, other_user, async_session)
        assert forked.script_type == source_skill.script_type

    async def test_copies_resource_refs(
        self,
        async_session: AsyncSession,
        source_skill: Skill,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_skill, other_user, async_session)
        assert forked.resource_refs == source_skill.resource_refs


class TestForkDoesNotCopyMetadata:
    """Fork does NOT copy org/publish metadata."""

    async def test_org_id_is_none(
        self,
        async_session: AsyncSession,
        source_skill: Skill,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_skill, other_user, async_session)
        assert forked.org_id is None

    async def test_publish_status_is_none(
        self,
        async_session: AsyncSession,
        source_skill: Skill,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_skill, other_user, async_session)
        assert forked.publish_status is None


class TestForkCustomName:
    """Fork with custom name uses that name."""

    async def test_custom_name_overrides_default(
        self,
        async_session: AsyncSession,
        source_skill: Skill,
        other_user: User,
    ) -> None:
        forked = await _do_fork(
            source_skill, other_user, async_session, fork_name="My Skill Clone"
        )
        assert forked.name == "My Skill Clone"

    async def test_long_name_is_truncated(
        self,
        async_session: AsyncSession,
        source_skill: Skill,
        other_user: User,
    ) -> None:
        long_name = "A" * 250
        forked = await _do_fork(
            source_skill, other_user, async_session, fork_name=long_name
        )
        assert len(forked.name) <= 200


class TestForkAssignsToCurrentUser:
    """Fork assigns ownership to the current user."""

    async def test_forked_user_id_is_current_user(
        self,
        async_session: AsyncSession,
        source_skill: Skill,
        owner_user: User,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_skill, other_user, async_session)
        assert forked.user_id == other_user.id
        assert forked.user_id != owner_user.id

    async def test_owner_can_fork_own_skill(
        self,
        async_session: AsyncSession,
        source_skill: Skill,
        owner_user: User,
    ) -> None:
        forked = await _do_fork(source_skill, owner_user, async_session)
        assert forked.user_id == owner_user.id
        assert forked.id != source_skill.id


class TestForkSetsDefaults:
    """Fork sets correct default values for a new draft skill."""

    async def test_status_is_draft(
        self,
        async_session: AsyncSession,
        source_skill: Skill,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_skill, other_user, async_session)
        assert forked.status == "draft"

    async def test_visibility_is_personal(
        self,
        async_session: AsyncSession,
        source_skill: Skill,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_skill, other_user, async_session)
        assert forked.visibility == "personal"

    async def test_is_active_is_true(
        self,
        async_session: AsyncSession,
        source_skill: Skill,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_skill, other_user, async_session)
        assert forked.is_active is True


class TestForkSkillWithMinimalData:
    """Fork a skill that has minimal data — no script, no resource_refs."""

    async def test_fork_minimal_skill(
        self,
        async_session: AsyncSession,
        owner_user: User,
        other_user: User,
    ) -> None:
        bare = Skill(
            user_id=owner_user.id,
            name="Bare Skill",
            description=None,
            content="Just do the thing.",
            script=None,
            script_type=None,
            resource_refs=None,
            is_active=True,
            status="published",
            visibility="personal",
        )
        async_session.add(bare)
        await async_session.commit()

        result = await async_session.execute(
            select(Skill).where(Skill.id == bare.id)
        )
        bare = result.scalar_one()

        forked = await _do_fork(bare, other_user, async_session)
        assert forked.id != bare.id
        assert forked.name == "Bare Skill (Fork)"
        assert forked.content == "Just do the thing."
        assert forked.script is None
        assert forked.script_type is None
        assert forked.resource_refs is None

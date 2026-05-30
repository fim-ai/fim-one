"""Tests for the Agent fork (clone) feature."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.db.base import Base
from fim_one.db.models.agent import Agent
from fim_one.db.models.user import User
from fim_one.web.schemas.agent import AgentForkRequest


# ---------------------------------------------------------------------------
# Fixtures — in-memory SQLite async database
# ---------------------------------------------------------------------------


@pytest.fixture()
async def async_session():
    """Create an in-memory SQLite database with all required tables."""
    import fim_one.db.models  # noqa: F401

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
    """Create and return a user who owns the source agent."""
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
    """Create and return a second user who will fork agents."""
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
async def source_agent(
    async_session: AsyncSession, owner_user: User
) -> Agent:
    """Create an agent to serve as the fork source."""
    agent = Agent(
        user_id=owner_user.id,
        name="My Agent",
        description="A test agent",
        instructions="You are a helpful assistant.",
        execution_mode="react",
        model_config_json={"general_model": "gpt-4"},
        tool_categories=["web", "computation"],
        suggested_prompts=["Hello", "Help me"],
        connector_ids=["conn-1", "conn-2"],
        kb_ids=["kb-1"],
        mcp_server_ids=["mcp-1"],
        skill_ids=["skill-1", "skill-2"],
        compact_instructions="Keep it short.",
        grounding_config={"threshold": 0.7},
        sandbox_config={"memory": "512m"},
        is_active=True,
        is_builder=True,
        visibility="personal",
        org_id="org-123",
        publish_status="approved",
    )
    async_session.add(agent)
    await async_session.commit()

    result = await async_session.execute(
        select(Agent).where(Agent.id == agent.id)
    )
    return result.scalar_one()


# ---------------------------------------------------------------------------
# Helper: simulate the fork logic (mirrors the endpoint)
# ---------------------------------------------------------------------------


async def _do_fork(
    source: Agent,
    current_user: User,
    db: AsyncSession,
    fork_name: str | None = None,
) -> Agent:
    """Replicate the fork_agent endpoint logic for testing."""
    name = (fork_name or f"{source.name} (Fork)")[:200]

    forked = Agent(
        user_id=current_user.id,
        name=name,
        description=source.description,
        icon=source.icon,
        instructions=source.instructions,
        execution_mode=source.execution_mode,
        model_config_json=source.model_config_json,
        tool_categories=source.tool_categories,
        suggested_prompts=source.suggested_prompts,
        connector_ids=source.connector_ids,
        kb_ids=source.kb_ids,
        mcp_server_ids=source.mcp_server_ids,
        skill_ids=source.skill_ids,
        compact_instructions=source.compact_instructions,
        grounding_config=source.grounding_config,
        sandbox_config=source.sandbox_config,
        is_active=True,
        is_builder=False,
        forked_from=source.id,
        visibility="personal",
        org_id=None,
        publish_status=None,
        status="draft",
    )
    db.add(forked)
    await db.commit()

    result = await db.execute(
        select(Agent).where(Agent.id == forked.id)
    )
    return result.scalar_one()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAgentForkRequest:
    """Schema validation for AgentForkRequest."""

    def test_default_name_is_none(self) -> None:
        req = AgentForkRequest()
        assert req.name is None

    def test_custom_name(self) -> None:
        req = AgentForkRequest(name="My Custom Fork")
        assert req.name == "My Custom Fork"


class TestForkCreatesNewAgent:
    """Fork creates a new agent with a different ID."""

    async def test_fork_has_different_id(
        self,
        async_session: AsyncSession,
        source_agent: Agent,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_agent, other_user, async_session)
        assert forked.id != source_agent.id
        assert forked.id  # not empty

    async def test_fork_sets_forked_from(
        self,
        async_session: AsyncSession,
        source_agent: Agent,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_agent, other_user, async_session)
        assert forked.forked_from == source_agent.id


class TestForkCopiesConfigFields:
    """Fork copies all relevant configuration fields."""

    async def test_copies_name_with_fork_suffix(
        self,
        async_session: AsyncSession,
        source_agent: Agent,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_agent, other_user, async_session)
        assert forked.name == f"{source_agent.name} (Fork)"

    async def test_copies_description(
        self,
        async_session: AsyncSession,
        source_agent: Agent,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_agent, other_user, async_session)
        assert forked.description == source_agent.description

    async def test_copies_instructions(
        self,
        async_session: AsyncSession,
        source_agent: Agent,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_agent, other_user, async_session)
        assert forked.instructions == source_agent.instructions

    async def test_copies_execution_mode(
        self,
        async_session: AsyncSession,
        source_agent: Agent,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_agent, other_user, async_session)
        assert forked.execution_mode == source_agent.execution_mode

    async def test_copies_compact_instructions(
        self,
        async_session: AsyncSession,
        source_agent: Agent,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_agent, other_user, async_session)
        assert forked.compact_instructions == source_agent.compact_instructions


class TestForkPreservesResourceBindings:
    """Fork preserves connector_ids, kb_ids, skill_ids, mcp_server_ids."""

    async def test_connector_ids_preserved(
        self,
        async_session: AsyncSession,
        source_agent: Agent,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_agent, other_user, async_session)
        assert forked.connector_ids == source_agent.connector_ids

    async def test_kb_ids_preserved(
        self,
        async_session: AsyncSession,
        source_agent: Agent,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_agent, other_user, async_session)
        assert forked.kb_ids == source_agent.kb_ids

    async def test_skill_ids_preserved(
        self,
        async_session: AsyncSession,
        source_agent: Agent,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_agent, other_user, async_session)
        assert forked.skill_ids == source_agent.skill_ids

    async def test_mcp_server_ids_preserved(
        self,
        async_session: AsyncSession,
        source_agent: Agent,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_agent, other_user, async_session)
        assert forked.mcp_server_ids == source_agent.mcp_server_ids


class TestForkDoesNotCopyOrgFields:
    """Fork does NOT copy org_id, publish_status, is_builder."""

    async def test_org_id_is_none(
        self,
        async_session: AsyncSession,
        source_agent: Agent,
        other_user: User,
    ) -> None:
        # Source has org_id set
        assert source_agent.org_id is not None
        forked = await _do_fork(source_agent, other_user, async_session)
        assert forked.org_id is None

    async def test_publish_status_is_none(
        self,
        async_session: AsyncSession,
        source_agent: Agent,
        other_user: User,
    ) -> None:
        # Source has publish_status set
        assert source_agent.publish_status is not None
        forked = await _do_fork(source_agent, other_user, async_session)
        assert forked.publish_status is None

    async def test_is_builder_is_false(
        self,
        async_session: AsyncSession,
        source_agent: Agent,
        other_user: User,
    ) -> None:
        # Source has is_builder=True
        assert source_agent.is_builder is True
        forked = await _do_fork(source_agent, other_user, async_session)
        assert forked.is_builder is False


class TestForkCustomName:
    """Fork with custom name uses that name."""

    async def test_custom_name_overrides_default(
        self,
        async_session: AsyncSession,
        source_agent: Agent,
        other_user: User,
    ) -> None:
        forked = await _do_fork(
            source_agent, other_user, async_session, fork_name="My Agent Clone"
        )
        assert forked.name == "My Agent Clone"

    async def test_long_name_is_truncated(
        self,
        async_session: AsyncSession,
        source_agent: Agent,
        other_user: User,
    ) -> None:
        long_name = "A" * 250
        forked = await _do_fork(
            source_agent, other_user, async_session, fork_name=long_name
        )
        assert len(forked.name) <= 200


class TestForkAssignsToCurrentUser:
    """Fork assigns ownership to the current user."""

    async def test_forked_user_id_is_current_user(
        self,
        async_session: AsyncSession,
        source_agent: Agent,
        owner_user: User,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_agent, other_user, async_session)
        assert forked.user_id == other_user.id
        assert forked.user_id != owner_user.id

    async def test_owner_can_fork_own_agent(
        self,
        async_session: AsyncSession,
        source_agent: Agent,
        owner_user: User,
    ) -> None:
        forked = await _do_fork(source_agent, owner_user, async_session)
        assert forked.user_id == owner_user.id
        assert forked.id != source_agent.id


class TestForkSetsDefaults:
    """Fork sets correct default values."""

    async def test_visibility_is_personal(
        self,
        async_session: AsyncSession,
        source_agent: Agent,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_agent, other_user, async_session)
        assert forked.visibility == "personal"

    async def test_is_active_is_true(
        self,
        async_session: AsyncSession,
        source_agent: Agent,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_agent, other_user, async_session)
        assert forked.is_active is True

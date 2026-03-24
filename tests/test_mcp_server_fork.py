"""Tests for the MCP server fork (clone) feature."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.db.base import Base
from fim_one.web.models.mcp_server import MCPServer
from fim_one.web.models.user import User
from fim_one.web.schemas.mcp_server import MCPServerForkRequest


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
    """Create and return a user who owns the source MCP server."""
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
    """Create and return a second user who will fork MCP servers."""
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
async def source_server(
    async_session: AsyncSession, owner_user: User
) -> MCPServer:
    """Create an MCP server to serve as the fork source."""
    server = MCPServer(
        user_id=owner_user.id,
        name="My MCP Server",
        description="A test MCP server",
        transport="sse",
        command=None,
        args=None,
        env={"API_KEY": "secret-key-123"},
        url="https://mcp.example.com/sse",
        working_dir=None,
        headers={"Authorization": "Bearer secret-token"},
        is_active=True,
        tool_count=5,
        visibility="personal",
        allow_fallback=False,
    )
    async_session.add(server)
    await async_session.commit()

    result = await async_session.execute(
        select(MCPServer).where(MCPServer.id == server.id)
    )
    return result.scalar_one()


# ---------------------------------------------------------------------------
# Helper: simulate the fork logic (mirrors the endpoint)
# ---------------------------------------------------------------------------


async def _do_fork(
    source: MCPServer,
    current_user: User,
    db: AsyncSession,
    fork_name: str | None = None,
) -> MCPServer:
    """Replicate the fork_mcp_server endpoint logic for testing."""
    name = (fork_name or f"{source.name} (Fork)")[:200]

    forked = MCPServer(
        user_id=current_user.id,
        name=name,
        description=source.description,
        transport=source.transport,
        command=source.command,
        args=source.args,
        env=None,  # encrypted credentials — do NOT copy
        url=source.url,
        working_dir=source.working_dir,
        headers=None,  # encrypted credentials — do NOT copy
        is_active=True,
        tool_count=source.tool_count,
        forked_from=source.id,
        visibility="personal",
        org_id=None,
        publish_status=None,
        allow_fallback=source.allow_fallback,
    )
    db.add(forked)
    await db.commit()

    result = await db.execute(
        select(MCPServer).where(MCPServer.id == forked.id)
    )
    return result.scalar_one()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMCPServerForkRequest:
    """Schema validation for MCPServerForkRequest."""

    def test_default_name_is_none(self) -> None:
        req = MCPServerForkRequest()
        assert req.name is None

    def test_custom_name(self) -> None:
        req = MCPServerForkRequest(name="My Custom Fork")
        assert req.name == "My Custom Fork"


class TestForkCreatesNewServer:
    """Fork creates a new MCP server with a different ID."""

    async def test_fork_has_different_id(
        self,
        async_session: AsyncSession,
        source_server: MCPServer,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_server, other_user, async_session)
        assert forked.id != source_server.id
        assert forked.id  # not empty

    async def test_fork_sets_forked_from(
        self,
        async_session: AsyncSession,
        source_server: MCPServer,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_server, other_user, async_session)
        assert forked.forked_from == source_server.id


class TestForkCopiesConfigFields:
    """Fork copies all relevant configuration fields."""

    async def test_copies_name_with_fork_suffix(
        self,
        async_session: AsyncSession,
        source_server: MCPServer,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_server, other_user, async_session)
        assert forked.name == f"{source_server.name} (Fork)"

    async def test_copies_description(
        self,
        async_session: AsyncSession,
        source_server: MCPServer,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_server, other_user, async_session)
        assert forked.description == source_server.description

    async def test_copies_transport(
        self,
        async_session: AsyncSession,
        source_server: MCPServer,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_server, other_user, async_session)
        assert forked.transport == source_server.transport

    async def test_copies_url(
        self,
        async_session: AsyncSession,
        source_server: MCPServer,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_server, other_user, async_session)
        assert forked.url == source_server.url

    async def test_copies_command_and_args(
        self,
        async_session: AsyncSession,
        source_server: MCPServer,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_server, other_user, async_session)
        assert forked.command == source_server.command
        assert forked.args == source_server.args

    async def test_copies_tool_count(
        self,
        async_session: AsyncSession,
        source_server: MCPServer,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_server, other_user, async_session)
        assert forked.tool_count == source_server.tool_count

    async def test_copies_allow_fallback(
        self,
        async_session: AsyncSession,
        source_server: MCPServer,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_server, other_user, async_session)
        assert forked.allow_fallback == source_server.allow_fallback


class TestForkDoesNotCopyCredentials:
    """Fork does NOT copy encrypted/sensitive fields."""

    async def test_env_is_none(
        self,
        async_session: AsyncSession,
        source_server: MCPServer,
        other_user: User,
    ) -> None:
        # Source has env data
        assert source_server.env is not None
        forked = await _do_fork(source_server, other_user, async_session)
        assert forked.env is None

    async def test_headers_is_none(
        self,
        async_session: AsyncSession,
        source_server: MCPServer,
        other_user: User,
    ) -> None:
        # Source has headers data
        assert source_server.headers is not None
        forked = await _do_fork(source_server, other_user, async_session)
        assert forked.headers is None

    async def test_org_id_is_none(
        self,
        async_session: AsyncSession,
        source_server: MCPServer,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_server, other_user, async_session)
        assert forked.org_id is None

    async def test_publish_status_is_none(
        self,
        async_session: AsyncSession,
        source_server: MCPServer,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_server, other_user, async_session)
        assert forked.publish_status is None


class TestForkCustomName:
    """Fork with custom name uses that name."""

    async def test_custom_name_overrides_default(
        self,
        async_session: AsyncSession,
        source_server: MCPServer,
        other_user: User,
    ) -> None:
        forked = await _do_fork(
            source_server, other_user, async_session, fork_name="My MCP Clone"
        )
        assert forked.name == "My MCP Clone"

    async def test_long_name_is_truncated(
        self,
        async_session: AsyncSession,
        source_server: MCPServer,
        other_user: User,
    ) -> None:
        long_name = "A" * 250
        forked = await _do_fork(
            source_server, other_user, async_session, fork_name=long_name
        )
        assert len(forked.name) <= 200


class TestForkAssignsToCurrentUser:
    """Fork assigns ownership to the current user."""

    async def test_forked_user_id_is_current_user(
        self,
        async_session: AsyncSession,
        source_server: MCPServer,
        owner_user: User,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_server, other_user, async_session)
        assert forked.user_id == other_user.id
        assert forked.user_id != owner_user.id

    async def test_owner_can_fork_own_server(
        self,
        async_session: AsyncSession,
        source_server: MCPServer,
        owner_user: User,
    ) -> None:
        forked = await _do_fork(source_server, owner_user, async_session)
        assert forked.user_id == owner_user.id
        assert forked.id != source_server.id


class TestForkSetsDefaults:
    """Fork sets correct default values."""

    async def test_visibility_is_personal(
        self,
        async_session: AsyncSession,
        source_server: MCPServer,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_server, other_user, async_session)
        assert forked.visibility == "personal"

    async def test_is_active_is_true(
        self,
        async_session: AsyncSession,
        source_server: MCPServer,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_server, other_user, async_session)
        assert forked.is_active is True

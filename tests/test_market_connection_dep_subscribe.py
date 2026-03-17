"""Tests for connection dependency auto-subscribe and cascade-clean on unsubscribe.

Covers:
1. Subscribing a Skill auto-subscribes its Connector and MCP Server connection deps.
2. Subscribing an Agent auto-subscribes its Connector and MCP Server connection deps.
3. Unsubscribing a Skill cascades deletion of orphaned connection dep subs + credentials.
4. Unsubscribing keeps shared connection deps when another Solution still references them.
5. Re-subscribing the same Skill does not create duplicate connection dep subscriptions.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.db.base import Base
from fim_one.web.api.market import (
    SOLUTION_TYPES,
    _cascade_clean_connection_deps,
)
from fim_one.web.dependency_analyzer import resolve_solution_dependencies
from fim_one.web.models.agent import Agent
from fim_one.web.models.connector import Connector
from fim_one.web.models.connector_credential import ConnectorCredential
from fim_one.web.models.mcp_server import MCPServer
from fim_one.web.models.mcp_server_credential import MCPServerCredential
from fim_one.web.models.resource_subscription import ResourceSubscription
from fim_one.web.models.skill import Skill
from fim_one.web.models.user import User
from fim_one.web.platform import MARKET_ORG_ID


# ---------------------------------------------------------------------------
# Fixtures — in-memory SQLite async database
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cred_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set a stable encryption key for tests that touch credential models."""
    import fim_one.core.security.encryption as enc

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "test-conn-dep-key-1234567890")
    enc._CREDENTIAL_KEY_RAW = "test-conn-dep-key-1234567890"
    enc._cred_fernet_instance = None
    yield
    enc._cred_fernet_instance = None


@pytest.fixture()
async def async_session():
    """Create an in-memory SQLite database with all required tables."""
    import fim_one.web.models  # noqa: F401 — register all models

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
async def user_a(async_session: AsyncSession) -> User:
    """Create a test user."""
    user = User(
        id=str(uuid.uuid4()),
        username="test_user_conn",
        email="conn_test@example.com",
        is_admin=False,
    )
    async_session.add(user)
    await async_session.commit()
    return user


def _make_sub(user_id: str, resource_type: str, resource_id: str) -> ResourceSubscription:
    """Helper to create a ResourceSubscription object."""
    return ResourceSubscription(
        user_id=user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        org_id=MARKET_ORG_ID,
    )


# ---------------------------------------------------------------------------
# Test: subscribe auto-subscribes connection deps
# ---------------------------------------------------------------------------


class TestSubscribeAutoSubscribesConnectionDeps:
    """Verify that subscribing a Solution auto-creates subscriptions for connection deps."""

    @pytest.mark.asyncio
    async def test_subscribe_skill_auto_subscribes_connection_deps(
        self, async_session: AsyncSession, user_a: User
    ) -> None:
        """Subscribe to a Skill that references a Connector and MCP Server.

        After subscribing the Skill, ResourceSubscription rows should exist
        for both the Connector and MCP Server connection deps.
        """
        conn_id = str(uuid.uuid4())
        srv_id = str(uuid.uuid4())
        skill_id = str(uuid.uuid4())
        other_user_id = str(uuid.uuid4())

        # Create the Connector and MCP Server (owned by another user)
        connector = Connector(
            id=conn_id,
            user_id=other_user_id,
            name="GitHub API",
            auth_type="bearer",
            allow_fallback=False,
        )
        server = MCPServer(
            id=srv_id,
            user_id=other_user_id,
            name="Code MCP",
            transport="stdio",
            allow_fallback=False,
        )
        async_session.add_all([connector, server])

        # Create the Skill with resource_refs pointing to the connector and MCP server
        skill = Skill(
            id=skill_id,
            user_id=other_user_id,
            name="Auto-Code Skill",
            content="Write code using GitHub API",
            resource_refs=[
                {"type": "connector", "id": conn_id, "name": "GitHub API"},
                {"type": "mcp_server", "id": srv_id, "name": "Code MCP"},
            ],
        )
        async_session.add(skill)
        await async_session.commit()

        # Resolve dependencies (simulates what subscribe endpoint does)
        manifest = await resolve_solution_dependencies("skill", skill_id, async_session)
        assert len(manifest.connection_deps) == 2

        # Simulate subscribe: create Skill subscription + auto-subscribe connection deps
        skill_sub = _make_sub(user_a.id, "skill", skill_id)
        async_session.add(skill_sub)

        for dep in manifest.connection_deps:
            existing = await async_session.execute(
                select(ResourceSubscription).where(
                    ResourceSubscription.user_id == user_a.id,
                    ResourceSubscription.resource_type == dep.resource_type,
                    ResourceSubscription.resource_id == dep.resource_id,
                )
            )
            if existing.scalar_one_or_none() is None:
                dep_sub = ResourceSubscription(
                    user_id=user_a.id,
                    resource_type=dep.resource_type,
                    resource_id=dep.resource_id,
                    org_id=MARKET_ORG_ID,
                )
                async_session.add(dep_sub)
        await async_session.commit()

        # Verify connector subscription was created
        conn_sub_result = await async_session.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.user_id == user_a.id,
                ResourceSubscription.resource_type == "connector",
                ResourceSubscription.resource_id == conn_id,
            )
        )
        assert conn_sub_result.scalar_one_or_none() is not None

        # Verify MCP server subscription was created
        srv_sub_result = await async_session.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.user_id == user_a.id,
                ResourceSubscription.resource_type == "mcp_server",
                ResourceSubscription.resource_id == srv_id,
            )
        )
        assert srv_sub_result.scalar_one_or_none() is not None

    @pytest.mark.asyncio
    async def test_subscribe_agent_auto_subscribes_connection_deps(
        self, async_session: AsyncSession, user_a: User
    ) -> None:
        """Subscribe to an Agent with connector_ids and mcp_server_ids.

        Verify subscriptions are created for both connection dep types.
        """
        conn_id = str(uuid.uuid4())
        srv_id = str(uuid.uuid4())
        agent_id = str(uuid.uuid4())
        other_user_id = str(uuid.uuid4())

        connector = Connector(
            id=conn_id,
            user_id=other_user_id,
            name="Slack API",
            auth_type="api_key",
            allow_fallback=False,
        )
        server = MCPServer(
            id=srv_id,
            user_id=other_user_id,
            name="Slack MCP",
            transport="sse",
            allow_fallback=True,
        )
        async_session.add_all([connector, server])

        agent = Agent(
            id=agent_id,
            user_id=other_user_id,
            name="Slack Agent",
            connector_ids=[conn_id],
            mcp_server_ids=[srv_id],
        )
        async_session.add(agent)
        await async_session.commit()

        manifest = await resolve_solution_dependencies("agent", agent_id, async_session)
        assert len(manifest.connection_deps) == 2

        # Verify allow_fallback is correctly resolved
        conn_dep = next(d for d in manifest.connection_deps if d.resource_type == "connector")
        srv_dep = next(d for d in manifest.connection_deps if d.resource_type == "mcp_server")
        assert conn_dep.allow_fallback is False
        assert srv_dep.allow_fallback is True

        # Simulate subscribe
        agent_sub = _make_sub(user_a.id, "agent", agent_id)
        async_session.add(agent_sub)
        for dep in manifest.connection_deps:
            dep_sub = ResourceSubscription(
                user_id=user_a.id,
                resource_type=dep.resource_type,
                resource_id=dep.resource_id,
                org_id=MARKET_ORG_ID,
            )
            async_session.add(dep_sub)
        await async_session.commit()

        # Verify both subscriptions exist
        all_subs = await async_session.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.user_id == user_a.id,
            )
        )
        subs = all_subs.scalars().all()
        sub_keys = {(s.resource_type, s.resource_id) for s in subs}
        assert ("agent", agent_id) in sub_keys
        assert ("connector", conn_id) in sub_keys
        assert ("mcp_server", srv_id) in sub_keys


# ---------------------------------------------------------------------------
# Test: unsubscribe cascades connection dep cleanup
# ---------------------------------------------------------------------------


class TestUnsubscribeCascadesConnectionDeps:
    """Verify that unsubscribing a Solution cascade-deletes orphaned connection deps."""

    @pytest.mark.asyncio
    async def test_unsubscribe_cascades_connection_deps(
        self, async_session: AsyncSession, user_a: User
    ) -> None:
        """Unsubscribe from a Skill should delete connection dep subs AND credentials."""
        from fim_one.core.security.encryption import encrypt_credential

        conn_id = str(uuid.uuid4())
        srv_id = str(uuid.uuid4())
        skill_id = str(uuid.uuid4())
        other_user_id = str(uuid.uuid4())

        # Create resources
        connector = Connector(
            id=conn_id,
            user_id=other_user_id,
            name="API Connector",
            auth_type="bearer",
        )
        server = MCPServer(
            id=srv_id,
            user_id=other_user_id,
            name="MCP Server",
            transport="stdio",
        )
        skill = Skill(
            id=skill_id,
            user_id=other_user_id,
            name="Test Skill",
            content="Test",
            resource_refs=[
                {"type": "connector", "id": conn_id, "name": "API Connector"},
                {"type": "mcp_server", "id": srv_id, "name": "MCP Server"},
            ],
        )
        async_session.add_all([connector, server, skill])

        # Create subscriptions (skill + connection deps)
        sub_skill = _make_sub(user_a.id, "skill", skill_id)
        sub_conn = _make_sub(user_a.id, "connector", conn_id)
        sub_srv = _make_sub(user_a.id, "mcp_server", srv_id)
        async_session.add_all([sub_skill, sub_conn, sub_srv])

        # Create credentials
        conn_cred = ConnectorCredential(
            connector_id=conn_id,
            user_id=user_a.id,
            credentials_blob=encrypt_credential({"api_key": "test-key"}),
        )
        srv_cred = MCPServerCredential(
            server_id=srv_id,
            user_id=user_a.id,
            env_blob={"API_KEY": "test-key"},
        )
        async_session.add_all([conn_cred, srv_cred])
        await async_session.commit()

        # Simulate unsubscribe: delete main sub, then cascade-clean
        await async_session.delete(sub_skill)

        await _cascade_clean_connection_deps(
            user_id=user_a.id,
            unsubscribed_type="skill",
            unsubscribed_id=skill_id,
            db=async_session,
        )
        await async_session.commit()

        # Verify connector subscription is gone
        conn_sub_result = await async_session.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.user_id == user_a.id,
                ResourceSubscription.resource_type == "connector",
                ResourceSubscription.resource_id == conn_id,
            )
        )
        assert conn_sub_result.scalar_one_or_none() is None

        # Verify MCP server subscription is gone
        srv_sub_result = await async_session.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.user_id == user_a.id,
                ResourceSubscription.resource_type == "mcp_server",
                ResourceSubscription.resource_id == srv_id,
            )
        )
        assert srv_sub_result.scalar_one_or_none() is None

        # Verify connector credential is gone
        conn_cred_result = await async_session.execute(
            select(ConnectorCredential).where(
                ConnectorCredential.connector_id == conn_id,
                ConnectorCredential.user_id == user_a.id,
            )
        )
        assert conn_cred_result.scalar_one_or_none() is None

        # Verify MCP server credential is gone
        srv_cred_result = await async_session.execute(
            select(MCPServerCredential).where(
                MCPServerCredential.server_id == srv_id,
                MCPServerCredential.user_id == user_a.id,
            )
        )
        assert srv_cred_result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_unsubscribe_keeps_shared_connection_deps(
        self, async_session: AsyncSession, user_a: User
    ) -> None:
        """When Skill A and Skill B share a Connector, unsubscribing A keeps the Connector."""
        conn_id = str(uuid.uuid4())
        skill_a_id = str(uuid.uuid4())
        skill_b_id = str(uuid.uuid4())
        other_user_id = str(uuid.uuid4())

        connector = Connector(
            id=conn_id,
            user_id=other_user_id,
            name="Shared Connector",
            auth_type="bearer",
        )
        skill_a = Skill(
            id=skill_a_id,
            user_id=other_user_id,
            name="Skill A",
            content="Uses shared connector",
            resource_refs=[{"type": "connector", "id": conn_id, "name": "Shared"}],
        )
        skill_b = Skill(
            id=skill_b_id,
            user_id=other_user_id,
            name="Skill B",
            content="Also uses shared connector",
            resource_refs=[{"type": "connector", "id": conn_id, "name": "Shared"}],
        )
        async_session.add_all([connector, skill_a, skill_b])

        # User subscriptions: both skills + the shared connector
        sub_a = _make_sub(user_a.id, "skill", skill_a_id)
        sub_b = _make_sub(user_a.id, "skill", skill_b_id)
        sub_conn = _make_sub(user_a.id, "connector", conn_id)
        async_session.add_all([sub_a, sub_b, sub_conn])
        await async_session.commit()

        # Unsubscribe Skill A
        await async_session.delete(sub_a)

        await _cascade_clean_connection_deps(
            user_id=user_a.id,
            unsubscribed_type="skill",
            unsubscribed_id=skill_a_id,
            db=async_session,
        )
        await async_session.commit()

        # Connector subscription should still exist (still needed by Skill B)
        conn_sub_result = await async_session.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.user_id == user_a.id,
                ResourceSubscription.resource_type == "connector",
                ResourceSubscription.resource_id == conn_id,
            )
        )
        assert conn_sub_result.scalar_one_or_none() is not None

    @pytest.mark.asyncio
    async def test_no_connection_deps_no_error(
        self, async_session: AsyncSession, user_a: User
    ) -> None:
        """Skill with no connection deps: cascade does nothing, no errors."""
        skill_id = str(uuid.uuid4())
        skill = Skill(
            id=skill_id,
            user_id=str(uuid.uuid4()),
            name="Simple Skill",
            content="No deps",
            resource_refs=None,
        )
        async_session.add(skill)
        await async_session.commit()

        # Should not raise
        await _cascade_clean_connection_deps(
            user_id=user_a.id,
            unsubscribed_type="skill",
            unsubscribed_id=skill_id,
            db=async_session,
        )


# ---------------------------------------------------------------------------
# Test: idempotent subscribe — no duplicate connection dep subscriptions
# ---------------------------------------------------------------------------


class TestSubscribeIdempotentConnectionDeps:
    """Verify that re-subscribing does not create duplicate subscriptions."""

    @pytest.mark.asyncio
    async def test_subscribe_idempotent_connection_deps(
        self, async_session: AsyncSession, user_a: User
    ) -> None:
        """Subscribe to the same Skill twice — no duplicate connector subscriptions."""
        conn_id = str(uuid.uuid4())
        skill_id = str(uuid.uuid4())
        other_user_id = str(uuid.uuid4())

        connector = Connector(
            id=conn_id,
            user_id=other_user_id,
            name="Idempotent Connector",
            auth_type="bearer",
        )
        skill = Skill(
            id=skill_id,
            user_id=other_user_id,
            name="Idempotent Skill",
            content="Test",
            resource_refs=[{"type": "connector", "id": conn_id, "name": "Conn"}],
        )
        async_session.add_all([connector, skill])
        await async_session.commit()

        # First subscribe: create Skill sub + connector dep sub
        manifest = await resolve_solution_dependencies("skill", skill_id, async_session)

        skill_sub = _make_sub(user_a.id, "skill", skill_id)
        async_session.add(skill_sub)
        for dep in manifest.connection_deps:
            existing = await async_session.execute(
                select(ResourceSubscription).where(
                    ResourceSubscription.user_id == user_a.id,
                    ResourceSubscription.resource_type == dep.resource_type,
                    ResourceSubscription.resource_id == dep.resource_id,
                )
            )
            if existing.scalar_one_or_none() is None:
                dep_sub = ResourceSubscription(
                    user_id=user_a.id,
                    resource_type=dep.resource_type,
                    resource_id=dep.resource_id,
                    org_id=MARKET_ORG_ID,
                )
                async_session.add(dep_sub)
        await async_session.commit()

        # Second subscribe attempt (simulates calling subscribe again)
        manifest2 = await resolve_solution_dependencies("skill", skill_id, async_session)
        for dep in manifest2.connection_deps:
            existing = await async_session.execute(
                select(ResourceSubscription).where(
                    ResourceSubscription.user_id == user_a.id,
                    ResourceSubscription.resource_type == dep.resource_type,
                    ResourceSubscription.resource_id == dep.resource_id,
                )
            )
            if existing.scalar_one_or_none() is None:
                dep_sub = ResourceSubscription(
                    user_id=user_a.id,
                    resource_type=dep.resource_type,
                    resource_id=dep.resource_id,
                    org_id=MARKET_ORG_ID,
                )
                async_session.add(dep_sub)
        await async_session.commit()

        # Verify only ONE connector subscription exists (no duplicates)
        conn_subs_result = await async_session.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.user_id == user_a.id,
                ResourceSubscription.resource_type == "connector",
                ResourceSubscription.resource_id == conn_id,
            )
        )
        conn_subs = conn_subs_result.scalars().all()
        assert len(conn_subs) == 1

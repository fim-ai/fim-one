"""Tests for Workflow connection dependency auto-subscribe.

Covers:
1. Subscribing to a workflow auto-subscribes its connector and MCP server deps.
2. Subscribing to a workflow with agent nodes auto-subscribes the agents and
   their transitive deps (connectors, MCP servers, KBs, skills).
3. Unsubscribing cascade-cleans orphaned connection and content deps.
4. Idempotency — re-subscribing does not create duplicate subscriptions.
5. Missing resources are handled gracefully (logged, not fatal).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.db.base import Base
from fim_one.web.api.market import (
    _cascade_clean_connection_deps,
    _cascade_clean_content_deps,
)
from fim_one.web.dependency_analyzer import resolve_solution_dependencies
from fim_one.db.models.agent import Agent
from fim_one.db.models.connector import Connector
from fim_one.db.models.connector_credential import ConnectorCredential
from fim_one.db.models.knowledge_base import KnowledgeBase
from fim_one.db.models.mcp_server import MCPServer
from fim_one.db.models.mcp_server_credential import MCPServerCredential
from fim_one.db.models.resource_subscription import ResourceSubscription
from fim_one.db.models.skill import Skill
from fim_one.db.models.user import User
from fim_one.db.models.workflow import Workflow
from fim_one.web.platform import MARKET_ORG_ID


# ---------------------------------------------------------------------------
# Fixtures -- in-memory SQLite async database
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cred_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set a stable encryption key for tests that touch credential models."""
    import fim_one.core.security.encryption as enc

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "test-wf-auto-sub-key-12345")
    enc._CREDENTIAL_KEY_RAW = "test-wf-auto-sub-key-12345"
    enc._cred_fernet_instance = None
    yield
    enc._cred_fernet_instance = None


@pytest.fixture()
async def async_session():
    """Create an in-memory SQLite database with all required tables."""
    import fim_one.db.models  # noqa: F401 -- register all models

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
        username="wf_auto_sub_user",
        email="wf_auto_sub@example.com",
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
# Helper: simulate subscribe (mirrors market.py subscribe_resource logic)
# ---------------------------------------------------------------------------


async def _simulate_subscribe(
    user_id: str,
    resource_type: str,
    resource_id: str,
    db: AsyncSession,
) -> None:
    """Simulate the subscribe_resource endpoint's auto-subscribe logic."""
    from fim_one.web.api.market import SOLUTION_TYPES

    # Create main subscription if not exists
    existing = await db.execute(
        select(ResourceSubscription).where(
            ResourceSubscription.user_id == user_id,
            ResourceSubscription.resource_type == resource_type,
            ResourceSubscription.resource_id == resource_id,
        )
    )
    if existing.scalar_one_or_none() is None:
        sub = ResourceSubscription(
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            org_id=MARKET_ORG_ID,
        )
        db.add(sub)
        await db.commit()

    # Auto-subscribe deps for Solutions
    if resource_type in SOLUTION_TYPES:
        manifest = await resolve_solution_dependencies(resource_type, resource_id, db)

        # Content deps (skip KBs -- accessed via owner delegation)
        for dep in manifest.content_deps:
            if dep.resource_type == "knowledge_base":
                continue
            existing_dep = await db.execute(
                select(ResourceSubscription).where(
                    ResourceSubscription.user_id == user_id,
                    ResourceSubscription.resource_type == dep.resource_type,
                    ResourceSubscription.resource_id == dep.resource_id,
                )
            )
            if existing_dep.scalar_one_or_none() is None:
                dep_sub = ResourceSubscription(
                    user_id=user_id,
                    resource_type=dep.resource_type,
                    resource_id=dep.resource_id,
                    org_id=MARKET_ORG_ID,
                )
                db.add(dep_sub)

        # Connection deps (connectors, MCP servers)
        for conn_dep in manifest.connection_deps:
            existing_dep = await db.execute(
                select(ResourceSubscription).where(
                    ResourceSubscription.user_id == user_id,
                    ResourceSubscription.resource_type == conn_dep.resource_type,
                    ResourceSubscription.resource_id == conn_dep.resource_id,
                )
            )
            if existing_dep.scalar_one_or_none() is None:
                dep_sub = ResourceSubscription(
                    user_id=user_id,
                    resource_type=conn_dep.resource_type,
                    resource_id=conn_dep.resource_id,
                    org_id=MARKET_ORG_ID,
                )
                db.add(dep_sub)

        await db.commit()


# ---------------------------------------------------------------------------
# Test: workflow subscribe auto-subscribes connector/MCP deps
# ---------------------------------------------------------------------------


class TestWorkflowSubscribeAutoSubscribesConnectionDeps:
    """Subscribing to a workflow should auto-create subscriptions for
    connector and MCP server connection deps found in blueprint nodes."""

    @pytest.mark.asyncio
    async def test_workflow_with_connector_and_mcp_nodes(
        self, async_session: AsyncSession, user_a: User
    ) -> None:
        """Workflow with CONNECTOR and MCP nodes creates connection dep subs."""
        other_user = str(uuid.uuid4())
        conn_id = str(uuid.uuid4())
        srv_id = str(uuid.uuid4())
        wf_id = str(uuid.uuid4())

        connector = Connector(
            id=conn_id, user_id=other_user, name="WF Connector", auth_type="bearer"
        )
        server = MCPServer(
            id=srv_id, user_id=other_user, name="WF MCP", transport="stdio"
        )
        workflow = Workflow(
            id=wf_id,
            user_id=other_user,
            name="Test Workflow",
            blueprint={
                "nodes": [
                    {"type": "CONNECTOR", "data": {"connector_id": conn_id}},
                    {"type": "MCP", "data": {"server_id": srv_id}},
                ],
                "edges": [],
            },
        )
        async_session.add_all([connector, server, workflow])
        await async_session.commit()

        await _simulate_subscribe(user_a.id, "workflow", wf_id, async_session)

        # Verify workflow subscription
        wf_sub = await async_session.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.user_id == user_a.id,
                ResourceSubscription.resource_type == "workflow",
                ResourceSubscription.resource_id == wf_id,
            )
        )
        assert wf_sub.scalar_one_or_none() is not None

        # Verify connector subscription
        conn_sub = await async_session.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.user_id == user_a.id,
                ResourceSubscription.resource_type == "connector",
                ResourceSubscription.resource_id == conn_id,
            )
        )
        assert conn_sub.scalar_one_or_none() is not None

        # Verify MCP server subscription
        srv_sub = await async_session.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.user_id == user_a.id,
                ResourceSubscription.resource_type == "mcp_server",
                ResourceSubscription.resource_id == srv_id,
            )
        )
        assert srv_sub.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# Test: workflow with agent nodes -> agent + agent's deps auto-subscribed
# ---------------------------------------------------------------------------


class TestWorkflowSubscribeAutoSubscribesAgentDeps:
    """Subscribing to a workflow that contains AGENT nodes should
    auto-subscribe the agent itself plus all of its transitive deps."""

    @pytest.mark.asyncio
    async def test_workflow_with_agent_node(
        self, async_session: AsyncSession, user_a: User
    ) -> None:
        """Workflow with an AGENT node auto-subscribes the agent and its
        connector/MCP deps."""
        other_user = str(uuid.uuid4())
        conn_id = str(uuid.uuid4())
        srv_id = str(uuid.uuid4())
        agent_id = str(uuid.uuid4())
        wf_id = str(uuid.uuid4())

        connector = Connector(
            id=conn_id, user_id=other_user, name="Agent Connector", auth_type="api_key"
        )
        server = MCPServer(
            id=srv_id, user_id=other_user, name="Agent MCP", transport="sse"
        )
        agent = Agent(
            id=agent_id,
            user_id=other_user,
            name="Embedded Agent",
            connector_ids=[conn_id],
            mcp_server_ids=[srv_id],
        )
        workflow = Workflow(
            id=wf_id,
            user_id=other_user,
            name="Agent Workflow",
            blueprint={
                "nodes": [
                    {"type": "AGENT", "data": {"agent_id": agent_id}},
                ],
                "edges": [],
            },
        )
        async_session.add_all([connector, server, agent, workflow])
        await async_session.commit()

        await _simulate_subscribe(user_a.id, "workflow", wf_id, async_session)

        # Verify all subscriptions
        all_subs = await async_session.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.user_id == user_a.id,
            )
        )
        subs = all_subs.scalars().all()
        sub_keys = {(s.resource_type, s.resource_id) for s in subs}

        assert ("workflow", wf_id) in sub_keys
        assert ("agent", agent_id) in sub_keys
        assert ("connector", conn_id) in sub_keys
        assert ("mcp_server", srv_id) in sub_keys


# ---------------------------------------------------------------------------
# Test: unsubscribe cascade cleans workflow deps
# ---------------------------------------------------------------------------


class TestWorkflowUnsubscribeCascade:
    """Unsubscribing from a workflow should cascade-delete orphaned
    connection dep subscriptions and content dep subscriptions."""

    @pytest.mark.asyncio
    async def test_unsubscribe_cascades_connection_deps(
        self, async_session: AsyncSession, user_a: User
    ) -> None:
        """Unsubscribing from a workflow should delete orphaned connector sub."""
        other_user = str(uuid.uuid4())
        conn_id = str(uuid.uuid4())
        wf_id = str(uuid.uuid4())

        connector = Connector(
            id=conn_id, user_id=other_user, name="Cascade Conn", auth_type="bearer"
        )
        workflow = Workflow(
            id=wf_id,
            user_id=other_user,
            name="Cascade WF",
            blueprint={
                "nodes": [
                    {"type": "CONNECTOR", "data": {"connector_id": conn_id}},
                ],
                "edges": [],
            },
        )
        async_session.add_all([connector, workflow])

        # Pre-create subscriptions (simulating prior subscribe)
        sub_wf = _make_sub(user_a.id, "workflow", wf_id)
        sub_conn = _make_sub(user_a.id, "connector", conn_id)
        async_session.add_all([sub_wf, sub_conn])
        await async_session.commit()

        # Simulate unsubscribe: delete main sub, then cascade-clean
        await async_session.delete(sub_wf)

        await _cascade_clean_connection_deps(
            user_id=user_a.id,
            unsubscribed_type="workflow",
            unsubscribed_id=wf_id,
            db=async_session,
        )
        await async_session.commit()

        # Connector subscription should be deleted (orphaned)
        result = await async_session.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.user_id == user_a.id,
                ResourceSubscription.resource_type == "connector",
                ResourceSubscription.resource_id == conn_id,
            )
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_unsubscribe_cascades_content_deps(
        self, async_session: AsyncSession, user_a: User
    ) -> None:
        """Unsubscribing from a workflow should delete orphaned agent sub."""
        other_user = str(uuid.uuid4())
        agent_id = str(uuid.uuid4())
        wf_id = str(uuid.uuid4())

        agent = Agent(
            id=agent_id, user_id=other_user, name="Cascade Agent",
        )
        workflow = Workflow(
            id=wf_id,
            user_id=other_user,
            name="Cascade WF",
            blueprint={
                "nodes": [
                    {"type": "AGENT", "data": {"agent_id": agent_id}},
                ],
                "edges": [],
            },
        )
        async_session.add_all([agent, workflow])

        sub_wf = _make_sub(user_a.id, "workflow", wf_id)
        sub_agent = _make_sub(user_a.id, "agent", agent_id)
        async_session.add_all([sub_wf, sub_agent])
        await async_session.commit()

        # Simulate unsubscribe: delete main sub, then cascade-clean content deps
        await async_session.delete(sub_wf)

        await _cascade_clean_content_deps(
            user_id=user_a.id,
            unsubscribed_type="workflow",
            unsubscribed_id=wf_id,
            db=async_session,
        )
        await async_session.commit()

        # Agent subscription should be deleted (orphaned)
        result = await async_session.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.user_id == user_a.id,
                ResourceSubscription.resource_type == "agent",
                ResourceSubscription.resource_id == agent_id,
            )
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_unsubscribe_keeps_shared_deps(
        self, async_session: AsyncSession, user_a: User
    ) -> None:
        """When two workflows share a connector, unsubscribing one keeps
        the connector subscription (still needed by the other)."""
        other_user = str(uuid.uuid4())
        conn_id = str(uuid.uuid4())
        wf_a_id = str(uuid.uuid4())
        wf_b_id = str(uuid.uuid4())

        connector = Connector(
            id=conn_id, user_id=other_user, name="Shared Conn", auth_type="bearer"
        )
        wf_a = Workflow(
            id=wf_a_id,
            user_id=other_user,
            name="WF A",
            blueprint={
                "nodes": [
                    {"type": "CONNECTOR", "data": {"connector_id": conn_id}},
                ],
                "edges": [],
            },
        )
        wf_b = Workflow(
            id=wf_b_id,
            user_id=other_user,
            name="WF B",
            blueprint={
                "nodes": [
                    {"type": "CONNECTOR", "data": {"connector_id": conn_id}},
                ],
                "edges": [],
            },
        )
        async_session.add_all([connector, wf_a, wf_b])

        sub_a = _make_sub(user_a.id, "workflow", wf_a_id)
        sub_b = _make_sub(user_a.id, "workflow", wf_b_id)
        sub_conn = _make_sub(user_a.id, "connector", conn_id)
        async_session.add_all([sub_a, sub_b, sub_conn])
        await async_session.commit()

        # Unsubscribe WF A
        await async_session.delete(sub_a)

        await _cascade_clean_connection_deps(
            user_id=user_a.id,
            unsubscribed_type="workflow",
            unsubscribed_id=wf_a_id,
            db=async_session,
        )
        await async_session.commit()

        # Connector subscription should still exist (needed by WF B)
        result = await async_session.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.user_id == user_a.id,
                ResourceSubscription.resource_type == "connector",
                ResourceSubscription.resource_id == conn_id,
            )
        )
        assert result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# Test: idempotency
# ---------------------------------------------------------------------------


class TestWorkflowSubscribeIdempotency:
    """Re-subscribing to the same workflow should not create duplicates."""

    @pytest.mark.asyncio
    async def test_subscribe_twice_no_duplicates(
        self, async_session: AsyncSession, user_a: User
    ) -> None:
        """Subscribing to the same workflow twice creates exactly one
        subscription per resource."""
        other_user = str(uuid.uuid4())
        conn_id = str(uuid.uuid4())
        wf_id = str(uuid.uuid4())

        connector = Connector(
            id=conn_id, user_id=other_user, name="Idem Conn", auth_type="bearer"
        )
        workflow = Workflow(
            id=wf_id,
            user_id=other_user,
            name="Idem WF",
            blueprint={
                "nodes": [
                    {"type": "CONNECTOR", "data": {"connector_id": conn_id}},
                ],
                "edges": [],
            },
        )
        async_session.add_all([connector, workflow])
        await async_session.commit()

        # Subscribe twice
        await _simulate_subscribe(user_a.id, "workflow", wf_id, async_session)
        await _simulate_subscribe(user_a.id, "workflow", wf_id, async_session)

        # Verify exactly one workflow subscription
        wf_subs = await async_session.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.user_id == user_a.id,
                ResourceSubscription.resource_type == "workflow",
                ResourceSubscription.resource_id == wf_id,
            )
        )
        assert len(wf_subs.scalars().all()) == 1

        # Verify exactly one connector subscription
        conn_subs = await async_session.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.user_id == user_a.id,
                ResourceSubscription.resource_type == "connector",
                ResourceSubscription.resource_id == conn_id,
            )
        )
        assert len(conn_subs.scalars().all()) == 1


# ---------------------------------------------------------------------------
# Test: missing resources handled gracefully
# ---------------------------------------------------------------------------


class TestWorkflowMissingResourcesGraceful:
    """Missing resources in workflow blueprint should be logged and skipped,
    not cause the subscription to fail."""

    @pytest.mark.asyncio
    async def test_missing_connector_skipped(
        self, async_session: AsyncSession, user_a: User
    ) -> None:
        """Workflow references a non-existent connector. Subscription should
        succeed and the missing dep should be skipped."""
        other_user = str(uuid.uuid4())
        wf_id = str(uuid.uuid4())
        fake_conn_id = str(uuid.uuid4())  # does not exist in DB

        workflow = Workflow(
            id=wf_id,
            user_id=other_user,
            name="Missing Dep WF",
            blueprint={
                "nodes": [
                    {"type": "CONNECTOR", "data": {"connector_id": fake_conn_id}},
                ],
                "edges": [],
            },
        )
        async_session.add(workflow)
        await async_session.commit()

        # Should not raise
        manifest = await resolve_solution_dependencies("workflow", wf_id, async_session)

        # No connection deps (connector not found)
        assert len(manifest.connection_deps) == 0

    @pytest.mark.asyncio
    async def test_missing_agent_skipped(
        self, async_session: AsyncSession, user_a: User
    ) -> None:
        """Workflow references a non-existent agent. Should not fail."""
        other_user = str(uuid.uuid4())
        wf_id = str(uuid.uuid4())
        fake_agent_id = str(uuid.uuid4())  # does not exist

        workflow = Workflow(
            id=wf_id,
            user_id=other_user,
            name="Missing Agent WF",
            blueprint={
                "nodes": [
                    {"type": "AGENT", "data": {"agent_id": fake_agent_id}},
                ],
                "edges": [],
            },
        )
        async_session.add(workflow)
        await async_session.commit()

        manifest = await resolve_solution_dependencies("workflow", wf_id, async_session)

        # No deps (agent not found)
        assert len(manifest.content_deps) == 0
        assert len(manifest.connection_deps) == 0

    @pytest.mark.asyncio
    async def test_mixed_existing_and_missing_deps(
        self, async_session: AsyncSession, user_a: User
    ) -> None:
        """Workflow with both existing and missing deps. Existing ones should
        be resolved, missing ones skipped."""
        other_user = str(uuid.uuid4())
        conn_id = str(uuid.uuid4())
        fake_srv_id = str(uuid.uuid4())
        wf_id = str(uuid.uuid4())

        connector = Connector(
            id=conn_id, user_id=other_user, name="Real Conn", auth_type="bearer"
        )
        workflow = Workflow(
            id=wf_id,
            user_id=other_user,
            name="Mixed WF",
            blueprint={
                "nodes": [
                    {"type": "CONNECTOR", "data": {"connector_id": conn_id}},
                    {"type": "MCP", "data": {"server_id": fake_srv_id}},
                ],
                "edges": [],
            },
        )
        async_session.add_all([connector, workflow])
        await async_session.commit()

        manifest = await resolve_solution_dependencies("workflow", wf_id, async_session)

        # Only the real connector should appear
        assert len(manifest.connection_deps) == 1
        assert manifest.connection_deps[0].resource_id == conn_id


# ---------------------------------------------------------------------------
# Test: dependency analyzer adds agents as content deps from workflow nodes
# ---------------------------------------------------------------------------


class TestWorkflowAgentContentDeps:
    """When a workflow contains AGENT nodes, the agent itself should
    appear as a content dep (not just its transitive deps)."""

    @pytest.mark.asyncio
    async def test_agent_node_added_as_content_dep(
        self, async_session: AsyncSession, user_a: User
    ) -> None:
        """Agent referenced from a workflow node should appear in
        manifest.content_deps."""
        other_user = str(uuid.uuid4())
        agent_id = str(uuid.uuid4())
        wf_id = str(uuid.uuid4())

        agent = Agent(
            id=agent_id, user_id=other_user, name="Content Agent",
        )
        workflow = Workflow(
            id=wf_id,
            user_id=other_user,
            name="Agent Content WF",
            blueprint={
                "nodes": [
                    {"type": "AGENT", "data": {"agent_id": agent_id}},
                ],
                "edges": [],
            },
        )
        async_session.add_all([agent, workflow])
        await async_session.commit()

        manifest = await resolve_solution_dependencies("workflow", wf_id, async_session)

        content_keys = {(d.resource_type, d.resource_id) for d in manifest.content_deps}
        assert ("agent", agent_id) in content_keys

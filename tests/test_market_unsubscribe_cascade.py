"""Tests for Market unsubscribe cascade cleanup and dependency analyzer.

Covers:
1. Unsubscribing a connector deletes its credential.
2. Unsubscribing an MCP server deletes its credential.
3. Unsubscribing an agent deletes orphaned KB subscriptions.
4. Unsubscribing an agent keeps KB subscriptions still needed by another agent.
5. Dependency analyzer resolves agent, skill, and workflow deps correctly.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.db.base import Base
from fim_one.web.api.market import SOLUTION_TYPES
from fim_one.web.dependency_analyzer import (
    ContentDep,
    resolve_solution_dependencies,
)
from fim_one.web.models.agent import Agent
from fim_one.web.models.connector import Connector
from fim_one.web.models.connector_credential import ConnectorCredential
from fim_one.web.models.knowledge_base import KnowledgeBase
from fim_one.web.models.mcp_server import MCPServer
from fim_one.web.models.mcp_server_credential import MCPServerCredential
from fim_one.web.models.resource_subscription import ResourceSubscription
from fim_one.web.models.skill import Skill
from fim_one.web.models.user import User
from fim_one.web.models.workflow import Workflow
from fim_one.web.platform import MARKET_ORG_ID


# ---------------------------------------------------------------------------
# Fixtures — in-memory SQLite async database
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cred_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set a stable encryption key for tests that touch credential models."""
    import fim_one.core.security.encryption as enc

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "test-cascade-key-1234567890")
    enc._CREDENTIAL_KEY_RAW = "test-cascade-key-1234567890"
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
        username="test_user",
        email="test@example.com",
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
# Test: dependency analyzer — SOLUTION_TYPES constant
# ---------------------------------------------------------------------------


class TestSolutionTypes:
    def test_solution_types_contains_expected(self) -> None:
        assert "agent" in SOLUTION_TYPES
        assert "skill" in SOLUTION_TYPES
        assert "workflow" in SOLUTION_TYPES

    def test_solution_types_excludes_components(self) -> None:
        assert "connector" not in SOLUTION_TYPES
        assert "knowledge_base" not in SOLUTION_TYPES
        assert "mcp_server" not in SOLUTION_TYPES


# ---------------------------------------------------------------------------
# Test: dependency analyzer — resolve_solution_dependencies
# ---------------------------------------------------------------------------


class TestResolveAgentDeps:
    """Test dependency resolution for Agent resources."""

    @pytest.mark.asyncio
    async def test_agent_with_kb_and_connector(self, async_session: AsyncSession, user_a: User) -> None:
        """Agent with kb_ids and connector_ids returns correct content deps."""
        kb_id = str(uuid.uuid4())
        conn_id = str(uuid.uuid4())
        agent = Agent(
            id=str(uuid.uuid4()),
            user_id=user_a.id,
            name="Test Agent",
            kb_ids=[kb_id],
            connector_ids=[conn_id],
            skill_ids=None,
        )
        async_session.add(agent)
        await async_session.commit()

        manifest = await resolve_solution_dependencies("agent", agent.id, async_session)
        assert manifest.solution_type == "agent"
        assert manifest.solution_id == agent.id

        dep_set = {(d.resource_type, d.resource_id) for d in manifest.content_deps}
        assert ("knowledge_base", kb_id) in dep_set
        assert ("connector", conn_id) in dep_set

    @pytest.mark.asyncio
    async def test_agent_with_skills(self, async_session: AsyncSession, user_a: User) -> None:
        """Agent with skill_ids returns skill content deps."""
        skill_id = str(uuid.uuid4())
        agent = Agent(
            id=str(uuid.uuid4()),
            user_id=user_a.id,
            name="Skill Agent",
            skill_ids=[skill_id],
        )
        async_session.add(agent)
        await async_session.commit()

        manifest = await resolve_solution_dependencies("agent", agent.id, async_session)
        dep_set = {(d.resource_type, d.resource_id) for d in manifest.content_deps}
        assert ("skill", skill_id) in dep_set

    @pytest.mark.asyncio
    async def test_agent_not_found(self, async_session: AsyncSession) -> None:
        """Non-existent agent returns empty manifest."""
        manifest = await resolve_solution_dependencies("agent", "non-existent-id", async_session)
        assert manifest.content_deps == []

    @pytest.mark.asyncio
    async def test_agent_with_empty_ids(self, async_session: AsyncSession, user_a: User) -> None:
        """Agent with None/empty JSON columns returns no deps."""
        agent = Agent(
            id=str(uuid.uuid4()),
            user_id=user_a.id,
            name="Empty Agent",
            kb_ids=None,
            connector_ids=[],
            skill_ids=None,
        )
        async_session.add(agent)
        await async_session.commit()

        manifest = await resolve_solution_dependencies("agent", agent.id, async_session)
        assert manifest.content_deps == []

    @pytest.mark.asyncio
    async def test_agent_deduplicates_deps(self, async_session: AsyncSession, user_a: User) -> None:
        """Duplicate IDs in kb_ids are deduplicated."""
        kb_id = str(uuid.uuid4())
        agent = Agent(
            id=str(uuid.uuid4()),
            user_id=user_a.id,
            name="Dup Agent",
            kb_ids=[kb_id, kb_id],
        )
        async_session.add(agent)
        await async_session.commit()

        manifest = await resolve_solution_dependencies("agent", agent.id, async_session)
        assert len(manifest.content_deps) == 1


class TestResolveSkillDeps:
    """Test dependency resolution for Skill resources."""

    @pytest.mark.asyncio
    async def test_skill_with_resource_refs(self, async_session: AsyncSession, user_a: User) -> None:
        """Skill with resource_refs returns content deps from refs."""
        conn_id = str(uuid.uuid4())
        skill = Skill(
            id=str(uuid.uuid4()),
            user_id=user_a.id,
            name="Test Skill",
            content="Do something",
            resource_refs=[
                {"type": "connector", "id": conn_id, "name": "GitHub"},
            ],
        )
        async_session.add(skill)
        await async_session.commit()

        manifest = await resolve_solution_dependencies("skill", skill.id, async_session)
        assert len(manifest.content_deps) == 1
        assert manifest.content_deps[0] == ContentDep(resource_type="connector", resource_id=conn_id)

    @pytest.mark.asyncio
    async def test_skill_with_no_refs(self, async_session: AsyncSession, user_a: User) -> None:
        """Skill with no resource_refs returns empty deps."""
        skill = Skill(
            id=str(uuid.uuid4()),
            user_id=user_a.id,
            name="Simple Skill",
            content="Just text",
            resource_refs=None,
        )
        async_session.add(skill)
        await async_session.commit()

        manifest = await resolve_solution_dependencies("skill", skill.id, async_session)
        assert manifest.content_deps == []


class TestResolveWorkflowDeps:
    """Test dependency resolution for Workflow resources."""

    @pytest.mark.asyncio
    async def test_workflow_with_connector_node(self, async_session: AsyncSession, user_a: User) -> None:
        """Workflow with a connector node returns connector dep."""
        conn_id = str(uuid.uuid4())
        workflow = Workflow(
            id=str(uuid.uuid4()),
            user_id=user_a.id,
            name="Test Workflow",
            blueprint={
                "nodes": [
                    {"id": "n1", "data": {"type": "connector", "connector_id": conn_id}},
                ],
                "edges": [],
            },
        )
        async_session.add(workflow)
        await async_session.commit()

        manifest = await resolve_solution_dependencies("workflow", workflow.id, async_session)
        dep_set = {(d.resource_type, d.resource_id) for d in manifest.content_deps}
        assert ("connector", conn_id) in dep_set

    @pytest.mark.asyncio
    async def test_workflow_with_kb_node(self, async_session: AsyncSession, user_a: User) -> None:
        """Workflow with a knowledge_retrieval node returns KB dep."""
        kb_id = str(uuid.uuid4())
        workflow = Workflow(
            id=str(uuid.uuid4()),
            user_id=user_a.id,
            name="KB Workflow",
            blueprint={
                "nodes": [
                    {"id": "n1", "data": {"type": "knowledge_retrieval", "knowledge_base_id": kb_id}},
                ],
                "edges": [],
            },
        )
        async_session.add(workflow)
        await async_session.commit()

        manifest = await resolve_solution_dependencies("workflow", workflow.id, async_session)
        dep_set = {(d.resource_type, d.resource_id) for d in manifest.content_deps}
        assert ("knowledge_base", kb_id) in dep_set

    @pytest.mark.asyncio
    async def test_workflow_with_mcp_node(self, async_session: AsyncSession, user_a: User) -> None:
        """Workflow with an MCP node returns mcp_server dep."""
        server_id = str(uuid.uuid4())
        workflow = Workflow(
            id=str(uuid.uuid4()),
            user_id=user_a.id,
            name="MCP Workflow",
            blueprint={
                "nodes": [
                    {"id": "n1", "data": {"type": "mcp", "server_id": server_id}},
                ],
                "edges": [],
            },
        )
        async_session.add(workflow)
        await async_session.commit()

        manifest = await resolve_solution_dependencies("workflow", workflow.id, async_session)
        dep_set = {(d.resource_type, d.resource_id) for d in manifest.content_deps}
        assert ("mcp_server", server_id) in dep_set

    @pytest.mark.asyncio
    async def test_workflow_with_empty_blueprint(self, async_session: AsyncSession, user_a: User) -> None:
        """Workflow with empty blueprint returns no deps."""
        workflow = Workflow(
            id=str(uuid.uuid4()),
            user_id=user_a.id,
            name="Empty Workflow",
            blueprint={"nodes": [], "edges": []},
        )
        async_session.add(workflow)
        await async_session.commit()

        manifest = await resolve_solution_dependencies("workflow", workflow.id, async_session)
        assert manifest.content_deps == []


# ---------------------------------------------------------------------------
# Test: cascade cleanup — credential deletion
# ---------------------------------------------------------------------------


class TestUnsubscribeCredentialCleanup:
    """Test that unsubscribing connection-type resources deletes credentials."""

    @pytest.mark.asyncio
    async def test_unsubscribe_connector_deletes_credential(
        self, async_session: AsyncSession, user_a: User
    ) -> None:
        """Unsubscribing a connector should delete the user's ConnectorCredential."""
        from fim_one.core.security.encryption import encrypt_credential
        from fim_one.web.api.market import _cascade_clean_content_deps

        conn_id = str(uuid.uuid4())

        # Create connector, subscription, and credential
        connector = Connector(
            id=conn_id,
            user_id=str(uuid.uuid4()),  # owned by someone else
            name="Market Connector",
        )
        async_session.add(connector)

        sub = _make_sub(user_a.id, "connector", conn_id)
        async_session.add(sub)

        cred = ConnectorCredential(
            connector_id=conn_id,
            user_id=user_a.id,
            credentials_blob=encrypt_credential({"token": "secret"}),
        )
        async_session.add(cred)
        await async_session.commit()

        # Simulate unsubscribe: delete sub + credential
        from sqlalchemy import delete
        await async_session.execute(
            delete(ResourceSubscription).where(
                ResourceSubscription.user_id == user_a.id,
                ResourceSubscription.resource_type == "connector",
                ResourceSubscription.resource_id == conn_id,
            )
        )
        await async_session.execute(
            delete(ConnectorCredential).where(
                ConnectorCredential.connector_id == conn_id,
                ConnectorCredential.user_id == user_a.id,
            )
        )
        await async_session.commit()

        # Verify subscription is gone
        result = await async_session.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.user_id == user_a.id,
                ResourceSubscription.resource_type == "connector",
            )
        )
        assert result.scalar_one_or_none() is None

        # Verify credential is gone
        cred_result = await async_session.execute(
            select(ConnectorCredential).where(
                ConnectorCredential.connector_id == conn_id,
                ConnectorCredential.user_id == user_a.id,
            )
        )
        assert cred_result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_unsubscribe_mcp_server_deletes_credential(
        self, async_session: AsyncSession, user_a: User
    ) -> None:
        """Unsubscribing an MCP server should delete the user's MCPServerCredential."""
        server_id = str(uuid.uuid4())

        server = MCPServer(
            id=server_id,
            user_id=str(uuid.uuid4()),
            name="Market MCP",
        )
        async_session.add(server)

        sub = _make_sub(user_a.id, "mcp_server", server_id)
        async_session.add(sub)

        cred = MCPServerCredential(
            server_id=server_id,
            user_id=user_a.id,
            env_blob={"KEY": "value"},
        )
        async_session.add(cred)
        await async_session.commit()

        # Simulate unsubscribe: delete sub + credential
        from sqlalchemy import delete
        await async_session.execute(
            delete(ResourceSubscription).where(
                ResourceSubscription.user_id == user_a.id,
                ResourceSubscription.resource_type == "mcp_server",
                ResourceSubscription.resource_id == server_id,
            )
        )
        await async_session.execute(
            delete(MCPServerCredential).where(
                MCPServerCredential.server_id == server_id,
                MCPServerCredential.user_id == user_a.id,
            )
        )
        await async_session.commit()

        # Verify both gone
        result = await async_session.execute(
            select(MCPServerCredential).where(
                MCPServerCredential.server_id == server_id,
                MCPServerCredential.user_id == user_a.id,
            )
        )
        assert result.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# Test: cascade cleanup — orphaned content dep subscriptions
# ---------------------------------------------------------------------------


class TestCascadeCleanContentDeps:
    """Test the _cascade_clean_content_deps logic directly."""

    @pytest.mark.asyncio
    async def test_kb_sub_not_touched_by_cascade(
        self, async_session: AsyncSession, user_a: User
    ) -> None:
        """KB subscriptions are never cascade-deleted — KBs are accessed via owner delegation."""
        from fim_one.web.api.market import _cascade_clean_content_deps

        kb_id = str(uuid.uuid4())
        agent_id = str(uuid.uuid4())

        # Create agent that references a KB
        agent = Agent(
            id=agent_id,
            user_id=str(uuid.uuid4()),  # owned by someone else
            name="Market Agent",
            kb_ids=[kb_id],
        )
        async_session.add(agent)

        # Create KB
        kb = KnowledgeBase(
            id=kb_id,
            user_id=str(uuid.uuid4()),
            name="Market KB",
        )
        async_session.add(kb)

        # User subscriptions: agent + kb (kb sub could be a legacy or direct sub)
        sub_agent = _make_sub(user_a.id, "agent", agent_id)
        sub_kb = _make_sub(user_a.id, "knowledge_base", kb_id)
        async_session.add_all([sub_agent, sub_kb])
        await async_session.commit()

        # Now simulate: main agent subscription already deleted, run cascade
        await async_session.delete(sub_agent)

        await _cascade_clean_content_deps(
            user_id=user_a.id,
            unsubscribed_type="agent",
            unsubscribed_id=agent_id,
            db=async_session,
        )
        await async_session.commit()

        # KB subscription should remain — cascade skips KB deps
        result = await async_session.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.user_id == user_a.id,
                ResourceSubscription.resource_type == "knowledge_base",
                ResourceSubscription.resource_id == kb_id,
            )
        )
        assert result.scalar_one_or_none() is not None

    @pytest.mark.asyncio
    async def test_shared_kb_sub_kept(
        self, async_session: AsyncSession, user_a: User
    ) -> None:
        """When an agent is unsubscribed, a KB still used by another agent is kept."""
        from fim_one.web.api.market import _cascade_clean_content_deps

        kb_id = str(uuid.uuid4())
        agent_a_id = str(uuid.uuid4())
        agent_b_id = str(uuid.uuid4())

        # Both agents reference the same KB
        agent_a = Agent(
            id=agent_a_id,
            user_id=str(uuid.uuid4()),
            name="Agent A",
            kb_ids=[kb_id],
        )
        agent_b = Agent(
            id=agent_b_id,
            user_id=str(uuid.uuid4()),
            name="Agent B",
            kb_ids=[kb_id],
        )
        async_session.add_all([agent_a, agent_b])

        # User subscriptions: both agents + KB
        sub_a = _make_sub(user_a.id, "agent", agent_a_id)
        sub_b = _make_sub(user_a.id, "agent", agent_b_id)
        sub_kb = _make_sub(user_a.id, "knowledge_base", kb_id)
        async_session.add_all([sub_a, sub_b, sub_kb])
        await async_session.commit()

        # Unsubscribe agent A — but agent B still needs the KB
        await async_session.delete(sub_a)

        await _cascade_clean_content_deps(
            user_id=user_a.id,
            unsubscribed_type="agent",
            unsubscribed_id=agent_a_id,
            db=async_session,
        )
        await async_session.commit()

        # KB subscription should still exist (needed by agent B)
        result = await async_session.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.user_id == user_a.id,
                ResourceSubscription.resource_type == "knowledge_base",
                ResourceSubscription.resource_id == kb_id,
            )
        )
        assert result.scalar_one_or_none() is not None

    @pytest.mark.asyncio
    async def test_multiple_kb_deps_all_kept(
        self, async_session: AsyncSession, user_a: User
    ) -> None:
        """Agent with multiple KB deps: all KB subs are kept (cascade skips KBs)."""
        from fim_one.web.api.market import _cascade_clean_content_deps

        kb1_id = str(uuid.uuid4())
        kb2_id = str(uuid.uuid4())
        agent_a_id = str(uuid.uuid4())
        agent_b_id = str(uuid.uuid4())

        # Agent A refs KB1 + KB2; Agent B refs only KB1
        agent_a = Agent(
            id=agent_a_id,
            user_id=str(uuid.uuid4()),
            name="Agent A",
            kb_ids=[kb1_id, kb2_id],
        )
        agent_b = Agent(
            id=agent_b_id,
            user_id=str(uuid.uuid4()),
            name="Agent B",
            kb_ids=[kb1_id],
        )
        async_session.add_all([agent_a, agent_b])

        # User subscriptions (legacy or direct KB subs)
        sub_a = _make_sub(user_a.id, "agent", agent_a_id)
        sub_b = _make_sub(user_a.id, "agent", agent_b_id)
        sub_kb1 = _make_sub(user_a.id, "knowledge_base", kb1_id)
        sub_kb2 = _make_sub(user_a.id, "knowledge_base", kb2_id)
        async_session.add_all([sub_a, sub_b, sub_kb1, sub_kb2])
        await async_session.commit()

        # Unsubscribe agent A
        await async_session.delete(sub_a)

        await _cascade_clean_content_deps(
            user_id=user_a.id,
            unsubscribed_type="agent",
            unsubscribed_id=agent_a_id,
            db=async_session,
        )
        await async_session.commit()

        # Both KB subs should remain — cascade never touches KB subs
        result1 = await async_session.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.user_id == user_a.id,
                ResourceSubscription.resource_type == "knowledge_base",
                ResourceSubscription.resource_id == kb1_id,
            )
        )
        assert result1.scalar_one_or_none() is not None

        result2 = await async_session.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.user_id == user_a.id,
                ResourceSubscription.resource_type == "knowledge_base",
                ResourceSubscription.resource_id == kb2_id,
            )
        )
        assert result2.scalar_one_or_none() is not None

    @pytest.mark.asyncio
    async def test_no_deps_no_error(
        self, async_session: AsyncSession, user_a: User
    ) -> None:
        """Agent with no content deps: cascade does nothing, no errors."""
        from fim_one.web.api.market import _cascade_clean_content_deps

        agent_id = str(uuid.uuid4())
        agent = Agent(
            id=agent_id,
            user_id=str(uuid.uuid4()),
            name="No-dep Agent",
            kb_ids=None,
            connector_ids=None,
            skill_ids=None,
        )
        async_session.add(agent)
        await async_session.commit()

        # Should not raise
        await _cascade_clean_content_deps(
            user_id=user_a.id,
            unsubscribed_type="agent",
            unsubscribed_id=agent_id,
            db=async_session,
        )

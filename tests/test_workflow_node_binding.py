"""B15: a workflow's steps use resources through the workflow, as its author's delegation.

Connector, MCP and Knowledge Retrieval steps used to judge visibility by
whoever ran the workflow: someone running a shared workflow needed their
own subscription to every connector and MCP server in it, and got an empty
result from a knowledge base they did not own. The workflow is now the
binding, as an agent is in chat: a resource is usable while the workflow's
author can use it, whoever runs the workflow. Credentials still resolve for
the runner.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.assembly.principal import Binding, CallContext, provisional_principal
from fim_one.core.workflow.nodes import (
    ConnectorExecutor,
    KnowledgeRetrievalExecutor,
    MCPExecutor,
)
from fim_one.core.workflow.types import (
    ExecutionContext,
    NodeResult,
    NodeStatus,
    NodeType,
    WorkflowNodeDef,
)
from fim_one.core.workflow.variable_store import VariableStore
from fim_one.db.base import Base
from fim_one.db.models.knowledge_base import KnowledgeBase

from .assembly import matrix as m

pytestmark = pytest.mark.timeout(60)


@pytest.fixture()
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'binding.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _context(scene: m.Scene, *, author: str = "owner", runner: str | None = None) -> ExecutionContext:
    """A run by *runner* of a workflow whose author is the scene's *author*."""
    runner_id = scene.users[runner] if runner else scene.actor
    return ExecutionContext(
        run_id="run-1",
        user_id=runner_id,
        workflow_id=scene.ids["workflow"],
        call_context=CallContext(
            principal=provisional_principal(runner_id),
            binding=Binding(
                kind="workflow", id=scene.ids["workflow"], owner_id=scene.users[author]
            ),
            entry="workflow_node",
            workflow_run_id="run-1",
        ),
    )


async def _connector(scene: m.Scene, context: ExecutionContext, resource: str = "api_bearer") -> NodeResult:
    node = WorkflowNodeDef(
        id="c1",
        type=NodeType.CONNECTOR,
        data={
            "connector_id": scene.ids[resource],
            "action_id": scene.actions[resource]["ping"],
            "parameters": {},
        },
    )
    return await ConnectorExecutor().execute(node, VariableStore(), context)


async def _mcp(scene: m.Scene, context: ExecutionContext, resource: str = "mcp_http") -> NodeResult:
    node = WorkflowNodeDef(
        id="m1",
        type=NodeType.MCP,
        data={"server_id": scene.ids[resource], "tool_name": "echo", "parameters": {}},
    )
    return await MCPExecutor().execute(node, VariableStore(), context)


class _Manager:
    """A knowledge-base manager that records whose namespace it was asked to read."""

    def __init__(self) -> None:
        self.read: list[tuple[str, str]] = []

    async def retrieve(self, _query: str, *, kb_id: str, user_id: str, **_: Any) -> list[Any]:
        from types import SimpleNamespace

        self.read.append((kb_id, user_id))
        return [SimpleNamespace(content="the leave policy", score=0.9)]


async def _kb(
    scene: m.Scene, context: ExecutionContext, kb_ids: list[str] | None = None
) -> tuple[NodeResult, _Manager, VariableStore]:
    manager = _Manager()
    node = WorkflowNodeDef(
        id="k1",
        type=NodeType.KNOWLEDGE_RETRIEVAL,
        data={"kb_ids": kb_ids or [scene.ids["kb"]], "query_template": "leave policy"},
    )
    store = VariableStore()
    with patch("fim_one.web.deps.get_kb_manager", lambda: manager):
        result = await KnowledgeRetrievalExecutor().execute(node, store, context)
    return result, manager, store


class TestARunnerNeedsNoSubscriptionOfTheirOwn:
    """What the author can use, the workflow can use, for whoever runs it."""

    async def test_connector_step(self, factory: async_sessionmaker[AsyncSession]) -> None:
        """A member who never subscribed got "not found or not accessible"."""
        scene = await m.seed_scene(factory, m.SceneKey("member", True, True, False))
        with m.outside_world(factory) as out:
            result = await _connector(scene, _context(scene))

        assert result.status == NodeStatus.COMPLETED, result.error
        # The owner allowed fallback and the member saved no credential.
        assert out.http[-1]["authorization"] == f"Bearer {m.OWNER_TOKEN}"

    async def test_mcp_step(self, factory: async_sessionmaker[AsyncSession]) -> None:
        scene = await m.seed_scene(factory, m.SceneKey("outsider", True, True, True))
        with m.outside_world(factory) as out:
            result = await _mcp(scene, _context(scene))

        assert result.status == NodeStatus.COMPLETED, result.error
        # Opened with the runner's own credential.
        assert out.mcp_connects[scene.names["mcp_http"]]["headers"] == m.PERSONAL_HEADERS

    async def test_knowledge_retrieval_step(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Someone else running the workflow got an empty result and no error."""
        scene = await m.seed_scene(factory, m.SceneKey("subscriber", True, True, False))
        with m.outside_world(factory):
            result, manager, store = await _kb(scene, _context(scene))

        assert result.status == NodeStatus.COMPLETED, result.error
        assert result.output == "Retrieved 1 results"
        # Read from the author's namespace, where the content lives.
        assert manager.read == [(scene.ids["kb"], scene.users["owner"])]
        assert await store.get("k1.output") == "the leave policy"


class TestTheCredentialIsStillTheRunners:
    async def test_seeing_it_through_the_workflow_is_not_a_credential(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await m.seed_scene(factory, m.SceneKey("member", False, True, False))
        with m.outside_world(factory) as out:
            connector = await _connector(scene, _context(scene))
            mcp = await _mcp(scene, _context(scene))

        assert connector.status == NodeStatus.FAILED
        assert "requires your own" in (connector.error or "")
        assert mcp.status == NodeStatus.FAILED
        assert out.http == [] and out.mcp_connects == {}

    async def test_a_database_connector_stays_its_owners(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """It answered "action not found"."""
        scene = await m.seed_scene(factory, m.SceneKey("subscriber", True, True, False))
        node = WorkflowNodeDef(
            id="c1",
            type=NodeType.CONNECTOR,
            data={"connector_id": scene.ids["db"], "action_id": "x", "parameters": {}},
        )
        with m.outside_world(factory):
            result = await ConnectorExecutor().execute(node, VariableStore(), _context(scene))

        assert result.status == NodeStatus.FAILED
        assert "can only be used by its owner" in (result.error or "")


class TestWhenTheAuthorCannotUseItNobodyCan:
    """The runner's own access does not stand in for the author's."""

    async def test_connector_and_mcp_steps(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """A subscriber's run went through on the subscriber's own subscription."""
        scene = await m.seed_scene(factory, m.SceneKey("subscriber", True, True, True))
        context = _context(scene, author="outsider")
        with m.outside_world(factory) as out:
            connector = await _connector(scene, context)
            mcp = await _mcp(scene, context)

        for result in (connector, mcp):
            assert result.status == NodeStatus.FAILED
            assert "not found or not accessible" in (result.error or "")
        assert out.http == [] and out.mcp_connects == {}

    async def test_a_switched_off_resource(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await m.seed_scene(factory, m.SceneKey("member", True, False, False))
        with m.outside_world(factory):
            connector = await _connector(scene, _context(scene))
            mcp = await _mcp(scene, _context(scene))
            kb, manager, _ = await _kb(scene, _context(scene))

        for result in (connector, mcp, kb):
            assert result.status == NodeStatus.FAILED
            assert "is disabled" in (result.error or "")
        assert manager.read == []

    async def test_a_knowledge_base_the_author_does_not_own_fails_the_step(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Even for a runner who owns it: it returned results for them, nothing for others."""
        scene = await m.seed_scene(factory, m.SceneKey("subscriber", True, True, False))
        async with factory() as s:
            s.add(KnowledgeBase(id="kb-of-the-runner", user_id=scene.actor, name="Mine"))
            await s.commit()

        with m.outside_world(factory):
            result, manager, _ = await _kb(scene, _context(scene), ["kb-of-the-runner"])

        assert result.status == NodeStatus.FAILED
        assert "not found or not accessible" in (result.error or "")
        assert manager.read == []

    async def test_one_unusable_knowledge_base_fails_the_step_before_any_is_read(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await m.seed_scene(factory, m.SceneKey("owner", True, True, False))
        with m.outside_world(factory):
            result, manager, _ = await _kb(
                scene, _context(scene), [scene.ids["kb"], "no-such-kb"]
            )

        assert result.status == NodeStatus.FAILED
        assert manager.read == []


class TestAContextBuiltByHand:
    """No run service, no binding: the step acts as its user directly."""

    async def test_steps_are_judged_by_the_user_themselves(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await m.seed_scene(factory, m.SceneKey("member", True, True, False))
        bare = ExecutionContext(run_id="r", user_id=scene.actor, workflow_id="w")
        with m.outside_world(factory):
            connector = await _connector(scene, bare)
            kb, manager, _ = await _kb(scene, bare)

        assert connector.status == NodeStatus.FAILED
        assert "not found or not accessible" in (connector.error or "")
        assert kb.status == NodeStatus.FAILED and manager.read == []

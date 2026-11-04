"""Workflow nodes refuse disabled resources and runs without an identity.

Chat stops offering a disabled connector or knowledge base; the workflow
nodes kept calling them (T10 W11). Connector, MCP and knowledge-base nodes
also skipped their visibility filter when ``context.user_id`` was empty; they
now fail instead.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.core.workflow.nodes import (
    ConnectorExecutor,
    KnowledgeRetrievalExecutor,
    MCPExecutor,
)
from fim_one.core.workflow.types import (
    ExecutionContext,
    NodeStatus,
    NodeType,
    WorkflowNodeDef,
)
from fim_one.core.workflow.variable_store import VariableStore
from fim_one.db.base import Base
from fim_one.db.models.connector import Connector, ConnectorAction
from fim_one.db.models.knowledge_base import KnowledgeBase
from fim_one.db.models.user import User


@pytest.fixture()
async def db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    import fim_one.db.models  # noqa: F401

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _cs() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    with patch("fim_one.db.create_session", _cs):
        yield factory
    await engine.dispose()


async def _owner(factory: async_sessionmaker[AsyncSession]) -> str:
    async with factory() as s:
        user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4()}@x.io")
        s.add(user)
        await s.commit()
        return user.id


def _ctx(user_id: str) -> ExecutionContext:
    return ExecutionContext(run_id="r", user_id=user_id, workflow_id="w")


class TestDisabledConnector:
    async def _setup(
        self, factory: async_sessionmaker[AsyncSession], *, active: bool
    ) -> tuple[str, WorkflowNodeDef]:
        owner = await _owner(factory)
        async with factory() as s:
            conn = Connector(user_id=owner, name="CRM", base_url="https://crm.example.com")
            conn.is_active = active
            s.add(conn)
            await s.flush()
            action = ConnectorAction(
                connector_id=conn.id, name="list", method="GET", path="/items"
            )
            s.add(action)
            await s.commit()
            node = WorkflowNodeDef(
                id="c1",
                type=NodeType.CONNECTOR,
                data={"connector_id": conn.id, "action_id": action.id},
            )
        return owner, node

    @pytest.mark.asyncio
    async def test_disabled_connector_fails(
        self, db: async_sessionmaker[AsyncSession]
    ) -> None:
        owner, node = await self._setup(db, active=False)
        result = await ConnectorExecutor().execute(node, VariableStore(), _ctx(owner))
        assert result.status == NodeStatus.FAILED
        assert "disabled" in (result.error or "")

    @pytest.mark.asyncio
    async def test_active_connector_is_called(
        self, db: async_sessionmaker[AsyncSession]
    ) -> None:
        owner, node = await self._setup(db, active=True)
        with patch(
            "fim_one.core.tool.connector.adapter.ConnectorToolAdapter.run",
            new=AsyncMock(return_value="ok"),
        ):
            result = await ConnectorExecutor().execute(node, VariableStore(), _ctx(owner))
        assert result.status == NodeStatus.COMPLETED


class TestDisabledKnowledgeBase:
    async def _kb(self, factory: async_sessionmaker[AsyncSession], *, active: bool) -> tuple[str, str]:
        owner = await _owner(factory)
        async with factory() as s:
            kb = KnowledgeBase(user_id=owner, name="Policies")
            kb.is_active = active
            s.add(kb)
            await s.commit()
            return owner, kb.id

    def _node(self, kb_id: str) -> WorkflowNodeDef:
        return WorkflowNodeDef(
            id="kb1",
            type=NodeType.KNOWLEDGE_RETRIEVAL,
            data={"kb_id": kb_id, "query_template": "leave policy"},
        )

    @pytest.mark.asyncio
    async def test_disabled_kb_fails_without_querying(
        self, db: async_sessionmaker[AsyncSession]
    ) -> None:
        owner, kb_id = await self._kb(db, active=False)
        manager = MagicMock()
        manager.retrieve = AsyncMock(return_value=[])
        with patch("fim_one.web.deps.get_kb_manager", return_value=manager):
            result = await KnowledgeRetrievalExecutor().execute(
                self._node(kb_id), VariableStore(), _ctx(owner)
            )
        assert result.status == NodeStatus.FAILED
        assert "disabled" in (result.error or "")
        manager.retrieve.assert_not_called()

    @pytest.mark.asyncio
    async def test_active_kb_is_queried(
        self, db: async_sessionmaker[AsyncSession]
    ) -> None:
        owner, kb_id = await self._kb(db, active=True)
        manager = MagicMock()
        manager.retrieve = AsyncMock(return_value=[])
        with patch("fim_one.web.deps.get_kb_manager", return_value=manager):
            result = await KnowledgeRetrievalExecutor().execute(
                self._node(kb_id), VariableStore(), _ctx(owner)
            )
        assert result.status == NodeStatus.COMPLETED
        manager.retrieve.assert_awaited_once()


class TestMissingIdentityFailsClosed:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("executor", "node_type", "data"),
        [
            (ConnectorExecutor(), NodeType.CONNECTOR, {"connector_id": "c", "action_id": "a"}),
            (MCPExecutor(), NodeType.MCP, {"server_id": "s", "tool_name": "t"}),
            (
                KnowledgeRetrievalExecutor(),
                NodeType.KNOWLEDGE_RETRIEVAL,
                {"kb_id": "k", "query_template": "q"},
            ),
        ],
    )
    async def test_empty_user_id_fails(
        self, executor: Any, node_type: NodeType, data: dict[str, Any]
    ) -> None:
        node = WorkflowNodeDef(id="n1", type=node_type, data=data)
        result = await executor.execute(node, VariableStore(), _ctx(""))
        assert result.status == NodeStatus.FAILED
        assert "no user identity" in (result.error or "")

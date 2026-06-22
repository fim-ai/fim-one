"""allow_fallback now defaults to FALSE for newly created connectors and MCP
servers.

Sharing the owner's credential with subscribers who have no credential of their
own is a dangerous default ("anyone who subscribes silently spends the owner's
token / quota"), so it is now opt-in. These tests pin the default at every layer
that decides a *new* resource's value:

- the request schema default (what the API assumes when the client omits it), and
- the ORM column default (what the DB stores when the field is not supplied).

Existing rows were flipped to FALSE by migration ``d5f7h9j1l345``; this file
only covers the new-resource default, which is what the schema/ORM control.
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.db.base import Base
from fim_one.db.models.connector import Connector
from fim_one.db.models.mcp_server import MCPServer
from fim_one.web.schemas.connector import ConnectorCreate
from fim_one.web.schemas.mcp_server import MCPServerCreate


@pytest.fixture()
async def session() -> AsyncIterator[AsyncSession]:
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


class TestSchemaDefault:
    def test_connector_create_defaults_to_no_fallback(self) -> None:
        body = ConnectorCreate(name="GitHub", base_url="https://api.github.com")
        assert body.allow_fallback is False

    def test_connector_create_can_opt_in(self) -> None:
        body = ConnectorCreate(
            name="GitHub", base_url="https://api.github.com", allow_fallback=True
        )
        assert body.allow_fallback is True

    def test_mcp_server_create_defaults_to_no_fallback(self) -> None:
        body = MCPServerCreate(name="fs", transport="stdio", command="npx")
        assert body.allow_fallback is False

    def test_mcp_server_create_can_opt_in(self) -> None:
        body = MCPServerCreate(
            name="fs", transport="stdio", command="npx", allow_fallback=True
        )
        assert body.allow_fallback is True


class TestOrmDefault:
    @pytest.mark.asyncio
    async def test_connector_orm_default_is_false(self, session: AsyncSession) -> None:
        conn = Connector(
            user_id="u1", name="c", type="api", base_url="https://x", status="published"
        )
        session.add(conn)
        await session.flush()
        loaded = (
            await session.execute(select(Connector).where(Connector.id == conn.id))
        ).scalar_one()
        assert loaded.allow_fallback is False

    @pytest.mark.asyncio
    async def test_mcp_server_orm_default_is_false(self, session: AsyncSession) -> None:
        srv = MCPServer(user_id="u1", name="s", transport="stdio", command="npx")
        session.add(srv)
        await session.flush()
        loaded = (
            await session.execute(select(MCPServer).where(MCPServer.id == srv.id))
        ).scalar_one()
        assert loaded.allow_fallback is False

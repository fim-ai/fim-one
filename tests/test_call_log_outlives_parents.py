"""A call record stays when what it names is deleted, and blocks no delete.

``connector_call_logs`` had foreign keys to ``connectors``, ``conversations``
and ``users`` with no delete rule. With foreign keys enforced (PostgreSQL
always; SQLite because the app's engine sets ``PRAGMA foreign_keys=ON``),
deleting a conversation or a connector that had a call record failed with an
IntegrityError, and so did deleting a user whose connector someone else had
called. The columns are plain ids now.

Each test drives the delete endpoint itself, on SQLite with foreign keys on
and (with ``FIM_TEST_PG_URL`` pointing at an empty throwaway database) on
PostgreSQL.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import fim_one.db.models  # noqa: F401
from fim_one.db.base import Base
from fim_one.db.models.connector import Connector
from fim_one.db.models.connector_call_log import ConnectorCallLog
from fim_one.db.models.conversation import Conversation
from fim_one.db.models.mcp_server import MCPServer
from fim_one.db.models.user import User

pytestmark = pytest.mark.timeout(120)

_PG = os.environ.get("FIM_TEST_PG_URL")


def _backends() -> list[Any]:
    return [
        pytest.param("sqlite", id="sqlite"),
        pytest.param(
            "postgresql",
            id="postgresql",
            marks=pytest.mark.skipif(not _PG, reason="FIM_TEST_PG_URL not set"),
        ),
    ]


@pytest.fixture(params=_backends())
async def engine(request: pytest.FixtureRequest) -> AsyncIterator[AsyncEngine]:
    if request.param == "sqlite":
        sqlite = create_async_engine(
            "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}
        )

        @event.listens_for(sqlite.sync_engine, "connect")
        def _enforce_foreign_keys(dbapi_conn: Any, _record: Any) -> None:
            dbapi_conn.execute("PRAGMA foreign_keys=ON")  # as the app's engine does

        async with sqlite.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield sqlite
        await sqlite.dispose()
        return

    assert _PG is not None
    url = make_url(_PG).set(drivername="postgresql+asyncpg")
    schema = f"call_log_parents_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    pg = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with pg.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield pg
    finally:
        await pg.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest.fixture()
async def world(engine: AsyncEngine) -> SimpleNamespace:
    """An owner with a connector, an MCP server and a conversation; a second
    user who called the owner's connector; one call record of each."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = SimpleNamespace(
        owner=str(uuid.uuid4()),
        caller=str(uuid.uuid4()),
        connector=str(uuid.uuid4()),
        server=str(uuid.uuid4()),
        conversation=str(uuid.uuid4()),
        other_conversation=str(uuid.uuid4()),
        factory=factory,
    )
    async with factory() as s:
        s.add(User(id=ids.owner, email=f"{ids.owner}@x.io", username=ids.owner, is_admin=True))
        s.add(User(id=ids.caller, email=f"{ids.caller}@x.io", username=ids.caller))
        await s.flush()
        s.add(Connector(id=ids.connector, user_id=ids.owner, name="crm", base_url="https://c.test"))
        s.add(
            MCPServer(
                id=ids.server,
                user_id=ids.owner,
                name="files",
                transport="streamable_http",
                url="https://m.test/mcp",
            )
        )
        s.add(Conversation(id=ids.conversation, user_id=ids.owner, title="a", mode="react"))
        s.add(Conversation(id=ids.other_conversation, user_id=ids.owner, title="b", mode="react"))
        await s.flush()
        for conversation_id, user_id in (
            (ids.conversation, ids.owner),
            (ids.other_conversation, ids.owner),
            (None, ids.caller),  # someone else's call to the owner's connector
        ):
            s.add(
                ConnectorCallLog(
                    connector_id=ids.connector,
                    connector_name="crm",
                    action_name="list contacts",
                    conversation_id=conversation_id,
                    user_id=user_id,
                    request_method="GET",
                    request_url="https://c.test/contacts",
                    success=True,
                )
            )
        await s.commit()
    return ids


async def _records(world: SimpleNamespace) -> list[ConnectorCallLog]:
    async with world.factory() as s:
        return list((await s.execute(select(ConnectorCallLog))).scalars().all())


async def _user(db: AsyncSession, user_id: str) -> User:
    user = await db.get(User, user_id)
    assert user is not None
    return user


class TestDeletingAConversation:
    async def test_the_owner_deletes_one(self, world: SimpleNamespace) -> None:
        from fim_one.web.api.conversations import delete_conversation

        async with world.factory() as db:
            await delete_conversation(
                conversation_id=world.conversation,
                current_user=await _user(db, world.owner),
                db=db,
            )

        async with world.factory() as db:
            assert await db.get(Conversation, world.conversation) is None
        records = await _records(world)
        assert len(records) == 3
        # The record still says which conversation it was made in.
        assert world.conversation in {r.conversation_id for r in records}

    async def test_the_owner_deletes_several(self, world: SimpleNamespace) -> None:
        from fim_one.web.api.conversations import batch_delete_conversations
        from fim_one.web.schemas.conversation import BatchDeleteRequest

        async with world.factory() as db:
            await batch_delete_conversations(
                body=BatchDeleteRequest(ids=[world.conversation, world.other_conversation]),
                current_user=await _user(db, world.owner),
                db=db,
            )

        async with world.factory() as db:
            assert await db.get(Conversation, world.other_conversation) is None
        assert len(await _records(world)) == 3

    async def test_an_admin_deletes_one(self, world: SimpleNamespace) -> None:
        from fim_one.web.api.admin import admin_delete_conversation

        async with world.factory() as db:
            await admin_delete_conversation(
                conv_id=world.conversation, current_user=await _user(db, world.owner), db=db
            )

        async with world.factory() as db:
            assert await db.get(Conversation, world.conversation) is None
        assert len(await _records(world)) == 3


class TestDeletingAConnector:
    async def test_the_owner_deletes_it(self, world: SimpleNamespace) -> None:
        from fim_one.web.api.connectors import delete_connector

        async with world.factory() as db:
            await delete_connector(
                connector_id=world.connector, current_user=await _user(db, world.owner), db=db
            )

        async with world.factory() as db:
            assert await db.get(Connector, world.connector) is None
        records = await _records(world)
        assert len(records) == 3
        # What was called is still on the record, by id and by name.
        assert {(r.connector_id, r.connector_name) for r in records} == {(world.connector, "crm")}

    async def test_an_admin_deletes_it_in_a_batch(self, world: SimpleNamespace) -> None:
        from fim_one.web.api.admin_batch import BatchDeleteRequest, batch_delete_connectors

        async with world.factory() as db:
            result = await batch_delete_connectors(
                body=BatchDeleteRequest(ids=[world.connector]),
                current_user=await _user(db, world.owner),
                db=db,
            )

        assert (result.success_count, result.failed_count) == (1, 0)
        assert len(await _records(world)) == 3

    async def test_the_admin_statistics_still_list_it(self, world: SimpleNamespace) -> None:
        """A reader meets a record whose connector is gone."""
        from fim_one.web.api.admin import get_connector_stats
        from fim_one.web.api.connectors import delete_connector

        async with world.factory() as db:
            await delete_connector(
                connector_id=world.connector, current_user=await _user(db, world.owner), db=db
            )
        async with world.factory() as db:
            stats = await get_connector_stats(current_user=await _user(db, world.owner), db=db)

        assert stats.total_calls == 3
        assert [(c.connector_id, c.connector_name, c.call_count) for c in stats.top_connectors] == [
            (world.connector, "crm", 3)
        ]


class TestDeletingAUser:
    async def test_an_owner_whose_connector_someone_else_called(
        self, world: SimpleNamespace
    ) -> None:
        """User deletion does not trip over the records other users left on
        the user's connector. The user's own call records stay as well: they
        are kept until their retention period ends, naming the user by id."""
        from fim_one.web.services.user_deletion import purge_user_data

        async with world.factory() as db:
            await purge_user_data(db, world.owner)
            await db.commit()

        async with world.factory() as db:
            assert await db.get(User, world.owner) is None
            assert await db.get(Connector, world.connector) is None
        records = await _records(world)
        assert {r.connector_id for r in records} == {world.connector}
        assert world.caller in {r.user_id for r in records}
        assert world.owner in {r.user_id for r in records}


class TestDeletingAnMcpServer:
    async def test_the_owner_deletes_it(self, world: SimpleNamespace) -> None:
        """Worked before (no record named an MCP server) and works with one."""
        from fim_one.web.api.mcp_servers import delete_mcp_server

        async with world.factory() as db:
            db.add(
                ConnectorCallLog(
                    connector_name="files",
                    action_name="read_file",
                    user_id=world.owner,
                    request_method="MCP",
                    request_url="mcp://files/read_file",
                    success=True,
                    resource_kind="mcp",
                    mcp_server_id=world.server,
                )
            )
            await db.commit()
            await delete_mcp_server(
                server_id=world.server, current_user=await _user(db, world.owner), db=db
            )

        async with world.factory() as db:
            assert await db.get(MCPServer, world.server) is None
        assert world.server in {r.mcp_server_id for r in await _records(world)}

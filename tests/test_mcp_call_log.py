"""MCP calls leave a call record, and the call log can hold one.

Before this, an MCP tool call left no record from any entry point: the
``mcp`` meta-tool accepted a callback it never called, and the call log's
``connector_id`` was a required foreign key an MCP call has nothing to put
in.

Covered here, on SQLite and (with ``FIM_TEST_PG_URL`` pointing at an empty
throwaway database) PostgreSQL:

- the adapter reports every call, successful or not;
- ``mcp_tools`` wires the record with who called, from where and with whose
  credential;
- every reader of the call log copes with a row that has no connector;
- a record stays, ids intact, when its conversation or connector is deleted
  (the endpoints are driven in ``test_call_log_outlives_parents.py``);
- migration ``c1p2a1b2c3d4`` up, again, down and up.
"""

from __future__ import annotations

import importlib
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import fim_one.db.models  # noqa: F401
from fim_one.core.assembly.calls import RecordKind, call_recorder
from fim_one.core.assembly.principal import CallContext, provisional_principal
from fim_one.core.assembly.toolset import mcp_tools
from fim_one.core.mcp.adapter import MCPToolAdapter
from fim_one.db.base import Base
from fim_one.db.models.connector import Connector
from fim_one.db.models.connector_call_log import ConnectorCallLog
from fim_one.db.models.conversation import Conversation
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
    """An empty database with the application's tables and foreign keys enforced."""
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
    schema = f"mcp_call_log_{uuid.uuid4().hex[:12]}"
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
async def factory(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _create_session() -> AsyncIterator[AsyncSession]:
        async with maker() as s:
            yield s

    with patch("fim_one.db.create_session", _create_session):
        yield maker


USER = "u-caller"
OWNER = "u-owner"


@pytest.fixture()
async def world(factory: async_sessionmaker[AsyncSession]) -> SimpleNamespace:
    """A caller, an owner, the owner's connector, and a conversation of the caller's."""
    async with factory() as s:
        s.add(User(id=USER, email="caller@x.io", username="caller", is_admin=True))
        s.add(User(id=OWNER, email="owner@x.io", username="owner"))
        await s.flush()
        s.add(Connector(id="conn-1", user_id=OWNER, name="crm", base_url="https://crm.test"))
        s.add(Conversation(id="conv-1", user_id=USER, title="t", mode="react"))
        await s.commit()
    return SimpleNamespace(connector_id="conn-1", conversation_id="conv-1")


def _ctx(**overrides: Any) -> CallContext:
    fields: dict[str, Any] = {
        "principal": provisional_principal(USER),
        "binding": None,
        "entry": "chat_noagent",
        "conversation_id": "conv-1",
    }
    fields.update(overrides)
    return CallContext(**fields)


async def _rows(factory: async_sessionmaker[AsyncSession]) -> list[ConnectorCallLog]:
    async with factory() as s:
        return list(
            (await s.execute(select(ConnectorCallLog).order_by(ConnectorCallLog.action_name)))
            .scalars()
            .all()
        )


def _adapter(server: str, tool: str, outcome: Any) -> MCPToolAdapter:
    async def _call(_name: str, _arguments: dict[str, Any]) -> Any:
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return MCPToolAdapter(server_name=server, tool_def={"name": tool}, call_fn=_call)


def _result(text_: str, *, is_error: bool = False) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text_)], isError=is_error)


# ---------------------------------------------------------------------------
# The record itself
# ---------------------------------------------------------------------------


class TestAnMcpCallIsRecorded:
    async def test_a_successful_call(
        self, factory: async_sessionmaker[AsyncSession], world: SimpleNamespace
    ) -> None:
        adapter = _adapter("files", "read_file", _result("contents"))
        adapter.record_calls(
            call_recorder(_ctx(), kind="mcp", credential_source={"srv-1": _source("personal")}),
            server_id="srv-1",
        )

        assert await adapter.run(path="/a") == "contents"

        (row,) = await _rows(factory)
        assert (row.resource_kind, row.connector_id, row.mcp_server_id) == ("mcp", None, "srv-1")
        assert (row.connector_name, row.action_name) == ("files", "read_file")
        assert (row.request_method, row.request_url) == ("MCP", "mcp://files/read_file")
        assert (row.success, row.error_message) == (True, None)
        assert (row.user_id, row.conversation_id) == (USER, "conv-1")
        assert (row.entry, row.credential_source) == ("chat_noagent", "personal")
        assert row.response_time_ms is not None and row.response_time_ms >= 0

    async def test_a_call_the_server_reports_as_failed(
        self, factory: async_sessionmaker[AsyncSession], world: SimpleNamespace
    ) -> None:
        adapter = _adapter("files", "read_file", _result("no such file", is_error=True))
        adapter.record_calls(call_recorder(_ctx(), kind="mcp"), server_id="srv-1")

        assert await adapter.run(path="/a") == "[MCP Error] no such file"

        (row,) = await _rows(factory)
        assert (row.success, row.error_message) == (False, "[MCP Error] no such file")

    async def test_a_call_that_raises_is_recorded_and_still_raises(
        self, factory: async_sessionmaker[AsyncSession], world: SimpleNamespace
    ) -> None:
        adapter = _adapter("files", "read_file", ConnectionError("pipe closed"))
        adapter.record_calls(call_recorder(_ctx(), kind="mcp"), server_id="srv-1")

        with pytest.raises(ConnectionError):
            await adapter.run(path="/a")

        (row,) = await _rows(factory)
        assert (row.success, row.error_message) == (False, "ConnectionError: pipe closed")

    async def test_an_adapter_nobody_asked_to_record_writes_nothing(
        self, factory: async_sessionmaker[AsyncSession], world: SimpleNamespace
    ) -> None:
        """The instance-level servers and an owner's test connection."""
        await _adapter("files", "read_file", _result("ok")).run()

        assert await _rows(factory) == []

    async def test_a_workflow_step_names_its_run(
        self, factory: async_sessionmaker[AsyncSession], world: SimpleNamespace
    ) -> None:
        ctx = _ctx(entry="workflow_node", conversation_id=None, workflow_run_id="run-1")
        adapter = _adapter("files", "read_file", _result("ok"))
        adapter.record_calls(call_recorder(ctx, kind="mcp"), server_id="srv-1")

        await adapter.run()

        (row,) = await _rows(factory)
        assert (row.entry, row.workflow_run_id, row.conversation_id) == (
            "workflow_node",
            "run-1",
            None,
        )

    async def test_names_longer_than_their_columns_do_not_cost_the_record(
        self, factory: async_sessionmaker[AsyncSession], world: SimpleNamespace
    ) -> None:
        """PostgreSQL rejects an over-long value; the row used to be dropped."""
        record = call_recorder(_ctx(), kind="connector")

        await record(
            connector_id=world.connector_id,
            connector_name="n" * 300,
            action_name="a long action description " * 20,
            request_method="PROPPATCHING",
            request_url="https://crm.test/" + "x" * 2000,
            success=True,
        )

        (row,) = await _rows(factory)
        assert len(row.connector_name) == 200 and len(row.action_name) == 200
        assert len(row.request_method) == 10 and len(row.request_url) == 1000


def _source(value: str) -> Any:
    from fim_one.core.assembly.credentials import CredentialSource

    return CredentialSource(value)


# ---------------------------------------------------------------------------
# Through the assembly layer
# ---------------------------------------------------------------------------


class _FakeMCPClient:
    async def connect_streamable_http(self, *, name: str, **_: Any) -> list[Any]:
        return [_adapter(name, "echo", _result("pong"))]


class TestToolsBuiltByTheAssemblyLayerRecord:
    @pytest.mark.parametrize("mode", ["progressive", "legacy"])
    async def test_both_tool_modes(
        self, factory: async_sessionmaker[AsyncSession], world: SimpleNamespace, mode: Any
    ) -> None:
        server = SimpleNamespace(
            id="srv-9",
            name="search",
            transport="streamable_http",
            url="https://mcp.test/mcp?key=secret-in-url",
            command=None,
            args=None,
            working_dir=None,
            env=None,
            headers={"Authorization": "Bearer owner"},
            allow_fallback=True,
            user_id=OWNER,
        )
        with patch("fim_one.core.mcp.MCPClient", _FakeMCPClient):
            tools, _client = await mcp_tools(_ctx(), [server], mode=mode)

        (tool,) = tools
        if mode == "progressive":
            await tool.run(subcommand="call", server="search", tool="echo", parameters={})
        else:
            await tool.run()

        (row,) = await _rows(factory)
        assert (row.resource_kind, row.mcp_server_id) == ("mcp", "srv-9")
        assert row.credential_source == "owner_fallback"
        assert "secret-in-url" not in row.request_url


# ---------------------------------------------------------------------------
# Readers of the call log
# ---------------------------------------------------------------------------


async def _seed_mixed_calls(world: SimpleNamespace) -> None:
    connector = call_recorder(_ctx(), kind="connector")
    mcp = call_recorder(_ctx(), kind="mcp")
    for ok in (True, True, False):
        await connector(
            connector_id=world.connector_id,
            connector_name="crm",
            action_id="act-1",
            action_name="list contacts",
            request_method="GET",
            request_url="https://crm.test/contacts",
            response_status=200 if ok else 500,
            response_time_ms=10,
            success=ok,
        )
    for ok in (True, False, False, False):
        await mcp(
            mcp_server_id="srv-1",
            connector_name="files",
            action_name="read_file",
            request_method="MCP",
            request_url="mcp://files/read_file",
            response_time_ms=30,
            success=ok,
        )


class TestOneDatabaseConnectorIsOneEntry:
    """Records of one connector under two kinds are still one resource.

    A database call recorded before the kind existed reads ``connector``;
    where the migration could not relabel it (its connector row was gone and
    came back under the same id, a restore, a late writer), grouping by the
    kind itself listed the connector twice.
    """

    async def _seed(self, world: SimpleNamespace) -> None:
        kinds: tuple[RecordKind, ...] = ("connector", "database", "database")
        for kind in kinds:
            await call_recorder(_ctx(), kind=kind)(
                connector_id=world.connector_id,
                connector_name="warehouse",
                action_name="query",
                request_method="QUERY",
                request_url="db://warehouse/query",
                success=False,
            )

    async def test_every_per_resource_reader(
        self, factory: async_sessionmaker[AsyncSession], world: SimpleNamespace
    ) -> None:
        from fim_one.web.api.admin import get_connector_stats
        from fim_one.web.api.admin_analytics import analytics_by_connector
        from fim_one.web.api.admin_notifications import list_system_events
        from fim_one.web.api.metrics import get_connector_usage

        await self._seed(world)
        admin = SimpleNamespace(id=USER)
        async with factory() as db:
            stats = await get_connector_stats(current_user=admin, db=db)
            items = await analytics_by_connector(period="7d", top_n=20, current_user=admin, db=db)
            usage = await get_connector_usage(period="7d", _admin=admin, db=db)
            events = await list_system_events(hours=24, limit=50, current_user=admin, db=db)

        assert [(c.kind, c.call_count) for c in stats.top_connectors] == [("database", 3)]
        assert [(a.kind, a.call_count) for a in stats.top_actions] == [("database", 3)]
        assert [(i.kind, i.total_calls) for i in items] == [("database", 3)]
        assert [(c["kind"], c["total_calls"]) for c in usage.data["connectors"]] == [
            ("database", 3)
        ]
        failures = [e for e in events.events if e.event_type == "connector_failures"]
        assert [e.description for e in failures] == ["Connector 'warehouse' had 3 failures"]


class TestReadersCopeWithARowThatHasNoConnector:
    async def test_admin_connector_stats(
        self, factory: async_sessionmaker[AsyncSession], world: SimpleNamespace
    ) -> None:
        from fim_one.web.api.admin import get_connector_stats

        await _seed_mixed_calls(world)
        async with factory() as db:
            stats = await get_connector_stats(current_user=SimpleNamespace(id=USER), db=db)

        assert (stats.total_calls, stats.today_calls) == (7, 7)
        assert [
            (c.kind, c.connector_id, c.connector_name, c.call_count) for c in stats.top_connectors
        ] == [
            ("mcp", "srv-1", "files", 4),
            ("connector", "conn-1", "crm", 3),
        ]
        assert [(a.kind, a.connector_name, a.action_name) for a in stats.top_actions] == [
            ("mcp", "files", "read_file"),
            ("connector", "crm", "list contacts"),
        ]

    async def test_admin_analytics_by_connector(
        self, factory: async_sessionmaker[AsyncSession], world: SimpleNamespace
    ) -> None:
        from fim_one.web.api.admin_analytics import analytics_by_connector

        await _seed_mixed_calls(world)
        async with factory() as db:
            items = await analytics_by_connector(
                period="7d", top_n=20, current_user=SimpleNamespace(id=USER), db=db
            )

        assert [(i.kind, i.connector_id, i.total_calls, i.error_count) for i in items] == [
            ("mcp", "srv-1", 4, 3),
            ("connector", "conn-1", 3, 1),
        ]

    async def test_metrics_connector_usage(
        self, factory: async_sessionmaker[AsyncSession], world: SimpleNamespace
    ) -> None:
        from fim_one.web.api.metrics import get_connector_usage

        await _seed_mixed_calls(world)
        async with factory() as db:
            response = await get_connector_usage(
                period="7d", _admin=SimpleNamespace(id=USER), db=db
            )

        assert [
            (c["kind"], c["connector_id"], c["connector_name"], c["total_calls"])
            for c in response.data["connectors"]
        ] == [("mcp", "srv-1", "files", 4), ("connector", "conn-1", "crm", 3)]

    async def test_admin_failure_events_name_the_mcp_server(
        self, factory: async_sessionmaker[AsyncSession], world: SimpleNamespace
    ) -> None:
        from fim_one.web.api.admin_notifications import list_system_events

        await _seed_mixed_calls(world)
        async with factory() as db:
            events = await list_system_events(
                hours=24, limit=50, current_user=SimpleNamespace(id=USER), db=db
            )

        failures = [e for e in events.events if e.event_type == "connector_failures"]
        assert [e.description for e in failures] == ["MCP server 'files' had 3 failures"]
        assert failures[0].details == {
            "kind": "mcp",
            "connector_id": None,
            "mcp_server_id": "srv-1",
            "connector_name": "files",
            "failure_count": 3,
        }

    async def test_the_dashboard_counts_connector_calls_only(
        self, factory: async_sessionmaker[AsyncSession], world: SimpleNamespace
    ) -> None:
        from fim_one.web.api.dashboard import get_dashboard_stats

        await _seed_mixed_calls(world)
        async with factory() as db:
            caller = await db.get(User, USER)
            response = await get_dashboard_stats(current_user=caller, db=db)

        assert response.connector_calls_today == 3


# ---------------------------------------------------------------------------
# A record does not block deleting what it names
# ---------------------------------------------------------------------------


class TestARecordOutlivesWhatItNames:
    async def test_the_conversation_and_the_connector_can_be_deleted(
        self, factory: async_sessionmaker[AsyncSession], world: SimpleNamespace
    ) -> None:
        """Both deletes used to fail on the record's foreign keys."""
        await _seed_mixed_calls(world)

        async with factory() as db:
            await db.delete(await db.get(Conversation, world.conversation_id))
            await db.delete(await db.get(Connector, world.connector_id))
            await db.commit()

        rows = await _rows(factory)
        assert len(rows) == 7
        # The records keep the ids and names of what was called, and where.
        assert {r.conversation_id for r in rows} == {"conv-1"}
        assert {(r.connector_id, r.connector_name) for r in rows} == {
            ("conn-1", "crm"),
            (None, "files"),
        }


# ---------------------------------------------------------------------------
# Migration c1p2a1b2c3d4
# ---------------------------------------------------------------------------

_MIGRATION = "fim_one.migrations.versions.c1p2a1b2c3d4_call_log_generalize"
_NEW_COLUMNS = {"resource_kind", "mcp_server_id", "workflow_run_id", "entry", "credential_source"}


def _sync_engine_params() -> list[Any]:
    return [
        pytest.param("sqlite:///:memory:", id="sqlite"),
        pytest.param(
            _PG or "",
            id="postgresql",
            marks=pytest.mark.skipif(not _PG, reason="FIM_TEST_PG_URL not set"),
        ),
    ]


@pytest.fixture(params=_sync_engine_params())
def legacy_db(request: pytest.FixtureRequest) -> Iterator[sa.Engine]:
    """The three tables as they stood before the migration, with one old record."""
    url = make_url(request.param)
    if url.get_backend_name() == "postgresql":
        url = url.set(drivername="postgresql+psycopg2")
    engine = sa.create_engine(url, future=True)
    schema = None
    if engine.dialect.name == "postgresql":
        schema = f"c1p2_migration_{uuid.uuid4().hex[:12]}"
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine.dispose()
        engine = sa.create_engine(
            url, future=True, connect_args={"options": f"-csearch_path={schema}"}
        )
    else:

        @event.listens_for(engine, "connect")
        def _enforce_foreign_keys(dbapi_conn: Any, _record: Any) -> None:
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    with engine.begin() as conn:
        for table in ("conversations", "users"):
            conn.execute(text(f"CREATE TABLE {table} (id VARCHAR(36) PRIMARY KEY)"))
            conn.execute(text(f"INSERT INTO {table} (id) VALUES ('{table[:4]}-1')"))
        conn.execute(text("CREATE TABLE connectors (id VARCHAR(36) PRIMARY KEY, type VARCHAR(20))"))
        conn.execute(
            text(
                "INSERT INTO connectors (id, type) VALUES ('conn-1', 'api'), ('dbc-1', 'database')"
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE connector_call_logs (
                    id VARCHAR(36) PRIMARY KEY,
                    connector_id VARCHAR(36) NOT NULL REFERENCES connectors (id),
                    connector_name VARCHAR(200) NOT NULL,
                    action_id VARCHAR(36),
                    action_name VARCHAR(200) NOT NULL,
                    conversation_id VARCHAR(36) REFERENCES conversations (id),
                    user_id VARCHAR(36) REFERENCES users (id),
                    agent_id VARCHAR(36),
                    request_method VARCHAR(10) NOT NULL,
                    request_url VARCHAR(1000) NOT NULL,
                    response_status INTEGER,
                    response_time_ms INTEGER,
                    success BOOLEAN NOT NULL,
                    error_message TEXT,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP,
                    scope_rules_applied TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_connector_call_logs_connector_id"
                " ON connector_call_logs (connector_id)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO connector_call_logs (id, connector_id, connector_name, action_name,"
                " conversation_id, user_id, request_method, request_url, success)"
                " VALUES ('old-1', 'conn-1', 'crm', 'list', 'conv-1', 'user-1', 'GET',"
                " 'https://crm.test', TRUE)"
            )
        )
    yield engine
    if schema is not None:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    engine.dispose()


def _migrate(engine: sa.Engine, direction: str) -> None:
    module = importlib.import_module(_MIGRATION)
    with engine.connect() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(module, direction)()
        conn.commit()


def _shape(engine: sa.Engine) -> dict[str, Any]:
    inspector = sa.inspect(engine)
    columns = {c["name"]: c for c in inspector.get_columns("connector_call_logs")}
    return {
        "columns": set(columns),
        "connector_id_nullable": columns["connector_id"]["nullable"],
        "foreign_keys": {
            fk["constrained_columns"][0] for fk in inspector.get_foreign_keys("connector_call_logs")
        },
        "indexes": {i["name"] for i in inspector.get_indexes("connector_call_logs")},
    }


def _ids(engine: sa.Engine) -> list[tuple[str, str | None, str | None]]:
    with engine.connect() as conn:
        return [
            (r[0], r[1], r[2])
            for r in conn.execute(
                text(
                    "SELECT id, connector_id, conversation_id FROM connector_call_logs ORDER BY id"
                )
            )
        ]


class TestMigration:
    def test_upgrade_makes_room_and_keeps_old_records(self, legacy_db: sa.Engine) -> None:
        _migrate(legacy_db, "upgrade")

        shape = _shape(legacy_db)
        assert shape["columns"] >= _NEW_COLUMNS
        assert shape["connector_id_nullable"] is True
        assert shape["foreign_keys"] == set()
        assert {
            "ix_connector_call_logs_connector_id",
            "ix_connector_call_logs_mcp_server_id",
        } <= shape["indexes"]
        with legacy_db.connect() as conn:
            assert conn.execute(
                text("SELECT connector_id, resource_kind, entry FROM connector_call_logs")
            ).all() == [("conn-1", "connector", None)]

    def test_upgrade_twice_changes_nothing_more(self, legacy_db: sa.Engine) -> None:
        _migrate(legacy_db, "upgrade")
        once = _shape(legacy_db)

        _migrate(legacy_db, "upgrade")

        assert _shape(legacy_db) == once
        assert _ids(legacy_db) == [("old-1", "conn-1", "conv-1")]

    def test_after_upgrade_an_mcp_row_fits_and_deletes_are_not_blocked(
        self, legacy_db: sa.Engine
    ) -> None:
        with legacy_db.connect() as conn, pytest.raises(sa.exc.IntegrityError):
            conn.execute(text("DELETE FROM conversations WHERE id = 'conv-1'"))

        _migrate(legacy_db, "upgrade")
        with legacy_db.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO connector_call_logs (id, connector_name, action_name,"
                    " request_method, request_url, success, resource_kind, mcp_server_id)"
                    " VALUES ('mcp-1', 'files', 'echo', 'MCP', 'mcp://files/echo', TRUE,"
                    " 'mcp', 'srv-1')"
                )
            )
            conn.execute(text("DELETE FROM conversations WHERE id = 'conv-1'"))
            conn.execute(text("DELETE FROM connectors WHERE id = 'conn-1'"))
            conn.execute(text("DELETE FROM users WHERE id = 'user-1'"))

        # The old record keeps naming what is gone.
        assert _ids(legacy_db) == [("mcp-1", None, None), ("old-1", "conn-1", "conv-1")]

    def test_downgrade_restores_the_old_columns(self, legacy_db: sa.Engine) -> None:
        before = _shape(legacy_db)
        _migrate(legacy_db, "upgrade")
        with legacy_db.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO connector_call_logs (id, connector_name, action_name,"
                    " request_method, request_url, success, resource_kind, mcp_server_id)"
                    " VALUES ('mcp-1', 'files', 'echo', 'MCP', 'mcp://files/echo', TRUE,"
                    " 'mcp', 'srv-1')"
                )
            )

        _migrate(legacy_db, "downgrade")

        after = _shape(legacy_db)
        assert after["columns"] == before["columns"]
        assert after["connector_id_nullable"] is False
        # Not restored: by now a record may name a row that is gone.
        assert after["foreign_keys"] == set()
        assert "ix_connector_call_logs_mcp_server_id" not in after["indexes"]
        # The MCP row has no place in the old shape; the connector row stays.
        assert _ids(legacy_db) == [("old-1", "conn-1", "conv-1")]

        _migrate(legacy_db, "upgrade")
        assert _shape(legacy_db)["connector_id_nullable"] is True

    def test_upgrade_relabels_old_database_calls(self, legacy_db: sa.Engine) -> None:
        """An old database call read ``connector`` while new ones are written
        ``database``: one database connector, two entries in the statistics."""
        with legacy_db.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO connector_call_logs (id, connector_id, connector_name,"
                    " action_name, request_method, request_url, success)"
                    " VALUES ('old-db', 'dbc-1', 'warehouse', 'query', 'QUERY',"
                    " 'db://warehouse/query', TRUE)"
                )
            )

        _migrate(legacy_db, "upgrade")
        _migrate(legacy_db, "upgrade")

        with legacy_db.connect() as conn:
            assert conn.execute(
                text("SELECT id, resource_kind FROM connector_call_logs ORDER BY id")
            ).all() == [("old-1", "connector"), ("old-db", "database")]

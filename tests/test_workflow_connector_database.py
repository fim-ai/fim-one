"""T10 P4: a Connector step that names a database connector queries it, as chat does.

The step used to treat a database connector as an HTTP connector and failed
with "Action ... not found", for its owner too. It now runs one of the three
operations a chat with the connector gets, with the tool chat is given: the
owner alone, the connector's read-only switch and limits, personal-data
columns masked, the call recorded.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.assembly.principal import Binding, CallContext, provisional_principal
from fim_one.core.tool.connector.database.base import QueryResult
from fim_one.core.workflow.nodes import ConnectorExecutor
from fim_one.core.workflow.types import (
    ExecutionContext,
    NodeResult,
    NodeStatus,
    NodeType,
    WorkflowNodeDef,
)
from fim_one.core.workflow.variable_store import VariableStore
from fim_one.db.base import Base
from fim_one.db.models.connector import Connector
from fim_one.db.models.connector_call_log import ConnectorCallLog
from fim_one.db.models.database_schema import DatabaseSchema, SchemaColumn

from .assembly import matrix as m

pytestmark = pytest.mark.timeout(60)


@pytest.fixture()
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'dbstep.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class _Driver:
    """Stands in for the database: records the SQL it was asked to run."""

    def __init__(self) -> None:
        self.ran: list[dict[str, Any]] = []

    async def execute_query(self, sql: str, *, timeout_s: int, max_rows: int) -> QueryResult:
        self.ran.append({"sql": sql, "timeout_s": timeout_s, "max_rows": max_rows})
        return QueryResult(
            columns=["id", "email"], rows=[[1, "a@example.test"]], row_count=1
        )


class _Pool:
    def __init__(self, driver: _Driver) -> None:
        self._driver = driver

    async def get_driver(self, _connector_id: str, _config: dict[str, Any]) -> _Driver:
        return self._driver


async def _scene(
    factory: async_sessionmaker[AsyncSession], role: str = "owner", **switches: Any
) -> m.Scene:
    """A scene whose database connector has one visible table with a masked column."""
    key = m.SceneKey(
        role=role,
        allow_fallback=switches.get("allow_fallback", True),
        is_active=switches.get("is_active", True),
        has_personal=False,
    )
    scene = await m.seed_scene(factory, key)
    async with factory() as s:
        table = DatabaseSchema(
            id=f"tbl-{scene.ids['db']}", connector_id=scene.ids["db"], table_name="customers"
        )
        s.add(table)
        s.add(SchemaColumn(schema_id=table.id, column_name="id", data_type="integer"))
        s.add(
            SchemaColumn(schema_id=table.id, column_name="email", data_type="text", is_pii=True)
        )
        await s.commit()
    return scene


def _context(scene: m.Scene) -> ExecutionContext:
    """A run by the scene's actor of a workflow written by the connector's owner."""
    return ExecutionContext(
        run_id="run-db-1",
        user_id=scene.actor,
        workflow_id=scene.ids["workflow"],
        call_context=CallContext(
            principal=provisional_principal(scene.actor),
            binding=Binding(
                kind="workflow", id=scene.ids["workflow"], owner_id=scene.users["owner"]
            ),
            entry="workflow_node",
            workflow_run_id="run-db-1",
        ),
    )


async def _step(
    scene: m.Scene,
    operation: str,
    parameters: dict[str, Any] | None = None,
    *,
    store: VariableStore | None = None,
    driver: _Driver | None = None,
) -> tuple[NodeResult, VariableStore]:
    node = WorkflowNodeDef(
        id="db_1",
        type=NodeType.CONNECTOR,
        data={
            "connector_id": scene.ids["db"],
            "action_id": operation,
            "parameters": parameters or {},
            "output_variable": "rows",
        },
    )
    store = store or VariableStore()
    with patch(
        "fim_one.core.tool.connector.database.adapter.ConnectionPoolManager.get_instance",
        lambda: _Pool(driver or _Driver()),
    ):
        result = await ConnectorExecutor().execute(node, store, _context(scene))
    return result, store


async def _records(factory: async_sessionmaker[AsyncSession], scene: m.Scene) -> list[Any]:
    async with factory() as s:
        rows = await s.execute(
            select(ConnectorCallLog).where(ConnectorCallLog.connector_id == scene.ids["db"])
        )
        return list(rows.scalars().all())


class TestTheOwnerRunsADatabaseStep:
    async def test_list_tables_returns_the_visible_schema(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await _scene(factory)
        with m.outside_world(factory):
            result, store = await _step(scene, "list_tables")
        assert result.status == NodeStatus.COMPLETED, result.error
        tables = json.loads(await store.get("db_1.output"))
        assert [t["table_name"] for t in tables] == ["customers"]
        # Readable under the step's output variable too, like any step.
        assert await store.get("db_1.rows") == await store.get("db_1.output")

    async def test_query_runs_the_sql_with_variables_filled_in(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await _scene(factory)
        driver = _Driver()
        store = VariableStore()
        await store.set("start_1.customer_id", 7)
        with m.outside_world(factory):
            result, store = await _step(
                scene,
                "query",
                {"sql": "SELECT id, email FROM customers WHERE id = {{start_1.customer_id}}"},
                store=store,
                driver=driver,
            )
        assert result.status == NodeStatus.COMPLETED, result.error
        assert [call["sql"] for call in driver.ran] == [
            "SELECT id, email FROM customers WHERE id = 7"
        ]
        # The connector's own limits, as in chat.
        assert (driver.ran[0]["max_rows"], driver.ran[0]["timeout_s"]) == (1000, 30)
        output = json.loads(await store.get("db_1.output"))
        assert output["row_count"] == 1
        # A column the owner marked as personal data comes back masked.
        assert output["redacted_columns"] == ["email"]
        assert "a@example.test" not in json.dumps(output)

    async def test_the_call_is_recorded_like_one_made_from_chat(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await _scene(factory)
        with m.outside_world(factory):
            await _step(scene, "query", {"sql": "SELECT id FROM customers"})
        (record,) = await _records(factory, scene)
        assert record.resource_kind == "database"
        assert record.action_name == "query"
        assert record.user_id == scene.users["owner"]
        assert record.workflow_run_id == "run-db-1"
        assert record.entry == "workflow_node"
        assert record.credential_source == "owner"
        assert record.success is True

    async def test_a_write_is_refused_on_a_read_only_connector_and_fails_the_step(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await _scene(factory)
        driver = _Driver()
        with m.outside_world(factory):
            result, store = await _step(
                scene, "query", {"sql": "DELETE FROM customers"}, driver=driver
            )
        assert result.status == NodeStatus.FAILED
        assert (result.error or "").startswith("[SQL Safety Error]")
        assert driver.ran == []
        assert await store.get("db_1.output") is None
        (record,) = await _records(factory, scene)
        assert record.success is False

    async def test_an_operation_the_connector_does_not_have_fails_and_names_the_three(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await _scene(factory)
        with m.outside_world(factory):
            result, _ = await _step(scene, "drop_everything")
        assert result.status == NodeStatus.FAILED
        assert "has no operation 'drop_everything'" in (result.error or "")
        assert "list_tables, describe_table, query" in (result.error or "")

    async def test_a_connector_with_no_connection_saved_fails_and_says_so(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await _scene(factory)
        async with factory() as s:
            connector = await s.get(Connector, scene.ids["db"])
            assert connector is not None
            connector.db_config = None
            await s.commit()
        with m.outside_world(factory):
            result, _ = await _step(scene, "list_tables")
        assert result.status == NodeStatus.FAILED
        assert "has no connection configured" in (result.error or "")

    async def test_a_switched_off_connector_fails_the_step(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await _scene(factory, is_active=False)
        with m.outside_world(factory):
            result, _ = await _step(scene, "list_tables")
        assert result.status == NodeStatus.FAILED
        assert "is disabled" in (result.error or "")


class TestOnlyTheOwnerMayRunIt:
    """Raw SQL runs on the owner's database account, whoever asks."""

    @pytest.mark.parametrize("role", ["member", "subscriber", "outsider"])
    async def test_someone_else_running_the_owners_workflow_is_refused(
        self, factory: async_sessionmaker[AsyncSession], role: str
    ) -> None:
        scene = await _scene(factory, role)
        driver = _Driver()
        with m.outside_world(factory):
            result, _ = await _step(
                scene, "query", {"sql": "SELECT id FROM customers"}, driver=driver
            )
        assert result.status == NodeStatus.FAILED
        assert "can only be used by its owner" in (result.error or "")
        assert driver.ran == []
        assert await _records(factory, scene) == []

    async def test_the_fallback_switch_does_not_open_it(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await _scene(factory, "subscriber", allow_fallback=True)
        with m.outside_world(factory):
            result, _ = await _step(scene, "list_tables")
        assert result.status == NodeStatus.FAILED
        assert "can only be used by its owner" in (result.error or "")

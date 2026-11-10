"""A read-only connector cannot write, whatever the SQL looks like.

The keyword check let three shapes through: a data-modifying CTE,
``EXPLAIN ANALYZE`` of a write, and ``SELECT ... INTO``. Each is tried here
twice on a real database: straight at the driver, where only the read-only
transaction stands in the way, and through the query tool, where the
keyword check comes first.

Runs against PostgreSQL 16 with ``FIM_TEST_PG_URL`` and MySQL 8 with
``FIM_TEST_MYSQL_URL``; skipped without them.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from fim_one.core.tool.connector.database.adapter import DatabaseToolAdapter
from fim_one.core.tool.connector.database.base import DatabaseDriver
from fim_one.core.tool.connector.database.pool import ConnectionPoolManager

from .conftest import LiveDatabase

pytestmark = pytest.mark.timeout(60)


async def _driver(live: LiveDatabase) -> DatabaseDriver:
    return await ConnectionPoolManager.get_instance().get_driver(live.connector_id, live.config)


async def _rows(live: LiveDatabase, sql: str) -> list[list[Any]]:
    return (await (await _driver(live)).execute_query(sql)).rows


async def _tool(live: LiveDatabase, sql: str, *, read_only: bool = True) -> str:
    tool = DatabaseToolAdapter.create_tools(
        connector_name="live",
        connector_id=live.connector_id,
        db_config={**live.config, "read_only": read_only},
        schema_tables=[],
        on_call_complete=None,
    )[2]
    return await tool.run(sql=sql)


def _pg_writes(live: LiveDatabase) -> list[str]:
    schema = live.orders.split(".")[0]
    return [
        f"WITH d AS (DELETE FROM {live.orders} WHERE id = 1 RETURNING id) SELECT * FROM d",
        f"WITH d AS (UPDATE {live.orders} SET note = 'x' RETURNING id) SELECT * FROM d",
        f"WITH d AS (INSERT INTO {live.orders} VALUES (9, 'z', 'z') RETURNING id) SELECT * FROM d",
        f"EXPLAIN ANALYZE DELETE FROM {live.orders} WHERE id = 1",
        f"EXPLAIN (ANALYZE) DELETE FROM {live.orders} WHERE id = 1",
        f"EXPLAIN ANALYZE CREATE TABLE {schema}.copied AS SELECT * FROM {live.orders}",
        f"SELECT * INTO {schema}.copied FROM {live.orders}",
        f"SELECT id FROM {live.orders} WHERE id = 1 FOR UPDATE",
        f"DELETE FROM {live.orders}",
        f"CREATE TABLE {schema}.copied (id integer)",
        f"SELECT set_config('transaction_read_only', 'off', true), "
        f"(SELECT count(*) FROM {live.orders})",
    ]


async def _pg_unchanged(live: LiveDatabase) -> None:
    schema = live.orders.split(".")[0]
    assert await _rows(live, f"SELECT id, note FROM {live.orders} ORDER BY id") == [
        [1, "first"],
        [2, "second"],
    ]
    assert await _rows(
        live,
        "SELECT count(*) FROM information_schema.tables "
        f"WHERE table_schema = '{schema}' AND table_name = 'copied'",
    ) == [[0]]


class TestPostgreSQL:
    async def test_the_database_refuses_each_write_in_a_read_only_transaction(
        self, live_pg: LiveDatabase
    ) -> None:
        driver = await _driver(live_pg)
        assert driver.enforces_read_only
        for sql in _pg_writes(live_pg)[:-1]:
            if "CREATE TABLE" in sql and sql.startswith("EXPLAIN"):
                continue
            with pytest.raises(RuntimeError, match="read-only transaction"):
                await driver.execute_query(sql, read_only=True)
        await _pg_unchanged(live_pg)

    async def test_what_the_read_only_mode_lets_through_is_rolled_back(
        self, live_pg: LiveDatabase
    ) -> None:
        # PostgreSQL 16 runs this in a read-only transaction and creates
        # the table; the driver rolls the transaction back.
        driver = await _driver(live_pg)
        schema = live_pg.orders.split(".")[0]
        result = await driver.execute_query(
            f"EXPLAIN ANALYZE CREATE TABLE {schema}.copied AS SELECT * FROM {live_pg.orders}",
            read_only=True,
        )
        assert result.row_count >= 1
        await _pg_unchanged(live_pg)

    async def test_a_query_cannot_switch_its_transaction_to_read_write(
        self, live_pg: LiveDatabase
    ) -> None:
        driver = await _driver(live_pg)
        with pytest.raises(RuntimeError, match="read-write mode"):
            await driver.execute_query(_pg_writes(live_pg)[-1], read_only=True)

    async def test_the_query_tool_of_a_read_only_connector_refuses_each_write(
        self, live_pg: LiveDatabase
    ) -> None:
        for sql in _pg_writes(live_pg)[:-1]:
            text = await _tool(live_pg, sql)
            assert text.startswith(("[SQL Safety Error]", "[Error]")), (sql, text)
        await _pg_unchanged(live_pg)

    async def test_reads_still_work(self, live_pg: LiveDatabase) -> None:
        orders = live_pg.orders
        for sql, expected in [
            (f"SELECT id FROM {orders} ORDER BY id", [[1], [2]]),
            (f"WITH o AS (SELECT id FROM {orders} WHERE id > 1) SELECT * FROM o", [[2]]),
            (f"SELECT count(*) FROM (SELECT id FROM {orders}) AS sub", [[2]]),
            (f"SELECT replace(note, 'first', 'updated') FROM {orders} WHERE id = 1", [["updated"]]),
        ]:
            assert json.loads(await _tool(live_pg, sql))["rows"] == expected, sql
        for sql in (
            f"EXPLAIN SELECT * FROM {orders}",
            f"EXPLAIN ANALYZE SELECT * FROM {orders}",
            f"EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM {orders}",
            f"EXPLAIN ANALYZE WITH o AS (SELECT id FROM {orders}) SELECT * FROM o",
            # Planned, not run.
            f"EXPLAIN DELETE FROM {orders}",
        ):
            plan = json.loads(await _tool(live_pg, sql))
            assert plan["row_count"] >= 1, sql
        await _pg_unchanged(live_pg)

    async def test_a_connector_that_allows_writes_still_writes(self, live_pg: LiveDatabase) -> None:
        text = await _tool(
            live_pg,
            f"WITH d AS (DELETE FROM {live_pg.orders} WHERE id = 1 RETURNING id) SELECT * FROM d",
            read_only=False,
        )
        assert json.loads(text)["rows"] == [[1]]
        assert await _rows(live_pg, f"SELECT id FROM {live_pg.orders}") == [[2]]


class TestMySQL:
    async def test_the_database_refuses_each_write_in_a_read_only_transaction(
        self, live_mysql: LiveDatabase
    ) -> None:
        driver = await _driver(live_mysql)
        assert driver.enforces_read_only
        orders = live_mysql.orders
        for sql in (
            f"DELETE FROM {orders}",
            f"UPDATE {orders} SET note = 'x'",
            f"INSERT INTO {orders} VALUES (9, 'z', 'z')",
            f"EXPLAIN ANALYZE DELETE o FROM {orders} o JOIN {orders} p ON o.id = p.id",
            f"WITH o AS (SELECT id FROM {orders}) DELETE FROM {orders} "
            "WHERE id IN (SELECT id FROM o)",
            f"SELECT id FROM {orders} FOR UPDATE",
        ):
            with pytest.raises(RuntimeError, match="READ ONLY transaction"):
                await driver.execute_query(sql, read_only=True)
        assert await _rows(live_mysql, f"SELECT id, note FROM {orders} ORDER BY id") == [
            [1, "first"],
            [2, "second"],
        ]

    async def test_the_query_tool_refuses_what_the_transaction_does_not_cover(
        self, live_mysql: LiveDatabase
    ) -> None:
        # MySQL commits a read-only transaction and runs DDL; DDL can only
        # start a statement, where the keyword check stops it.
        orders = live_mysql.orders
        for sql in (
            f"CREATE TABLE {orders}_copied AS SELECT * FROM {orders}",
            f"DROP TABLE {orders}",
            f"ALTER TABLE {orders} ADD COLUMN extra integer",
            f"TRUNCATE TABLE {orders}",
            f"DELETE FROM {orders}",
            f"SELECT id INTO @last FROM {orders} LIMIT 1",
            f"WITH o AS (SELECT id FROM {orders}) DELETE FROM {orders} "
            "WHERE id IN (SELECT id FROM o)",
        ):
            text = await _tool(live_mysql, sql)
            assert text.startswith("[SQL Safety Error]"), (sql, text)
        assert await _rows(live_mysql, f"SELECT id, note FROM {orders} ORDER BY id") == [
            [1, "first"],
            [2, "second"],
        ]
        assert await _rows(live_mysql, f"SHOW TABLES LIKE '{orders}_copied'") == []

    async def test_reads_still_work_and_the_transaction_is_ended(
        self, live_mysql: LiveDatabase
    ) -> None:
        orders = live_mysql.orders
        text = await _tool(live_mysql, f"SELECT INSERT(note, 1, 5, 'x') FROM {orders} WHERE id = 1")
        assert json.loads(text)["rows"] == [["x"]]
        text = await _tool(
            live_mysql, f"WITH o AS (SELECT id FROM {orders} WHERE id > 1) SELECT * FROM o"
        )
        assert json.loads(text)["rows"] == [[2]]
        assert json.loads(await _tool(live_mysql, f"EXPLAIN SELECT * FROM {orders}"))["row_count"]
        # The connection goes back to its pool outside any transaction: a
        # connector that allows writes can write on it.
        text = await _tool(live_mysql, f"DELETE FROM {orders} WHERE id = 1", read_only=False)
        assert not text.startswith("["), text
        assert await _rows(live_mysql, f"SELECT id FROM {orders}") == [[2]]

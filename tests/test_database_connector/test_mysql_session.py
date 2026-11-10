"""A MySQL connection is handed out with a clean session, and bound text does not depend on it.

A pooled connection keeps user variables, named locks, ``sql_mode`` and the
client character set from whatever ran on it last. With the client
character set switched to gbk, quote-escaping a value is no longer safe:
the two-byte character 縗 ends in 0x5C, a backslash, and swallows the one
that escapes the quote after it. ``SET NAMES gbk`` is an ordinary statement
on a read-write connector, and the same connector's next query (a workflow
step with a bound value, say) ran on that connection.

Live tests need ``FIM_TEST_MYSQL_URL``; the rest run anywhere.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from fim_one.core.tool.connector.database.adapter import DatabaseToolAdapter, _DatabaseQueryTool
from fim_one.core.tool.connector.database.drivers import mysql as mysql_driver
from fim_one.core.tool.connector.database.drivers.mysql import MySQLDriver
from fim_one.core.tool.connector.database.pool import ConnectionPoolManager

from .conftest import SECRET, LiveDatabase

pytestmark = pytest.mark.timeout(60)

STATE = (
    "SELECT @@character_set_client, @@sql_mode LIKE '%NO_BACKSLASH_ESCAPES%', @kept, "
    "IS_USED_LOCK('kept_lock') IS NOT NULL, @@autocommit"
)
# What a read-write connector may leave on its connection.
LEFT_BEHIND = (
    "SET NAMES gbk",
    "SET SESSION sql_mode = 'NO_BACKSLASH_ESCAPES'",
    "SET @kept = 9",
    "SELECT GET_LOCK('kept_lock', 0)",
)
GBK_PAYLOADS = ("縗' OR 1=1 -- ", "縗' UNION SELECT id, token FROM {secrets} -- ", "\\' OR 1=1 -- ")


def _tool(live: LiveDatabase, *, read_only: bool) -> _DatabaseQueryTool:
    tool = DatabaseToolAdapter.create_tools(
        connector_name="live",
        connector_id=live.connector_id,
        db_config={**live.config, "read_only": read_only},
        schema_tables=[],
        on_call_complete=None,
    )[2]
    assert isinstance(tool, _DatabaseQueryTool)
    return tool


async def _bound(tool: _DatabaseQueryTool, sql: str, value: Any) -> str:
    async def resolve(_name: str) -> Any:
        return value

    return await tool.run_with_variables(sql, resolve)


@pytest.fixture()
async def one_connection(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """A pool of one, so that every call gets the connection the last one used."""
    create = mysql_driver.aiomysql.create_pool

    async def _one(**settings: Any) -> Any:
        return await create(**{**settings, "maxsize": 1})

    monkeypatch.setattr(mysql_driver.aiomysql, "create_pool", _one)
    yield


class TestLive:
    async def test_nothing_is_left_from_the_last_use_of_the_connection(
        self, live_mysql: LiveDatabase, one_connection: None
    ) -> None:
        read_write = _tool(live_mysql, read_only=False)
        read_only = _tool(live_mysql, read_only=True)
        connection = json.loads(await read_only.run(sql="SELECT CONNECTION_ID()"))["rows"]
        for statement in LEFT_BEHIND:
            assert not (await read_write.run(sql=statement)).startswith("["), statement
        state = json.loads(await read_only.run(sql=STATE))["rows"]
        assert state == [["utf8mb4", 0, None, 0, 1]]
        # The same connection, reused, not a new one.
        assert json.loads(await read_only.run(sql="SELECT CONNECTION_ID()"))["rows"] == connection

    async def test_a_read_write_connector_is_reset_between_its_own_calls_too(
        self, live_mysql: LiveDatabase, one_connection: None
    ) -> None:
        read_write = _tool(live_mysql, read_only=False)
        for statement in LEFT_BEHIND:
            await read_write.run(sql=statement)
        assert json.loads(await read_write.run(sql=STATE))["rows"] == [["utf8mb4", 0, None, 0, 1]]

    async def test_bound_text_is_safe_in_a_gbk_session_even_if_the_reset_is_missed(
        self, live_mysql: LiveDatabase, one_connection: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _no_reset(_self: MySQLDriver, _conn: Any) -> bool:
            return True

        monkeypatch.setattr(MySQLDriver, "_reset", _no_reset)
        read_write = _tool(live_mysql, read_only=False)
        read_only = _tool(live_mysql, read_only=True)
        await read_write.run(sql="SET NAMES gbk")
        sql = f"SELECT id, customer FROM {live_mysql.orders} WHERE customer = {{{{v}}}}"
        for payload in GBK_PAYLOADS:
            text = await _bound(read_only, sql, payload.format(secrets=live_mysql.secrets))
            assert SECRET not in text, payload
            assert json.loads(text)["rows"] == [], payload
        # And an ordinary value still finds its row.
        assert json.loads(await _bound(read_only, sql, "alice"))["rows"] == [[1, "alice"]]

    async def test_a_server_without_the_reset_command_gets_a_connection_per_call(
        self, live_mysql: LiveDatabase
    ) -> None:
        driver = await ConnectionPoolManager.get_instance().get_driver(
            live_mysql.connector_id, live_mysql.config
        )
        assert isinstance(driver, MySQLDriver)
        driver._can_reset = False
        await driver.execute_query("SET @kept = 9", read_only=False)
        first = await driver.execute_query("SELECT CONNECTION_ID(), @kept")
        second = await driver.execute_query("SELECT CONNECTION_ID(), @kept")
        assert first.rows[0][1] is None and second.rows[0][1] is None
        assert first.rows[0][0] != second.rows[0][0]

    async def test_listing_and_describing_tables_use_a_clean_session_too(
        self, live_mysql: LiveDatabase, one_connection: None
    ) -> None:
        read_write = _tool(live_mysql, read_only=False)
        await read_write.run(sql="SET NAMES gbk")
        driver = await ConnectionPoolManager.get_instance().get_driver(
            live_mysql.connector_id, live_mysql.config
        )
        columns = await driver.describe_table(f"縗' OR 1=1 -- {live_mysql.orders}")
        assert columns == []
        assert [c.column_name for c in await driver.describe_table(live_mysql.orders)] == [
            "id",
            "customer",
            "note",
        ]


class TestReset:
    def _connection(self, error: Exception | None = None) -> MagicMock:
        conn = MagicMock()
        conn._execute_command = AsyncMock()
        conn._read_ok_packet = AsyncMock(side_effect=error)
        conn.set_charset = AsyncMock()
        conn.autocommit = AsyncMock()
        return conn

    async def test_it_resets_then_states_the_character_set_and_autocommit(self) -> None:
        driver = MySQLDriver({"charset": "utf8mb4"})
        conn = self._connection()
        assert await driver._reset(conn) is True
        conn._execute_command.assert_awaited_once_with(0x1F, b"")
        conn.set_charset.assert_awaited_once_with("utf8mb4")
        conn.autocommit.assert_awaited_once_with(True)

    async def test_a_server_that_does_not_know_the_command_stops_reuse(self) -> None:
        driver = MySQLDriver({})
        unknown = RuntimeError(1047, "Unknown command")
        assert await driver._reset(self._connection(unknown)) is False
        assert driver._can_reset is False

    async def test_another_failure_discards_that_connection_only(self) -> None:
        driver = MySQLDriver({})
        assert await driver._reset(self._connection(ConnectionResetError("gone"))) is False
        assert driver._can_reset is True

    async def test_a_connection_that_failed_to_reset_is_closed_and_replaced(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        driver = MySQLDriver({})
        pooled = self._connection(ConnectionResetError("gone"))
        released: list[Any] = []

        async def _acquire() -> MagicMock:
            return pooled

        async def _release(conn: Any) -> None:
            released.append(conn)

        driver._pool = MagicMock(acquire=_acquire, release=_release)
        fresh = MagicMock()
        monkeypatch.setattr(mysql_driver.aiomysql, "connect", AsyncMock(return_value=fresh))
        async with driver._session() as conn:
            assert conn is fresh
        pooled.close.assert_called_once()
        assert released == [pooled]
        fresh.close.assert_called_once()

    def test_the_character_set_is_the_connectors_or_utf8mb4(self) -> None:
        assert MySQLDriver({})._connection_settings()["charset"] == "utf8mb4"
        assert MySQLDriver({"charset": "latin1"})._connection_settings()["charset"] == "latin1"


class TestCancellationAndTimeouts:
    """A cancelled or timed-out query says so, and leaves a usable driver."""

    SLOW = (
        "SELECT COUNT(*) FROM information_schema.columns a, information_schema.columns b, "
        "information_schema.columns c"
    )

    async def test_cancelling_the_task_raises_cancellation(self, live_mysql: LiveDatabase) -> None:
        import asyncio

        tool = _tool(live_mysql, read_only=True)
        task = asyncio.create_task(tool.run(sql=self.SLOW))
        await asyncio.sleep(0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # The driver still works: the dropped connection is not handed out.
        assert json.loads(await tool.run(sql="SELECT 1"))["rows"] == [[1]]

    @pytest.mark.parametrize("read_only", [True, False])
    async def test_a_query_past_its_time_is_reported_as_a_timeout(
        self, live_mysql: LiveDatabase, read_only: bool
    ) -> None:
        tool = DatabaseToolAdapter.create_tools(
            connector_name="live",
            connector_id=live_mysql.connector_id,
            db_config={**live_mysql.config, "read_only": read_only, "query_timeout": 1},
            schema_tables=[],
            on_call_complete=None,
        )[2]
        text = await tool.run(sql=self.SLOW)
        assert text.startswith("[Timeout]"), text
        assert json.loads(await tool.run(sql="SELECT 1"))["rows"] == [[1]]

    async def test_the_client_side_timeout_is_a_timeout_too(
        self, live_mysql: LiveDatabase, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Where the server has no limit of its own, the connection is dropped."""

        async def _no_limit(_cur: Any, _timeout_s: int) -> None:
            return None

        monkeypatch.setattr(MySQLDriver, "_limit_statement_time", staticmethod(_no_limit))
        driver = await ConnectionPoolManager.get_instance().get_driver(
            live_mysql.connector_id, live_mysql.config
        )
        with pytest.raises(TimeoutError, match="timed out after 1s"):
            await driver.execute_query(self.SLOW, timeout_s=1)
        assert (await driver.execute_query("SELECT 1")).rows == [[1]]

    async def test_nothing_is_rolled_back_on_a_connection_that_is_gone(self) -> None:
        conn = MagicMock(closed=True)
        cur = MagicMock()
        cur.execute = AsyncMock(side_effect=AssertionError("must not be called"))
        await mysql_driver._finish(conn, cur, rollback=True)

    async def test_a_failing_cleanup_does_not_hide_the_querys_own_error(self) -> None:
        conn = MagicMock(closed=False)
        cur = MagicMock()
        cur.execute = AsyncMock(side_effect=RuntimeError("Cancelled during execution"))
        await mysql_driver._finish(conn, cur, rollback=True)
        conn.close.assert_called_once()

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

import asyncio
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
    "0, @@autocommit"
)
LOCK_HELD = "SELECT IS_USED_LOCK('kept_lock') IS NOT NULL"
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
        assert json.loads(await read_write.run(sql=LOCK_HELD))["rows"] == [[0]]
        # The same connection, reused, not a new one.
        assert json.loads(await read_only.run(sql="SELECT CONNECTION_ID()"))["rows"] == connection

    async def test_a_named_lock_is_released_when_the_query_is_done(
        self, live_mysql: LiveDatabase, one_connection: None
    ) -> None:
        """Not at the next checkout: the connection may idle in the pool for hours."""
        import aiomysql  # type: ignore[import-untyped]

        lock = f"{live_mysql.orders}_lock"
        read_write = _tool(live_mysql, read_only=False)
        assert json.loads(await read_write.run(sql=f"SELECT GET_LOCK('{lock}', 0)"))["rows"] == [[1]]
        await read_write.run(sql="CREATE TEMPORARY TABLE kept_tmp (id int)")
        # Seen from another connection, while the connector's one is idle.
        other = await aiomysql.connect(
            host=live_mysql.config["host"],
            port=live_mysql.config["port"],
            user=live_mysql.config["username"],
            password=live_mysql.config["password"],
            db=live_mysql.config["database"],
        )
        try:
            async with other.cursor() as cur:
                await cur.execute(f"SELECT IS_USED_LOCK('{lock}')")
                assert await cur.fetchone() == (None,)
        finally:
            other.close()
        # And a read-only connector does not take such locks at all.
        refused = await _tool(live_mysql, read_only=True).run(sql=f"SELECT GET_LOCK('{lock}', 0)")
        assert refused.startswith("[SQL Safety Error]")

    async def test_use_does_not_move_later_queries_to_another_database(
        self, live_mysql: LiveDatabase, one_connection: None
    ) -> None:
        read_write = _tool(live_mysql, read_only=False)
        read_only = _tool(live_mysql, read_only=True)
        configured = json.loads(await read_only.run(sql="SELECT DATABASE()"))["rows"]
        assert not (await read_write.run(sql="USE mysql")).startswith("[")
        for tool in (read_only, read_write):
            assert json.loads(await tool.run(sql="SELECT DATABASE()"))["rows"] == configured
        orders = json.loads(await read_only.run(sql=f"SELECT COUNT(*) FROM {live_mysql.orders}"))
        assert orders["rows"] == [[2]]
        # A read-only connector does not take USE at all.
        assert (await read_only.run(sql="USE mysql")).startswith("[SQL Safety Error]")

    async def test_a_read_write_connector_is_reset_between_its_own_calls_too(
        self, live_mysql: LiveDatabase, one_connection: None
    ) -> None:
        read_write = _tool(live_mysql, read_only=False)
        for statement in LEFT_BEHIND:
            await read_write.run(sql=statement)
        assert json.loads(await read_write.run(sql=STATE))["rows"] == [["utf8mb4", 0, None, 0, 1]]
        assert json.loads(await read_write.run(sql=LOCK_HELD))["rows"] == [[0]]

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
        conn.select_db = AsyncMock()
        return conn

    async def test_it_resets_then_states_the_character_set_and_autocommit(self) -> None:
        driver = MySQLDriver({"charset": "utf8mb4"})
        conn = self._connection()
        assert await driver._reset(conn) is True
        conn._execute_command.assert_awaited_once_with(0x1F, b"")
        conn.set_charset.assert_awaited_once_with("utf8mb4")
        conn.autocommit.assert_awaited_once_with(True)
        conn.select_db.assert_not_awaited()

    async def test_it_goes_back_to_the_connectors_database(self) -> None:
        driver = MySQLDriver({"database": "app"})
        conn = self._connection()
        assert await driver._reset(conn) is True
        conn.select_db.assert_awaited_once_with("app")

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

        def _release(conn: Any) -> None:
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

    @staticmethod
    def _slow(live: LiveDatabase) -> str:
        """A sum over 2^32 joined rows: minutes of work the server can interrupt.

        (Not a join of information_schema tables: MySQL does not interrupt
        those for a long while, and each run would leave one behind.)
        """
        tables = ", ".join(f"{live.orders} t{i}" for i in range(32))
        return f"SELECT SUM(t0.id + t31.id) FROM {tables}"

    async def test_cancelling_the_task_raises_cancellation(self, live_mysql: LiveDatabase) -> None:
        import asyncio

        # A short limit on the server, so that the abandoned query is gone
        # before this test's tables are dropped.
        tool = DatabaseToolAdapter.create_tools(
            connector_name="live",
            connector_id=live_mysql.connector_id,
            db_config={**live_mysql.config, "read_only": True, "query_timeout": 3},
            schema_tables=[],
            on_call_complete=None,
        )[2]
        task = asyncio.create_task(tool.run(sql=self._slow(live_mysql)))
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
        text = await tool.run(sql=self._slow(live_mysql))
        assert text.startswith("[Timeout]"), text
        assert json.loads(await tool.run(sql="SELECT 1"))["rows"] == [[1]]

    async def test_the_client_side_timeout_is_a_timeout_too(
        self, live_mysql: LiveDatabase, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Where the server's own limit comes later (or not at all), the connection is dropped."""
        limit = MySQLDriver._limit_statement_time

        async def _later(cur: Any, _timeout_s: int) -> None:
            # Long after the client gives up, and soon enough that the
            # query is gone before this test's tables are dropped.
            await limit(cur, 4)

        monkeypatch.setattr(MySQLDriver, "_limit_statement_time", staticmethod(_later))
        driver = await ConnectionPoolManager.get_instance().get_driver(
            live_mysql.connector_id, live_mysql.config
        )
        with pytest.raises(TimeoutError, match="timed out after 1s"):
            await driver.execute_query(self._slow(live_mysql), timeout_s=1)
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


async def _raw(live: LiveDatabase, sql: str) -> list[list[Any]]:
    """Run *sql* on the connector's driver with no check and no transaction."""
    driver = await ConnectionPoolManager.get_instance().get_driver(live.connector_id, live.config)
    return (await driver.execute_query(sql, read_only=False)).rows


class TestTheServerAndTheCheckReadTheSameStatement:
    """Where MySQL's lexer and the check's reading disagreed, a read wrote a file."""

    @pytest.mark.parametrize(
        "payload",
        [
            # MySQL starts a "--" comment before U+007F ...
            "SELECT 1 --\x7f'\nINTO OUTFILE '{path}' --\x7f'",
            "SELECT 1 --\x7f'\n/*!INTO OUTFILE '{path}'*/ --\x7f'",
            # ... and not before U+3000 or U+0085, which are part of a name.
            "SELECT 1 FROM {orders} `　a` WHERE 1 --　a.id INTO OUTFILE '{path}'",
            "SELECT 1 FROM {orders} `\u0085a` WHERE 1 --\u0085a.id INTO OUTFILE '{path}'",
            "SELECT 1 \x00 /*!INTO OUTFILE '{path}'*/",
        ],
    )
    async def test_an_odd_character_cannot_hide_into_outfile(
        self, live_mysql: LiveDatabase, payload: str
    ) -> None:
        path = f"/tmp/{live_mysql.orders}.out"
        text = await _tool(live_mysql, read_only=True).run(
            sql=payload.format(path=path, orders=live_mysql.orders)
        )
        assert text.startswith("[SQL Safety Error]"), text
        assert await _raw(live_mysql, f"SELECT LOAD_FILE('{path}') IS NULL") == [[1]]

    async def test_a_server_in_no_backslash_escapes_mode_is_read_by_the_default_rules(
        self, live_mysql: LiveDatabase
    ) -> None:
        """The session is taken out of the two modes that move where a string ends."""
        (before,) = (await _raw(live_mysql, "SELECT @@GLOBAL.sql_mode"))[0]
        path = f"/tmp/{live_mysql.orders}.nbe"
        strict = LiveDatabase(
            config=live_mysql.config,
            connector_id=f"{live_mysql.connector_id}-nbe",
            orders=live_mysql.orders,
            secrets=live_mysql.secrets,
        )
        try:
            await _raw(
                live_mysql,
                "SET GLOBAL sql_mode = CONCAT(@@GLOBAL.sql_mode, ',NO_BACKSLASH_ESCAPES,ANSI_QUOTES')",
            )
            tool = _tool(strict, read_only=True)
            mode = json.loads(await tool.run(sql="SELECT @@sql_mode, @@GLOBAL.sql_mode"))["rows"][0]
            assert "NO_BACKSLASH_ESCAPES" in mode[1] and "ANSI_QUOTES" in mode[1]
            assert "NO_BACKSLASH_ESCAPES" not in mode[0] and "ANSI_QUOTES" not in mode[0]
            # The rest of the server's mode is kept.
            assert set(mode[0].split(",")) == set(before.split(",")) - {
                "ANSI_QUOTES",
                "NO_BACKSLASH_ESCAPES",
            }
            for sql in (
                "SELECT '\\' , LOAD_FILE('/etc/hostname') , '\\'",
                f"SELECT 1, '\\' INTO OUTFILE '{path}' -- '",
                "SELECT '\\' , SLEEP(1), '\\'",
                "SELECT '\\' /*!, 'exec-comment-ran' */ , '\\'",
            ):
                text = await tool.run(sql=sql)
                # Refused by the check, or a syntax error on the server:
                # under one reading there is nothing here to run.
                assert text.startswith(("[SQL Safety Error]", "[Error]")), (sql, text)
                assert "exec-comment-ran" not in text and "binary" not in text
            assert await _raw(live_mysql, f"SELECT LOAD_FILE('{path}') IS NULL") == [[1]]
            bound = f"SELECT id FROM {strict.orders} WHERE customer = {{{{v}}}}"
            assert json.loads(await _bound(tool, bound, "\\' OR 1=1 -- "))["rows"] == []
            assert json.loads(await _bound(tool, bound, "alice"))["rows"] == [[1]]
            # The fallback path (a connection per call) is prepared the same way.
            driver = await ConnectionPoolManager.get_instance().get_driver(
                strict.connector_id, strict.config
            )
            assert isinstance(driver, MySQLDriver)
            driver._can_reset = False
            fresh = await driver.execute_query("SELECT @@sql_mode")
            assert "NO_BACKSLASH_ESCAPES" not in fresh.rows[0][0]
        finally:
            await _raw(live_mysql, f"SET GLOBAL sql_mode = '{before}'")
            await ConnectionPoolManager.get_instance().close_driver(strict.connector_id)

    def test_the_combination_modes_that_imply_ansi_quotes_are_dropped_too(self) -> None:
        assert {"ANSI", "ORACLE", "MSSQL"} <= mysql_driver._QUOTING_MODES


class _CountingPool:
    """Stands in for the aiomysql pool: who holds what."""

    def __init__(self) -> None:
        self.used: list[Any] = []
        self.dropped: list[Any] = []
        self.handed_out = 0

    async def acquire(self) -> Any:
        self.handed_out += 1
        conn = MagicMock(closed=False)
        conn.close = lambda: setattr(conn, "closed", True)
        self.used.append(conn)
        return conn

    def release(self, conn: Any) -> Any:
        # Not a coroutine in aiomysql either.
        self.used.remove(conn)
        if conn.closed:
            self.dropped.append(conn)
        return asyncio.get_running_loop().create_future()


class TestAConnectionIsAlwaysGivenBack:
    """Five connections not given back are the whole pool, until a restart."""

    async def _cancel_inside(
        self, monkeypatch: pytest.MonkeyPatch, *, where: str, times: int = 20
    ) -> _CountingPool:
        driver = MySQLDriver({})
        pool = _CountingPool()
        driver._pool = pool
        entered = asyncio.Event()

        async def _stuck(*_args: Any) -> bool:
            entered.set()
            await asyncio.sleep(3600)
            return True

        async def _fine(*_args: Any) -> bool:
            return True

        monkeypatch.setattr(MySQLDriver, "_reset", _stuck if where == "reset" else _fine)
        monkeypatch.setattr(
            MySQLDriver, "_clear", staticmethod(_stuck if where == "clear" else _fine)
        )

        async def _use() -> None:
            async with driver._session():
                if where == "body":
                    await _stuck()

        for _ in range(times):
            entered.clear()
            task = asyncio.create_task(_use())
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        return pool

    @pytest.mark.parametrize("where", ["reset", "body", "clear"])
    async def test_a_cancellation_anywhere_between_checkout_and_release(
        self, monkeypatch: pytest.MonkeyPatch, where: str
    ) -> None:
        pool = await self._cancel_inside(monkeypatch, where=where)
        assert pool.handed_out == 20
        assert pool.used == []
        if where != "body":
            # Cut off in the middle of a reset: not reused, closed and dropped.
            assert len(pool.dropped) == 20

    async def test_an_error_in_the_query_gives_the_connection_back_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _fine(*_args: Any) -> bool:
            return True

        monkeypatch.setattr(MySQLDriver, "_reset", _fine)
        monkeypatch.setattr(MySQLDriver, "_clear", staticmethod(_fine))
        driver = MySQLDriver({})
        pool = _CountingPool()
        driver._pool = pool
        with pytest.raises(RuntimeError):
            async with driver._session():
                raise RuntimeError("syntax error")
        assert pool.used == [] and pool.dropped == []

    async def test_live_the_pool_still_serves_after_twenty_cancels_in_the_release_reset(
        self, live_mysql: LiveDatabase, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tool = _tool(live_mysql, read_only=True)
        assert json.loads(await tool.run(sql="SELECT 1"))["rows"] == [[1]]
        clear = MySQLDriver._clear
        cancelled = 0

        async def _slow_clear(conn: Any) -> None:
            nonlocal cancelled
            try:
                await asyncio.sleep(0.3)
            except asyncio.CancelledError:
                cancelled += 1
                raise
            await clear(conn)

        monkeypatch.setattr(MySQLDriver, "_clear", staticmethod(_slow_clear))
        for _ in range(20):
            task = asyncio.create_task(tool.run(sql="SELECT 1"))
            await asyncio.sleep(0.15)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert cancelled == 20
        monkeypatch.setattr(MySQLDriver, "_clear", staticmethod(clear))
        driver = await ConnectionPoolManager.get_instance().get_driver(
            live_mysql.connector_id, live_mysql.config
        )
        assert isinstance(driver, MySQLDriver) and driver._pool is not None
        assert len(driver._pool._used) == 0
        answers = await asyncio.wait_for(
            asyncio.gather(*(tool.run(sql="SELECT 1") for _ in range(8))), timeout=10
        )
        assert all(json.loads(text)["rows"] == [[1]] for text in answers)

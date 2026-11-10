"""MySQL driver using aiomysql."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import aiomysql  # type: ignore[import-untyped]

from fim_one.core.tool.connector.database.base import (
    ColumnInfo,
    DatabaseDriver,
    QueryResult,
    TableInfo,
)

logger = logging.getLogger(__name__)

# The protocol command behind ``mysql_reset_connection()``, and the error a
# server that does not know it answers with.
_COM_RESET_CONNECTION = 0x1F
_ER_UNKNOWN_COM_ERROR = 1047
_ER_UNKNOWN_SYSTEM_VARIABLE = 1193
_ER_NO_SUCH_THREAD = 1094
_POOL_SIZE = 5
# How long an abandoned statement may keep its slot: this many times the
# query timeout. And how often the server is asked whether it is gone.
_ABANDONED_CEILING_FACTOR = 10
_ABANDONED_POLL_S = 1.0
_ER_CANT_AGGREGATE_2COLLATIONS = 1267
_MODE_NAME = re.compile(r"[A-Za-z0-9_]+")
# Modes under which MySQL reads strings and quoted names differently from
# its default: the two themselves, and the combination modes that include
# ANSI_QUOTES.
_QUOTING_MODES = frozenset(
    {"NO_BACKSLASH_ESCAPES", "ANSI_QUOTES", "ANSI", "DB2", "MAXDB", "MSSQL", "ORACLE", "POSTGRESQL"}
)
# "Query execution was interrupted": MySQL's MAX_EXECUTION_TIME (3024) and
# MariaDB's max_statement_time (1969).
_STATEMENT_TIMED_OUT = (3024, 1969)


class MySQLDriver(DatabaseDriver):
    """MySQL / MariaDB driver backed by aiomysql connection pool."""

    placeholder_style = "literal"
    enforces_read_only = True
    sql_dialect = "mysql"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._pool: aiomysql.Pool | None = None
        # Whether a pooled connection can be handed out with a clean
        # session (see ``_session``). ``False`` once the server has refused
        # the reset command: connections are then not reused at all.
        self._can_reset = True
        # The SQL mode a read-only session is put into (see ``_set_sql_mode``).
        self._sql_mode: str | None = None
        # Slots for statements on the server, running or abandoned and not
        # yet gone (see ``_session``), and the tasks waiting those out.
        self._slots: asyncio.Semaphore | None = None
        self._stopping: set[asyncio.Task[None]] = set()

    def _connection_settings(self) -> dict[str, Any]:
        return {
            "host": self._config.get("host", "localhost"),
            "port": int(self._config.get("port", 3306)),
            "user": self._config.get("username", ""),
            "password": self._config.get("password", ""),
            "db": self._config.get("database", ""),
            # Said outright, so that it does not follow the server's default
            # and can be put back after a reset.
            "charset": str(self._config.get("charset") or "utf8mb4"),
            "connect_timeout": 10,
            "autocommit": True,
        }

    async def connect(self) -> None:
        """Create an aiomysql connection pool."""
        self._pool = await aiomysql.create_pool(
            minsize=1, maxsize=_POOL_SIZE, **self._connection_settings()
        )

    @asynccontextmanager
    async def _session(
        self, *, read_only: bool = True, timeout_s: int = 30
    ) -> AsyncIterator[Any]:
        """A clean session (:meth:`_connection`), within the driver's count of busy slots.

        A query whose client gave up (a timeout, a cancelled request) is
        not over for the server: aiomysql drops the connection and the
        statement runs on, for as long as it takes. Such a statement keeps
        its slot here until it is gone (see :meth:`_abandon`), so however
        many requests are abandoned, at most ``_POOL_SIZE`` statements of
        this connector run on the server at once, from this process.
        """
        if self._slots is None:
            self._slots = asyncio.Semaphore(_POOL_SIZE)
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=timeout_s)
        except TimeoutError:
            raise RuntimeError(
                f"all {_POOL_SIZE} connections of this connector are busy, some with "
                "queries that were given up on and are still running on the server; "
                "try again later"
            ) from None
        kept = False
        try:
            async with self._connection(read_only=read_only) as conn:
                thread_id = conn.thread_id()
                try:
                    yield conn
                finally:
                    if conn.closed:
                        # Cut off in the middle of a statement.
                        kept = self._abandon(thread_id, timeout_s)
        finally:
            if not kept:
                self._slots.release()

    def _abandon(self, thread_id: int, timeout_s: int) -> bool:
        """Have the server stop the statement of *thread_id*; its slot stays busy meanwhile.

        Runs on its own, after the caller has gone: it never raises into
        the caller and does not hold up a cancellation. ``True`` when the
        slot was handed to it.
        """
        try:
            task = asyncio.get_running_loop().create_task(self._stop(thread_id, timeout_s))
        except RuntimeError:
            return False
        self._stopping.add(task)

        def _done(finished: asyncio.Task[None]) -> None:
            self._stopping.discard(finished)
            if self._slots is not None:
                self._slots.release()
            if not finished.cancelled() and finished.exception() is not None:
                logger.warning(
                    "Stopping an abandoned MySQL statement failed", exc_info=finished.exception()
                )

        task.add_done_callback(_done)
        return True

    async def _stop(self, thread_id: int, timeout_s: int) -> None:
        """``KILL QUERY`` the statement, then wait until its thread is no longer running one.

        From a short-lived connection of its own, with the connector's
        credentials (a user may always kill their own threads). Some
        statements take the server a long time to interrupt; the wait ends
        after ten times the query timeout whatever the server says.
        """
        side = await aiomysql.connect(**self._connection_settings())
        try:
            async with side.cursor() as cur:
                try:
                    await cur.execute(f"KILL QUERY {int(thread_id)}")
                except Exception as exc:
                    if getattr(exc, "args", (None,))[0] != _ER_NO_SUCH_THREAD:
                        raise
                    return
                deadline = time.monotonic() + _ABANDONED_CEILING_FACTOR * timeout_s
                while time.monotonic() < deadline:
                    await cur.execute(
                        "SELECT 1 FROM information_schema.processlist "
                        "WHERE id = %s AND command <> 'Sleep'",
                        (int(thread_id),),
                    )
                    if not await cur.fetchone():
                        return
                    await asyncio.sleep(_ABANDONED_POLL_S)
                logger.warning(
                    "MySQL thread %s is still running %ss after its query was given up on",
                    thread_id,
                    _ABANDONED_CEILING_FACTOR * timeout_s,
                )
        finally:
            side.close()

    @asynccontextmanager
    async def _connection(self, *, read_only: bool = True) -> AsyncIterator[Any]:
        """A connection whose session carries nothing over from its last use.

        A pooled connection keeps what earlier statements left on it: user
        variables, named locks, temporary tables, ``sql_mode``, the client
        character set. Any of these can come from a statement the connector
        ran, on a read-write connector by design (``SET NAMES gbk``), and
        the next caller would run under them. So each checkout first sends
        ``COM_RESET_CONNECTION`` (MySQL 5.7.3+, MariaDB 10.2.4+), which
        clears all of them, and then states the character set, auto-commit,
        the connector's database (the reset keeps whatever ``USE`` chose)
        and the SQL mode again.

        A server that does not know the command gets a connection of its
        own for every call, closed afterwards: with no reliable reset,
        nothing is reused.
        """
        assert self._pool is not None
        if self._can_reset:
            conn = await self._pool.acquire()
            # From here to the release below nothing may get in the way of
            # the release, a cancellation least of all: a connection that
            # is not released stays counted as in use for ever, and five
            # of them are the whole pool. The release itself is not a
            # coroutine. A connection goes back open only if everything
            # between ran to its end; otherwise it is closed first, and the
            # pool drops it.
            reusable = False
            clean = False
            try:
                clean = await self._reset(conn, read_only=read_only)
                if clean:
                    try:
                        yield conn
                    finally:
                        # Reset again before the connection waits in the
                        # pool: a named lock (GET_LOCK) or a temporary
                        # table would otherwise be held for as long as it
                        # idles there.
                        await self._clear(conn)
                        reusable = True
            finally:
                if not reusable:
                    conn.close()
                self._pool.release(conn)
            if clean:
                return
        conn = await aiomysql.connect(**self._connection_settings())
        try:
            await self._set_sql_mode(conn, read_only=read_only)
            yield conn
        finally:
            conn.close()

    async def _set_sql_mode(self, conn: Any, *, read_only: bool) -> None:
        """Put the session into the SQL mode its use calls for.

        ``NO_BACKSLASH_ESCAPES`` makes a backslash in a string an ordinary
        character and ``ANSI_QUOTES`` makes ``"..."`` a name. The read-only
        check reads statements by MySQL's default rules (``reading.py``);
        on a server whose global mode has either, a string would end
        somewhere else for the server than for the check, and what the
        check took for text would run (``SELECT '\\' , LOAD_FILE(...) , '\\'``).

        So a **read-only** session is put into the server's own mode minus
        these two (and minus the combination modes that imply
        ``ANSI_QUOTES``; their other parts are listed separately by the
        server and stay). That mode is read from the server once and then
        set every time, so a later change of the global mode does not
        bring the two back.

        A **read-write** session keeps the server's mode: its owner's SQL
        is written for that server (``"name"`` a column, ``'C:\\tmp\\'`` a
        path), and nothing but the short list of never-allowed functions is
        checked on it.

        The mode is set on every checkout for both kinds: one connector's
        read-only and read-write calls share the pooled connections.
        """
        if not read_only:
            async with conn.cursor() as cur:
                await cur.execute("SET SESSION sql_mode = @@GLOBAL.sql_mode")
            return
        if self._sql_mode is None:
            async with conn.cursor() as cur:
                await cur.execute("SELECT @@GLOBAL.sql_mode")
                row = await cur.fetchone()
            modes = [m for m in str(row[0] if row else "").split(",") if _MODE_NAME.fullmatch(m)]
            self._sql_mode = ",".join(m for m in modes if m.upper() not in _QUOTING_MODES)
        async with conn.cursor() as cur:
            # Mode names are letters, digits and underscores (checked above).
            await cur.execute(f"SET SESSION sql_mode = '{self._sql_mode}'")

    @staticmethod
    async def _clear(conn: Any) -> None:
        """Drop what the session holds (locks, temporary tables, variables); close it if that fails."""
        if conn.closed:
            return
        try:
            await conn._execute_command(_COM_RESET_CONNECTION, b"")
            await conn._read_ok_packet()
        except Exception:
            logger.debug("Clearing a MySQL session on release failed", exc_info=True)
            conn.close()

    async def _reset(self, conn: Any, *, read_only: bool = True) -> bool:
        """Reset *conn*'s session; ``False`` when it could not be reset."""
        send = getattr(conn, "_execute_command", None)
        read_ok = getattr(conn, "_read_ok_packet", None)
        if send is None or read_ok is None:
            # Not the aiomysql this was written against.
            self._can_reset = False
            return False
        try:
            await send(_COM_RESET_CONNECTION, b"")
            await read_ok()
            settings = self._connection_settings()
            await conn.set_charset(settings["charset"])
            await conn.autocommit(True)
            if settings["db"]:
                # The reset keeps the current database: after "USE other"
                # every later query on this connection would run there.
                await conn.select_db(settings["db"])
            await self._set_sql_mode(conn, read_only=read_only)
        except Exception as exc:
            if getattr(exc, "args", (None,))[0] == _ER_UNKNOWN_COM_ERROR:
                logger.warning(
                    "MySQL server %s does not support COM_RESET_CONNECTION; its "
                    "connections will not be reused between queries",
                    self._config.get("host"),
                )
                self._can_reset = False
            else:
                logger.warning("Resetting a MySQL connection failed", exc_info=True)
            return False
        return True

    async def disconnect(self) -> None:
        """Close the connection pool."""
        for task in list(self._stopping):
            task.cancel()
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None

    async def test_connection(self) -> tuple[bool, str]:
        """Test connectivity and return the database version."""
        try:
            if not self._pool:
                await self.connect()
            assert self._pool is not None
            async with self._session() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT VERSION()")
                    row = await cur.fetchone()
                    version = row[0] if row else "unknown"
                    return True, version
        except Exception as exc:
            return False, str(exc)

    async def list_tables(self, schema: str | None = None) -> list[TableInfo]:
        """List tables from information_schema.tables."""
        database = schema or self._config.get("database", "")
        assert self._pool is not None

        async with self._session() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    """
                    SELECT t.TABLE_NAME AS table_name,
                           (SELECT COUNT(*)
                            FROM information_schema.COLUMNS c
                            WHERE c.TABLE_SCHEMA = t.TABLE_SCHEMA
                              AND c.TABLE_NAME = t.TABLE_NAME) AS col_count
                    FROM information_schema.TABLES t
                    WHERE t.TABLE_SCHEMA = %s
                      AND t.TABLE_TYPE = 'BASE TABLE'
                    ORDER BY t.TABLE_NAME
                    """,
                    (database,),
                )
                rows = await cur.fetchall()
                return [
                    TableInfo(
                        table_name=r["table_name"],
                        column_count=r["col_count"],
                    )
                    for r in rows
                ]

    async def describe_table(
        self, table_name: str, schema: str | None = None
    ) -> list[ColumnInfo]:
        """Describe columns for a table."""
        database = schema or self._config.get("database", "")
        assert self._pool is not None

        async with self._session() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    """
                    SELECT
                        c.COLUMN_NAME AS column_name,
                        c.DATA_TYPE AS data_type,
                        c.IS_NULLABLE AS is_nullable,
                        c.COLUMN_KEY AS column_key
                    FROM information_schema.COLUMNS c
                    WHERE c.TABLE_SCHEMA = %s
                      AND c.TABLE_NAME = %s
                    ORDER BY c.ORDINAL_POSITION
                    """,
                    (database, table_name),
                )
                rows = await cur.fetchall()
                return [
                    ColumnInfo(
                        column_name=r["column_name"],
                        data_type=r["data_type"],
                        is_nullable=r["is_nullable"] == "YES",
                        is_primary_key=r["column_key"] == "PRI",
                    )
                    for r in rows
                ]

    async def execute_query(
        self,
        sql: str,
        *,
        timeout_s: int = 30,
        max_rows: int = 1000,
        parameters: Sequence[Any] | None = None,
        read_only: bool = True,
    ) -> QueryResult:
        """Execute a SQL query with timeout and row limit.

        With *read_only* the query runs between ``START TRANSACTION READ
        ONLY`` and ``ROLLBACK`` (MySQL 5.6.5+, MariaDB 10.0+): the server
        refuses INSERT, UPDATE, DELETE and locking reads, under ``EXPLAIN
        ANALYZE`` and after a CTE as well. A server too old for the
        statement fails the query rather than running it unguarded.

        It does not stop DDL or administration: MySQL commits the open
        transaction before a ``CREATE``, ``DROP``, ``ALTER``, ``RENAME``,
        ``TRUNCATE``, ``GRANT`` or ``CREATE USER`` and then runs it, and
        ``SET`` is allowed inside one. Those have to be the statement's
        first word, and MySQL also takes them from inside a ``/*! ... */``
        comment at the start of a statement; ``safety.validate_sql`` refuses
        both on a read-only connector (any ``/*!`` comment, and any first
        word that is not a read, judged with comments removed). Nothing in
        the server stands behind that check for these statements.

        *parameters* are not taken: aiomysql has no server-side
        parameters, and its client-side escaping is only right for the
        character set the session is in. Values are written into *sql* as
        hex literals before it gets here (``binding.mysql_literal``).
        """
        if parameters:
            raise RuntimeError(
                "The MySQL driver takes no parameters; bind values with binding.render_sql"
            )
        assert self._pool is not None

        start = time.monotonic()
        try:
            async with self._session(read_only=read_only, timeout_s=timeout_s) as conn:
                cur = await conn.cursor()
                try:
                    await self._limit_statement_time(cur, timeout_s)
                    if read_only:
                        await cur.execute("START TRANSACTION READ ONLY")
                    await asyncio.wait_for(cur.execute(sql), timeout=timeout_s)

                    # Fetch max_rows + 1 to detect truncation
                    rows_raw = await asyncio.wait_for(
                        cur.fetchmany(max_rows + 1), timeout=timeout_s
                    )

                    truncated = len(rows_raw) > max_rows
                    if truncated:
                        rows_raw = rows_raw[:max_rows]

                    columns = [desc[0] for desc in cur.description] if cur.description else []
                    rows = [list(r) for r in rows_raw]

                    elapsed = (time.monotonic() - start) * 1000
                    return QueryResult(
                        columns=columns,
                        rows=_serialize_rows(rows),
                        row_count=len(rows),
                        truncated=truncated,
                        execution_time_ms=round(elapsed, 2),
                    )
                finally:
                    await _finish(conn, cur, rollback=read_only)
        except TimeoutError:
            raise TimeoutError(f"Query timed out after {timeout_s}s") from None
        except Exception as exc:
            code = getattr(exc, "args", (None,))[0]
            if code in _STATEMENT_TIMED_OUT:
                raise TimeoutError(f"Query timed out after {timeout_s}s") from None
            if code == _ER_CANT_AGGREGATE_2COLLATIONS:
                # "Illegal mix of collations (latin1_swedish_ci,IMPLICIT) and
                # (utf8mb4_0900_ai_ci,COERCIBLE) for operation '='".
                raise RuntimeError(
                    "MySQL error: a text value cannot be compared with this column, "
                    "because it has characters the column's character set cannot hold "
                    "(Chinese text against a latin1 column, for example, or an emoji "
                    f"against utf8mb3). The server said: {exc.args[1] if len(exc.args) > 1 else exc}"
                ) from exc
            raise RuntimeError(f"MySQL error: {exc}") from exc

    @staticmethod
    async def _limit_statement_time(cur: Any, timeout_s: int) -> None:
        """Have the server stop a statement that runs past *timeout_s*.

        MySQL and MariaDB name the setting differently. A server with
        neither is still bounded by the client-side timeout, which drops
        the connection.
        """
        for statement, value in (
            ("SET SESSION MAX_EXECUTION_TIME = %s", int(timeout_s * 1000)),
            ("SET SESSION max_statement_time = %s", int(timeout_s)),
        ):
            try:
                await cur.execute(statement, (value,))
                return
            except Exception as exc:
                if getattr(exc, "args", (None,))[0] != _ER_UNKNOWN_SYSTEM_VARIABLE:
                    raise
        logger.debug("This MySQL server has no statement time limit setting")


async def _finish(conn: Any, cur: Any, *, rollback: bool) -> None:
    """End the read-only transaction and close the cursor, if the connection is still there.

    aiomysql closes a connection whose query was cancelled (a timeout, a
    cancelled task). There is then nothing to roll back, and trying would
    raise over the cancellation or the timeout that is on its way out.
    The session's settings need no undoing: the next checkout resets it.
    """
    if conn.closed:
        return
    try:
        if rollback:
            # Ends the transaction; there is nothing to keep.
            await cur.execute("ROLLBACK")
        await cur.close()
    except Exception:
        logger.debug("Cleaning up after a MySQL query failed", exc_info=True)
        conn.close()


def _serialize_rows(rows: list[list[Any]]) -> list[list[Any]]:
    """Convert non-JSON-serializable types to strings."""
    import decimal
    from datetime import date, datetime, time, timedelta

    result: list[list[Any]] = []
    for row in rows:
        new_row: list[Any] = []
        for val in row:
            if val is None:
                new_row.append(None)
            elif isinstance(val, (str, int, float, bool)):
                new_row.append(val)
            elif isinstance(val, decimal.Decimal):
                new_row.append(float(val))
            elif isinstance(val, (datetime, date, time)):
                new_row.append(val.isoformat())
            elif isinstance(val, timedelta):
                new_row.append(str(val))
            elif isinstance(val, bytes):
                new_row.append(f"<binary {len(val)} bytes>")
            elif isinstance(val, (list, dict)):
                new_row.append(val)
            else:
                new_row.append(str(val))
        result.append(new_row)
    return result

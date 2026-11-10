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
        # The SQL mode every session is put into (see ``_set_sql_mode``).
        self._sql_mode: str | None = None

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
            minsize=1, maxsize=5, **self._connection_settings()
        )

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[Any]:
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
            try:
                clean = await self._reset(conn)
            except BaseException:
                conn.close()
                await self._pool.release(conn)
                raise
            if clean:
                try:
                    yield conn
                finally:
                    # Reset again before the connection waits in the pool:
                    # a named lock (GET_LOCK) or a temporary table would
                    # otherwise be held for as long as it idles there.
                    await self._clear(conn)
                    await self._pool.release(conn)
                return
            conn.close()
            await self._pool.release(conn)
        conn = await aiomysql.connect(**self._connection_settings())
        try:
            await self._set_sql_mode(conn)
            yield conn
        finally:
            conn.close()

    async def _set_sql_mode(self, conn: Any) -> None:
        """Give the session the server's SQL mode without the two modes that change quoting.

        ``NO_BACKSLASH_ESCAPES`` makes a backslash in a string an ordinary
        character and ``ANSI_QUOTES`` makes ``"..."`` a name. The safety
        check reads statements by MySQL's default rules (``reading.py``);
        on a server whose global mode has either, a string would end
        somewhere else for the server than for the check, and what the
        check took for text would run (``SELECT '\\' , LOAD_FILE(...) , '\\'``).
        So every session is put into the server's own mode minus these two
        (and minus the combination modes that imply ``ANSI_QUOTES``; their
        other parts are listed separately by the server and stay).

        The mode is read from the server once and then set on every
        session, so a later change of the global mode does not bring the
        two back.
        """
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

    async def _reset(self, conn: Any) -> bool:
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
            await self._set_sql_mode(conn)
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
            async with self._session() as conn:
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

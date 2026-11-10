"""MySQL driver using aiomysql."""

from __future__ import annotations

import asyncio
import logging
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
        clears all of them, and then states the character set and
        auto-commit again.

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
                    await self._pool.release(conn)
                return
            conn.close()
            await self._pool.release(conn)
        conn = await aiomysql.connect(**self._connection_settings())
        try:
            yield conn
        finally:
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
            await conn.set_charset(self._connection_settings()["charset"])
            await conn.autocommit(True)
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
                async with conn.cursor() as cur:
                    # Set session-level query timeout
                    await cur.execute(
                        "SET SESSION MAX_EXECUTION_TIME = %s",
                        (int(timeout_s * 1000),),
                    )
                    try:
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

                        columns = (
                            [desc[0] for desc in cur.description]
                            if cur.description
                            else []
                        )
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
                        if read_only:
                            # Ends the transaction; there is nothing to keep.
                            await cur.execute("ROLLBACK")
                        await cur.execute("SET SESSION MAX_EXECUTION_TIME = 0")
        except asyncio.TimeoutError:
            raise TimeoutError(f"Query timed out after {timeout_s}s") from None
        except Exception as exc:
            if "TimeoutError" in type(exc).__name__:
                raise
            raise RuntimeError(f"MySQL error: {exc}") from exc


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

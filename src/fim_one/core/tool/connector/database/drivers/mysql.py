"""MySQL driver using aiomysql."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiomysql  # type: ignore[import-untyped]

from fim_one.core.tool.connector.database.base import (
    ColumnInfo,
    DatabaseDriver,
    QueryResult,
    TableInfo,
)

logger = logging.getLogger(__name__)


class MySQLDriver(DatabaseDriver):
    """MySQL / MariaDB driver backed by aiomysql connection pool."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._pool: aiomysql.Pool | None = None

    async def connect(self) -> None:
        """Create an aiomysql connection pool."""
        self._pool = await aiomysql.create_pool(
            host=self._config.get("host", "localhost"),
            port=int(self._config.get("port", 3306)),
            user=self._config.get("username", ""),
            password=self._config.get("password", ""),
            db=self._config.get("database", ""),
            minsize=1,
            maxsize=5,
            connect_timeout=10,
            autocommit=True,
        )

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
            async with self._pool.acquire() as conn:
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

        async with self._pool.acquire() as conn:
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

        async with self._pool.acquire() as conn:
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
        self, sql: str, *, timeout_s: int = 30, max_rows: int = 1000
    ) -> QueryResult:
        """Execute a SQL query with timeout and row limit."""
        assert self._pool is not None

        start = time.monotonic()
        try:
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cur:
                    # Set session-level query timeout
                    await cur.execute(
                        "SET SESSION MAX_EXECUTION_TIME = %s",
                        (int(timeout_s * 1000),),
                    )
                    try:
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

"""PostgreSQL driver using asyncpg."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import asyncpg  # type: ignore[import-untyped]

from fim_one.core.tool.connector.database.base import (
    ColumnInfo,
    DatabaseDriver,
    QueryResult,
    TableInfo,
)

logger = logging.getLogger(__name__)


class PostgreSQLDriver(DatabaseDriver):
    """PostgreSQL driver backed by asyncpg connection pool."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        """Create an asyncpg connection pool."""
        ssl_mode = self._config.get("ssl", False)
        ssl_arg: Any = "require" if ssl_mode else False

        self._pool = await asyncpg.create_pool(
            host=self._config.get("host", "localhost"),
            port=int(self._config.get("port", 5432)),
            user=self._config.get("username", ""),
            password=self._config.get("password", ""),
            database=self._config.get("database", ""),
            ssl=ssl_arg,
            min_size=1,
            max_size=5,
            command_timeout=60,
        )

    async def disconnect(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def test_connection(self) -> tuple[bool, str]:
        """Test connectivity and return the database version."""
        try:
            if not self._pool:
                await self.connect()
            assert self._pool is not None
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow("SELECT version()")
                version = row[0] if row else "unknown"
                return True, version
        except Exception as exc:
            return False, str(exc)

    async def list_tables(self, schema: str | None = None) -> list[TableInfo]:
        """List tables from information_schema.tables."""
        schema = schema or self._config.get("schema", "public")
        assert self._pool is not None

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT t.table_name,
                       (SELECT COUNT(*)
                        FROM information_schema.columns c
                        WHERE c.table_schema = t.table_schema
                          AND c.table_name = t.table_name) AS col_count
                FROM information_schema.tables t
                WHERE t.table_schema = $1
                  AND t.table_type = 'BASE TABLE'
                ORDER BY t.table_name
                """,
                schema,
            )
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
        """Describe columns for a table including primary key info."""
        schema = schema or self._config.get("schema", "public")
        assert self._pool is not None

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    c.column_name,
                    c.data_type,
                    c.is_nullable,
                    COALESCE(
                        (SELECT TRUE
                         FROM information_schema.table_constraints tc
                         JOIN information_schema.key_column_usage kcu
                           ON tc.constraint_name = kcu.constraint_name
                          AND tc.table_schema = kcu.table_schema
                         WHERE tc.constraint_type = 'PRIMARY KEY'
                           AND tc.table_schema = c.table_schema
                           AND tc.table_name = c.table_name
                           AND kcu.column_name = c.column_name
                         LIMIT 1),
                        FALSE
                    ) AS is_pk
                FROM information_schema.columns c
                WHERE c.table_schema = $1
                  AND c.table_name = $2
                ORDER BY c.ordinal_position
                """,
                schema,
                table_name,
            )
            return [
                ColumnInfo(
                    column_name=r["column_name"],
                    data_type=r["data_type"],
                    is_nullable=r["is_nullable"] == "YES",
                    is_primary_key=bool(r["is_pk"]),
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
                # Set statement timeout
                await conn.execute(f"SET statement_timeout = {int(timeout_s * 1000)}")
                try:
                    stmt = await asyncio.wait_for(conn.prepare(sql), timeout=timeout_s)
                    columns = [a.name for a in stmt.get_attributes()]

                    # Use cursor to cap rows without fetching everything.
                    # asyncpg's stmt.fetch(n) treats n as a bind param, not a limit.
                    records: list[asyncpg.Record] = []
                    async with conn.transaction():
                        async for record in stmt.cursor():
                            records.append(record)
                            if len(records) > max_rows:
                                break

                    truncated = len(records) > max_rows
                    if truncated:
                        records = records[:max_rows]

                    rows = [list(r.values()) for r in records]

                    elapsed = (time.monotonic() - start) * 1000
                    return QueryResult(
                        columns=columns,
                        rows=_serialize_rows(rows),
                        row_count=len(rows),
                        truncated=truncated,
                        execution_time_ms=round(elapsed, 2),
                    )
                finally:
                    await conn.execute("RESET statement_timeout")
        except asyncio.TimeoutError:
            elapsed = (time.monotonic() - start) * 1000
            raise TimeoutError(f"Query timed out after {timeout_s}s") from None
        except asyncpg.PostgresError as exc:
            raise RuntimeError(f"PostgreSQL error: {exc}") from exc


def _serialize_rows(rows: list[list[Any]]) -> list[list[Any]]:
    """Convert non-JSON-serializable types to strings."""
    import decimal
    from datetime import date, datetime, time, timedelta
    from uuid import UUID

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
            elif isinstance(val, UUID):
                new_row.append(str(val))
            elif isinstance(val, bytes):
                new_row.append(f"<binary {len(val)} bytes>")
            elif isinstance(val, (list, dict)):
                new_row.append(val)
            else:
                new_row.append(str(val))
        result.append(new_row)
    return result

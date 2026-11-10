"""PostgreSQL driver using asyncpg."""

from __future__ import annotations

import asyncio
import decimal
import logging
import re
import time
from collections.abc import Sequence
from datetime import date, datetime
from datetime import time as dtime
from typing import Any

import asyncpg  # type: ignore[import-untyped]

from fim_one.core.tool.connector.database.base import (
    ColumnInfo,
    DatabaseDriver,
    QueryResult,
    TableInfo,
)

logger = logging.getLogger(__name__)

# How long after the server's own statement timeout the client gives up.
_CLIENT_TIMEOUT_GRACE_S = 2


class PostgreSQLDriver(DatabaseDriver):
    """PostgreSQL driver backed by asyncpg connection pool."""

    placeholder_style = "numeric"
    enforces_read_only = True
    sql_dialect = "postgresql"

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
        self,
        sql: str,
        *,
        timeout_s: int = 30,
        max_rows: int = 1000,
        parameters: Sequence[Any] | None = None,
        read_only: bool = True,
    ) -> QueryResult:
        """Execute a SQL query with timeout and row limit.

        *parameters* are bound to ``$1``, ``$2``, ... by the server. With
        *read_only* the query runs in a ``READ ONLY`` transaction that is
        rolled back when the rows have been read. The server refuses
        INSERT, UPDATE, DELETE, DDL and ``SELECT ... INTO`` in it, inside a
        CTE or under ``EXPLAIN ANALYZE`` as well. What it does not refuse
        (PostgreSQL 16 runs ``EXPLAIN ANALYZE CREATE TABLE ... AS`` in a
        read-only transaction) is undone by the rollback.
        """
        assert self._pool is not None

        start = time.monotonic()
        try:
            async with self._pool.acquire() as conn:
                # Set statement timeout
                await conn.execute(f"SET statement_timeout = {int(timeout_s * 1000)}")
                try:
                    stmt = await asyncio.wait_for(conn.prepare(sql), timeout=timeout_s)
                    columns = [a.name for a in stmt.get_attributes()]
                    args = _fit_parameters(stmt.get_parameters(), parameters or ())

                    # Use cursor to cap rows without fetching everything.
                    # asyncpg's stmt.fetch(n) treats n as a bind param, not a limit.
                    records: list[asyncpg.Record] = []
                    transaction = conn.transaction(readonly=read_only)
                    await transaction.start()
                    try:
                        # The server's statement_timeout can be lifted from
                        # inside the query (set_config(..., true)); this
                        # one cannot.
                        async with asyncio.timeout(timeout_s + _CLIENT_TIMEOUT_GRACE_S):
                            async for record in stmt.cursor(*args):
                                records.append(record)
                                if len(records) > max_rows:
                                    break
                    except BaseException:
                        try:
                            await transaction.rollback()
                        except Exception:
                            # The connection is unusable (cancelled in the
                            # middle of a query): the pool discards it,
                            # and the server rolls back with it.
                            logger.debug("Rollback after a failed query failed", exc_info=True)
                        raise
                    else:
                        if read_only:
                            # Nothing to keep, and anything the read-only
                            # mode let through is undone.
                            await transaction.rollback()
                        else:
                            await transaction.commit()

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
        except asyncpg.QueryCanceledError:
            # "canceling statement due to statement timeout".
            raise TimeoutError(f"Query timed out after {timeout_s}s") from None
        except asyncpg.PostgresError as exc:
            raise RuntimeError(f"PostgreSQL error: {exc}") from exc


_INTEGER = re.compile(r"[+-]?[0-9]+", re.ASCII)
_DECIMAL = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", re.ASCII)


def _fit_parameters(types: Sequence[Any], values: Sequence[Any]) -> list[Any]:
    """*values* in the Python types asyncpg sends for the parameter *types*.

    The server decides each parameter's type from the SQL, and asyncpg
    refuses a value of another Python type: text ``"42"`` for an integer
    column, or the number ``42`` for a text column. A workflow variable is
    often the text form of what the column holds, so text that reads as the
    wanted type is converted, and a number or boolean bound to a text
    parameter is sent as text. A number with a fraction bound to an integer
    parameter is an error (asyncpg would truncate it). Anything else is
    left for asyncpg to refuse.
    """
    if len(types) != len(values):
        raise RuntimeError(
            f"The query takes {len(types)} parameter(s) and {len(values)} value(s) were given"
        )
    return [_fit_parameter(kind.name, value) for kind, value in zip(types, values, strict=True)]


def _fit_parameter(kind: str, value: Any) -> Any:
    if value is None:
        return None
    if kind in ("text", "varchar", "bpchar", "name", "unknown"):
        if isinstance(value, bool):
            return "true" if value else "false"
        return value if isinstance(value, str) else str(value)
    if kind in ("json", "jsonb"):
        if isinstance(value, str):
            return value
        import json

        return json.dumps(value)
    if isinstance(value, bool):
        return decimal.Decimal(int(value)) if kind == "numeric" else value
    if isinstance(value, float) and kind in ("int2", "int4", "int8"):
        if not value.is_integer():
            # asyncpg would drop the fraction: 1.5 would find the row with id 1.
            raise ValueError(
                f"{value} is not a whole number, and the query compares it with an "
                f"integer ({kind})"
            )
        return int(value)
    if kind.endswith("[]"):
        # ``= ANY({{ids}})``: a list arrives as JSON text (or, for an
        # empty IN list, as a list).
        items = value
        if isinstance(value, str):
            import json

            try:
                items = json.loads(value)
            except ValueError:
                return value
        if isinstance(items, list):
            return [_fit_parameter(kind[:-2], item) for item in items]
        return value
    if isinstance(value, (int, float)) and kind == "numeric":
        return decimal.Decimal(str(value))
    if not isinstance(value, str):
        return value
    text = value.strip()
    try:
        if kind in ("int2", "int4", "int8") and _INTEGER.fullmatch(text):
            return int(text)
        if kind in ("float4", "float8") and _DECIMAL.fullmatch(text):
            return float(text)
        if kind == "numeric" and _DECIMAL.fullmatch(text):
            return decimal.Decimal(text)
        if kind == "bool" and text.lower() in ("true", "false", "t", "f"):
            return text.lower() in ("true", "t")
        if kind == "date":
            return date.fromisoformat(text)
        if kind in ("timestamp", "timestamptz"):
            return datetime.fromisoformat(text)
        if kind in ("time", "timetz"):
            return dtime.fromisoformat(text)
    except (ValueError, decimal.InvalidOperation):
        pass
    return value


def _serialize_rows(rows: list[list[Any]]) -> list[list[Any]]:
    """Convert non-JSON-serializable types to strings."""
    from datetime import time, timedelta
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

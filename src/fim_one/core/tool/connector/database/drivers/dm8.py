"""DM8 (达梦) driver using the official ``dmPython`` package.

DM8 is a domestic Chinese relational database. Its Python driver
(``dmPython``) is synchronous only and is distributed as a ``.whl``
file from the vendor -- it is **not** published on PyPI. Operators
are expected to download the wheel from the DM (DaMeng) official
site and either ``pip install`` it or drop it into a ``vendor/``
directory wired up via ``uv pip install vendor/dmPython-*.whl``.

Because the driver is synchronous, all blocking calls are offloaded
to a thread via :func:`asyncio.to_thread` so that they do not stall
the event loop.

SQL dialect notes
-----------------
* DM8 exposes its catalog via ``ALL_TABLES`` / ``ALL_TAB_COLUMNS``
  (Oracle-compatible metadata views) and ``ALL_CONSTRAINTS`` /
  ``ALL_CONS_COLUMNS`` for primary keys.
* Schema is typically the uppercased username. When the caller does
  not provide one, we fall back to ``config["schema"]`` or
  ``config["username"]`` upper-cased.
* Row limiting uses ``ROWNUM`` to avoid fetching large result sets
  when the caller passes ``max_rows``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fim_one.core.tool.connector.database.base import (
    ColumnInfo,
    DatabaseDriver,
    QueryResult,
    TableInfo,
)

logger = logging.getLogger(__name__)


def _load_dmpython() -> Any:
    """Import ``dmPython`` lazily with a helpful error message.

    The dependency is optional and distributed as a vendor wheel;
    importing it at module import time would break every deployment
    that does not run DM8. Returns the imported module as ``Any``
    because it has no stubs.
    """
    try:
        import dmPython  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - exercised at runtime
        raise RuntimeError(
            "The 'dmPython' package is not installed. DM8 (达梦) support requires "
            "the official driver wheel from https://eco.dameng.com/. Download the "
            "wheel matching your Python version, then run "
            "'uv pip install path/to/dmPython-*.whl'."
        ) from exc
    return dmPython


class DM8Driver(DatabaseDriver):
    """DM8 (达梦) driver backed by the synchronous ``dmPython`` library.

    Each call delegates the blocking work to :func:`asyncio.to_thread`
    so the event loop stays responsive. A single shared connection is
    held per driver instance -- the driver is always owned by the
    :class:`~fim_one.core.tool.connector.database.pool.ConnectionPoolManager`
    LRU pool, so no second pooling layer is needed.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._conn: Any | None = None
        self._lock = asyncio.Lock()

    # ---- connection lifecycle -------------------------------------------------

    async def connect(self) -> None:
        """Open the underlying ``dmPython`` connection in a worker thread."""
        # Keep the upstream capitalisation (``dmPython``) to match the
        # vendor package name; N806 does not apply to references to
        # third-party library modules.
        dm_py = _load_dmpython()

        host = self._config.get("host", "localhost")
        port = int(self._config.get("port", 5236))
        user = self._config.get("username", "")
        password = self._config.get("password", "")
        # DM8 doesn't use a separate "database" field -- the service is a
        # single instance per port. We accept it in the config for UX parity
        # but ignore it when dialing.

        def _open() -> Any:
            return dm_py.connect(
                user=user,
                password=password,
                server=host,
                port=port,
                autoCommit=True,
            )

        self._conn = await asyncio.to_thread(_open)

    async def disconnect(self) -> None:
        """Close the underlying connection."""
        if self._conn is None:
            return
        conn = self._conn
        self._conn = None
        try:
            await asyncio.to_thread(conn.close)
        except Exception:
            logger.debug("Error closing DM8 connection", exc_info=True)

    async def test_connection(self) -> tuple[bool, str]:
        """Return ``(True, version)`` on success, ``(False, error)`` otherwise."""
        try:
            if self._conn is None:
                await self.connect()
            rows = await self._execute_sync(
                "SELECT BANNER FROM V$VERSION WHERE ROWNUM = 1",
                (),
                fetch="all",
            )
            version = rows[0][0] if rows and rows[0] else "DM8"
            return True, str(version)
        except Exception as exc:
            return False, str(exc)

    # ---- schema introspection -------------------------------------------------

    def _default_schema(self, schema: str | None) -> str:
        if schema:
            return schema.upper()
        cfg_schema = self._config.get("schema")
        if cfg_schema:
            return str(cfg_schema).upper()
        return str(self._config.get("username", "")).upper()

    async def list_tables(self, schema: str | None = None) -> list[TableInfo]:
        """List user tables and their column counts via the DM8 catalog."""
        owner = self._default_schema(schema)
        sql = (
            "SELECT t.TABLE_NAME, "
            "       (SELECT COUNT(*) FROM ALL_TAB_COLUMNS c "
            "        WHERE c.OWNER = t.OWNER AND c.TABLE_NAME = t.TABLE_NAME) AS COL_COUNT "
            "FROM ALL_TABLES t "
            "WHERE t.OWNER = ? "
            "ORDER BY t.TABLE_NAME"
        )
        rows = await self._execute_sync(sql, (owner,), fetch="all")
        return [
            TableInfo(table_name=row[0], column_count=int(row[1] or 0)) for row in rows
        ]

    async def describe_table(
        self, table_name: str, schema: str | None = None
    ) -> list[ColumnInfo]:
        """Return column metadata (type, nullable, PK) for a single table."""
        owner = self._default_schema(schema)
        sql = (
            "SELECT c.COLUMN_NAME, c.DATA_TYPE, c.NULLABLE, "
            "       CASE WHEN EXISTS ( "
            "           SELECT 1 FROM ALL_CONSTRAINTS ac "
            "           JOIN ALL_CONS_COLUMNS acc "
            "             ON ac.CONSTRAINT_NAME = acc.CONSTRAINT_NAME "
            "            AND ac.OWNER = acc.OWNER "
            "           WHERE ac.CONSTRAINT_TYPE = 'P' "
            "             AND ac.OWNER = c.OWNER "
            "             AND ac.TABLE_NAME = c.TABLE_NAME "
            "             AND acc.COLUMN_NAME = c.COLUMN_NAME "
            "       ) THEN 1 ELSE 0 END AS IS_PK "
            "FROM ALL_TAB_COLUMNS c "
            "WHERE c.OWNER = ? AND c.TABLE_NAME = ? "
            "ORDER BY c.COLUMN_ID"
        )
        rows = await self._execute_sync(sql, (owner, table_name.upper()), fetch="all")
        return [
            ColumnInfo(
                column_name=row[0],
                data_type=row[1],
                is_nullable=str(row[2]).upper() in {"Y", "YES"},
                is_primary_key=bool(int(row[3] or 0)),
            )
            for row in rows
        ]

    # ---- query execution ------------------------------------------------------

    async def execute_query(
        self, sql: str, *, timeout_s: int = 30, max_rows: int = 1000
    ) -> QueryResult:
        """Execute ``sql`` and return a :class:`QueryResult`.

        DM8 does not provide a trivial per-statement timeout through
        ``dmPython``; the ``timeout_s`` argument is enforced via
        :func:`asyncio.wait_for` on the thread-offloaded execution.
        """
        start = time.monotonic()

        async def _run() -> QueryResult:
            rows_raw, columns = await self._execute_sync(
                sql, (), fetch="rows+cols", max_rows=max_rows + 1
            )
            truncated = len(rows_raw) > max_rows
            if truncated:
                rows_raw = rows_raw[:max_rows]
            rows = [list(r) for r in rows_raw]
            elapsed = (time.monotonic() - start) * 1000
            return QueryResult(
                columns=columns,
                rows=_serialize_rows(rows),
                row_count=len(rows),
                truncated=truncated,
                execution_time_ms=round(elapsed, 2),
            )

        try:
            return await asyncio.wait_for(_run(), timeout=timeout_s)
        except TimeoutError:
            raise TimeoutError(f"Query timed out after {timeout_s}s") from None
        except Exception as exc:
            if isinstance(exc, TimeoutError):
                raise
            raise RuntimeError(f"DM8 error: {exc}") from exc

    # ---- internals ------------------------------------------------------------

    async def _execute_sync(
        self,
        sql: str,
        params: tuple[Any, ...],
        *,
        fetch: str = "all",
        max_rows: int | None = None,
    ) -> Any:
        """Run a SQL statement in a worker thread with a shared connection lock."""
        if self._conn is None:
            await self.connect()
        assert self._conn is not None
        conn = self._conn

        def _run() -> Any:
            cursor = conn.cursor()
            try:
                cursor.execute(sql, params)
                cols = (
                    [d[0] for d in cursor.description] if cursor.description else []
                )
                if fetch == "all":
                    return cursor.fetchall()
                if fetch == "rows+cols":
                    rows = (
                        cursor.fetchmany(max_rows)
                        if max_rows is not None
                        else cursor.fetchall()
                    )
                    return rows, cols
                return None
            finally:
                try:
                    cursor.close()
                except Exception:  # pragma: no cover - defensive
                    logger.debug("Error closing DM8 cursor", exc_info=True)

        async with self._lock:
            return await asyncio.to_thread(_run)


def _serialize_rows(rows: list[list[Any]]) -> list[list[Any]]:
    """Convert non-JSON-serializable types into safe primitives."""
    import decimal
    from datetime import date, datetime, timedelta
    from datetime import time as dtime

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
            elif isinstance(val, (datetime, date, dtime)):
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

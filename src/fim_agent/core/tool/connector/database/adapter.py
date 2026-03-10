"""DatabaseToolAdapter — creates BaseTool instances for database connectors.

Each database connector produces three tools:
1. ``{name}__list_tables`` — list visible tables with descriptions
2. ``{name}__describe_table`` — show columns for a specific table
3. ``{name}__query`` — execute a validated SQL query
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fim_agent.core.tool.base import BaseTool

from .pool import ConnectionPoolManager
from .safety import SqlSafetyError, validate_sql

logger = logging.getLogger(__name__)


class _DatabaseListTablesTool(BaseTool):
    """Lists visible tables for a database connector."""

    def __init__(
        self,
        connector_name: str,
        connector_id: str,
        db_config: dict[str, Any],
        schema_tables: list[dict[str, Any]],
        on_call_complete: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        safe_name = re.sub(r"[^a-zA-Z0-9]", "_", connector_name.lower()).strip("_")
        self._name = f"{safe_name}__list_tables"
        self._connector_name = connector_name
        self._connector_id = connector_id
        self._db_config = db_config
        self._schema_tables = schema_tables
        self._on_call_complete = on_call_complete

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return f"{self._connector_name}: List Tables"

    @property
    def description(self) -> str:
        table_count = len(self._schema_tables)
        table_names = [t["table_name"] for t in self._schema_tables[:20]]
        preview = ", ".join(table_names)
        if table_count > 20:
            preview += f" ... ({table_count} total)"
        return (
            f"List all available tables in the '{self._connector_name}' database. "
            f"Currently has {table_count} visible tables: {preview}. "
            "Returns table names, descriptions, and column counts."
        )

    @property
    def category(self) -> str:
        return "database"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs: Any) -> str:
        """Return cached schema table list."""
        start_ms = time.monotonic_ns() // 1_000_000
        try:
            result = []
            for t in self._schema_tables:
                entry: dict[str, Any] = {"table_name": t["table_name"]}
                if t.get("display_name"):
                    entry["display_name"] = t["display_name"]
                if t.get("description"):
                    entry["description"] = t["description"]
                if t.get("column_count"):
                    entry["column_count"] = t["column_count"]
                result.append(entry)

            output = json.dumps(result, ensure_ascii=False, indent=2)
            await self._log_call(start_ms, True)
            return output
        except Exception as exc:
            await self._log_call(start_ms, False, str(exc))
            return f"[Error] {exc}"

    async def _log_call(
        self, start_ms: int, success: bool, error: str | None = None
    ) -> None:
        if self._on_call_complete:
            try:
                elapsed = time.monotonic_ns() // 1_000_000 - start_ms
                await self._on_call_complete(
                    connector_id=self._connector_id,
                    connector_name=self._connector_name,
                    action_id=None,
                    action_name="list_tables",
                    request_method="QUERY",
                    request_url=f"db://{self._connector_name}/list_tables",
                    response_status=200 if success else 500,
                    response_time_ms=elapsed,
                    success=success,
                    error_message=error,
                )
            except Exception:
                logger.debug("on_call_complete callback failed", exc_info=True)


class _DatabaseDescribeTableTool(BaseTool):
    """Describes the columns of a specific table."""

    def __init__(
        self,
        connector_name: str,
        connector_id: str,
        db_config: dict[str, Any],
        schema_tables: list[dict[str, Any]],
        on_call_complete: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        safe_name = re.sub(r"[^a-zA-Z0-9]", "_", connector_name.lower()).strip("_")
        self._name = f"{safe_name}__describe_table"
        self._connector_name = connector_name
        self._connector_id = connector_id
        self._db_config = db_config
        self._schema_tables = schema_tables
        self._on_call_complete = on_call_complete
        # Build lookup for quick access
        self._table_lookup: dict[str, dict[str, Any]] = {
            t["table_name"]: t for t in schema_tables
        }

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return f"{self._connector_name}: Describe Table"

    @property
    def description(self) -> str:
        table_names = sorted(self._table_lookup.keys())
        return (
            f"Describe the columns of a specific table in the '{self._connector_name}' "
            f"database. Available tables: {', '.join(table_names[:30])}. "
            "Returns column names, data types, nullability, and annotations."
        )

    @property
    def category(self) -> str:
        return "database"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "table_name": {
                    "type": "string",
                    "description": "Name of the table to describe",
                }
            },
            "required": ["table_name"],
        }

    async def run(self, **kwargs: Any) -> str:
        """Return column info from cached schema or live introspection."""
        table_name = kwargs.get("table_name", "")
        start_ms = time.monotonic_ns() // 1_000_000

        try:
            table_info = self._table_lookup.get(table_name)

            if table_info and table_info.get("columns"):
                # Use cached schema columns
                columns = []
                for col in table_info["columns"]:
                    entry: dict[str, Any] = {
                        "column_name": col["column_name"],
                        "data_type": col["data_type"],
                    }
                    if col.get("is_primary_key"):
                        entry["is_primary_key"] = True
                    if not col.get("is_nullable", True):
                        entry["is_nullable"] = False
                    if col.get("display_name"):
                        entry["display_name"] = col["display_name"]
                    if col.get("description"):
                        entry["description"] = col["description"]
                    columns.append(entry)

                result = {
                    "table_name": table_name,
                    "columns": columns,
                }
                if table_info.get("description"):
                    result["table_description"] = table_info["description"]

                output = json.dumps(result, ensure_ascii=False, indent=2)
                await self._log_call(start_ms, True)
                return output
            else:
                # Fall back to live introspection
                pool = ConnectionPoolManager.get_instance()
                driver = await pool.get_driver(self._connector_id, self._db_config)
                schema = self._db_config.get("schema")
                col_infos = await driver.describe_table(table_name, schema=schema)

                result = {
                    "table_name": table_name,
                    "columns": [
                        {
                            "column_name": c.column_name,
                            "data_type": c.data_type,
                            "is_nullable": c.is_nullable,
                            "is_primary_key": c.is_primary_key,
                        }
                        for c in col_infos
                    ],
                }
                output = json.dumps(result, ensure_ascii=False, indent=2)
                await self._log_call(start_ms, True)
                return output

        except Exception as exc:
            await self._log_call(start_ms, False, str(exc))
            return f"[Error] Failed to describe table '{table_name}': {exc}"

    async def _log_call(
        self, start_ms: int, success: bool, error: str | None = None
    ) -> None:
        if self._on_call_complete:
            try:
                elapsed = time.monotonic_ns() // 1_000_000 - start_ms
                await self._on_call_complete(
                    connector_id=self._connector_id,
                    connector_name=self._connector_name,
                    action_id=None,
                    action_name="describe_table",
                    request_method="QUERY",
                    request_url=f"db://{self._connector_name}/describe_table",
                    response_status=200 if success else 500,
                    response_time_ms=elapsed,
                    success=success,
                    error_message=error,
                )
            except Exception:
                logger.debug("on_call_complete callback failed", exc_info=True)


class _DatabaseQueryTool(BaseTool):
    """Executes validated SQL queries against the database."""

    def __init__(
        self,
        connector_name: str,
        connector_id: str,
        db_config: dict[str, Any],
        schema_tables: list[dict[str, Any]],
        read_only: bool = True,
        max_rows: int = 1000,
        query_timeout: int = 30,
        on_call_complete: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        safe_name = re.sub(r"[^a-zA-Z0-9]", "_", connector_name.lower()).strip("_")
        self._name = f"{safe_name}__query"
        self._connector_name = connector_name
        self._connector_id = connector_id
        self._db_config = db_config
        self._schema_tables = schema_tables
        self._read_only = read_only
        self._max_rows = max_rows
        self._query_timeout = query_timeout
        self._on_call_complete = on_call_complete

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return f"{self._connector_name}: Query"

    @property
    def description(self) -> str:
        mode = "read-only (SELECT)" if self._read_only else "read-write"
        # Build schema context for LLM
        schema_hint = self._build_schema_hint()
        return (
            f"Execute a SQL query against the '{self._connector_name}' database "
            f"({mode}, max {self._max_rows} rows, {self._query_timeout}s timeout). "
            f"Database schema:\n{schema_hint}"
        )

    @property
    def category(self) -> str:
        return "database"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "The SQL query to execute",
                }
            },
            "required": ["sql"],
        }

    def _build_schema_hint(self) -> str:
        """Build a compact schema representation for the LLM."""
        lines = []
        for t in self._schema_tables[:30]:  # Limit to 30 tables
            table_name = t["table_name"]
            desc = f" -- {t['description']}" if t.get("description") else ""
            cols = t.get("columns", [])
            if cols:
                col_parts = []
                for c in cols[:20]:  # Limit columns per table
                    col_str = f"{c['column_name']} {c['data_type']}"
                    if c.get("is_primary_key"):
                        col_str += " PK"
                    if c.get("description"):
                        col_str += f" /* {c['description']} */"
                    col_parts.append(col_str)
                col_list = ", ".join(col_parts)
                if len(cols) > 20:
                    col_list += f", ... ({len(cols)} total)"
                lines.append(f"  {table_name}({col_list}){desc}")
            else:
                lines.append(f"  {table_name}{desc}")
        if len(self._schema_tables) > 30:
            lines.append(
                f"  ... ({len(self._schema_tables)} tables total, "
                "use list_tables for full list)"
            )
        return "\n".join(lines) if lines else "  (no schema info available)"

    async def run(self, **kwargs: Any) -> str:
        """Validate and execute the SQL query."""
        sql = kwargs.get("sql", "")
        start_ms = time.monotonic_ns() // 1_000_000

        try:
            # Validate SQL
            cleaned_sql = validate_sql(sql, allow_write=not self._read_only)

            # Execute via pool
            pool = ConnectionPoolManager.get_instance()
            driver = await pool.get_driver(self._connector_id, self._db_config)
            result = await driver.execute_query(
                cleaned_sql,
                timeout_s=self._query_timeout,
                max_rows=self._max_rows,
            )

            output = {
                "columns": result.columns,
                "rows": result.rows,
                "row_count": result.row_count,
                "execution_time_ms": result.execution_time_ms,
            }
            if result.truncated:
                output["truncated"] = True
                output["note"] = f"Results limited to {self._max_rows} rows"

            text = json.dumps(output, ensure_ascii=False, indent=2)
            await self._log_call(start_ms, True, sql=cleaned_sql)
            return text

        except SqlSafetyError as exc:
            await self._log_call(start_ms, False, error=str(exc), sql=sql)
            return f"[SQL Safety Error] {exc}"
        except TimeoutError as exc:
            await self._log_call(start_ms, False, error=str(exc), sql=sql)
            return f"[Timeout] {exc}"
        except Exception as exc:
            await self._log_call(start_ms, False, error=str(exc), sql=sql)
            return f"[Error] {exc}"

    async def _log_call(
        self,
        start_ms: int,
        success: bool,
        error: str | None = None,
        sql: str = "",
    ) -> None:
        if self._on_call_complete:
            try:
                elapsed = time.monotonic_ns() // 1_000_000 - start_ms
                await self._on_call_complete(
                    connector_id=self._connector_id,
                    connector_name=self._connector_name,
                    action_id=None,
                    action_name="query",
                    request_method="QUERY",
                    request_url=f"db://{self._connector_name}/query",
                    response_status=200 if success else 500,
                    response_time_ms=elapsed,
                    success=success,
                    error_message=error,
                )
            except Exception:
                logger.debug("on_call_complete callback failed", exc_info=True)


class DatabaseToolAdapter:
    """Factory that creates database tools for a connector.

    Creates three tools per database connector following the same
    pattern as :class:`ConnectorToolAdapter`.
    """

    @staticmethod
    def create_tools(
        connector_name: str,
        connector_id: str,
        db_config: dict[str, Any],
        schema_tables: list[dict[str, Any]],
        on_call_complete: Callable[..., Awaitable[None]] | None = None,
    ) -> list[BaseTool]:
        """Create the standard set of database tools.

        Parameters
        ----------
        connector_name:
            Human-readable connector name.
        connector_id:
            Connector UUID for pool management and logging.
        db_config:
            Decrypted database connection config.
        schema_tables:
            List of table dicts with columns, loaded from DB.
            Each dict: ``{table_name, display_name, description, columns: [...]}``.
        on_call_complete:
            Optional async callback for logging connector calls.

        Returns
        -------
        list[BaseTool]
            Three tool instances: list_tables, describe_table, query.
        """
        read_only = db_config.get("read_only", True)
        max_rows = int(db_config.get("max_rows", 1000))
        query_timeout = int(db_config.get("query_timeout", 30))

        return [
            _DatabaseListTablesTool(
                connector_name=connector_name,
                connector_id=connector_id,
                db_config=db_config,
                schema_tables=schema_tables,
                on_call_complete=on_call_complete,
            ),
            _DatabaseDescribeTableTool(
                connector_name=connector_name,
                connector_id=connector_id,
                db_config=db_config,
                schema_tables=schema_tables,
                on_call_complete=on_call_complete,
            ),
            _DatabaseQueryTool(
                connector_name=connector_name,
                connector_id=connector_id,
                db_config=db_config,
                schema_tables=schema_tables,
                read_only=read_only,
                max_rows=max_rows,
                query_timeout=query_timeout,
                on_call_complete=on_call_complete,
            ),
        ]

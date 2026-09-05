"""DatabaseMetaTool — single tool proxy for progressive database disclosure.

Instead of registering three tools per database connector (list_tables,
describe_table, query), the DatabaseMetaTool presents a compact stub listing
(~20 tokens per database) and exposes three subcommands:

    list_tables <database>             — table names + descriptions + column counts
    discover <database> [<table>]      — full column schemas on demand
    query <database> <sql>             — execute a validated SQL query

This reduces prompt size dramatically when multiple databases are connected
while keeping full functionality.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fim_one.core.tool.base import BaseTool

from .pool import ConnectionPoolManager
from .redaction import pii_column_names, redact_rows
from .safety import SqlSafetyError, validate_sql

logger = logging.getLogger(__name__)

_DISCOVER_INDENT = int(os.getenv("DATABASE_DISCOVER_INDENT", "2"))


@dataclass(frozen=True)
class TableStub:
    """Lightweight table metadata stored for discover routing."""

    name: str
    display_name: str | None
    description: str | None
    column_count: int


@dataclass(frozen=True)
class DatabaseStub:
    """Lightweight database summary for the system prompt."""

    name: str  # sanitised connector name
    display_name: str  # original connector name
    description: str | None
    table_count: int
    tables: list[TableStub] = field(default_factory=list)
    # Full schema data for discover subcommand (table_name -> columns list)
    schema_tables: list[dict[str, Any]] = field(default_factory=list)
    # Decrypted database config for query execution
    db_config: dict[str, Any] = field(default_factory=dict)
    connector_id: str = ""
    read_only: bool = True
    max_rows: int = 1000
    query_timeout: int = 30


class DatabaseMetaTool(BaseTool):
    """A single tool that proxies all database operations.

    System prompt sees only lightweight stubs::

        database("list_tables", "my_postgres")
        database("discover", "my_postgres", table="users")
        database("query", "my_postgres", sql="SELECT * FROM users LIMIT 10")

    Subcommands:
        list_tables <database> — table names, descriptions, column counts
        discover <database> [table] — full column schemas
        query <database> <sql> — execute a validated SQL query
    """

    def __init__(
        self,
        stubs: list[DatabaseStub],
        *,
        on_call_complete: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self._stubs: dict[str, DatabaseStub] = {s.name: s for s in stubs}
        self._pii: dict[str, frozenset[str]] = {
            s.name: pii_column_names(s.schema_tables) for s in stubs
        }
        self._on_call_complete = on_call_complete

    # ------------------------------------------------------------------
    # BaseTool protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "database"

    @property
    def display_name(self) -> str:
        return "Database"

    @property
    def description(self) -> str:
        lines = ["Query connected databases. Available databases:"]
        for stub in self._stubs.values():
            desc = stub.description or stub.display_name
            table_names = [t.name for t in stub.tables[:10]]
            preview = ", ".join(table_names)
            if stub.table_count > 10:
                preview += f" ... ({stub.table_count} total)"
            lines.append(
                f"  - {stub.name}: {desc} ({stub.table_count} tables: {preview})"
            )
        lines.append("")
        lines.append("Subcommands:")
        lines.append(
            "  list_tables <database> — table names, descriptions, column counts"
        )
        lines.append(
            "  discover <database> [table] — full column schemas for one or all tables"
        )
        lines.append(
            '  query <database> <sql> — execute a SQL query'
        )
        lines.append("")
        lines.append(
            "IMPORTANT: Call 'list_tables' or 'discover' first to learn the schema "
            "before writing queries."
        )
        return "\n".join(lines)

    @property
    def category(self) -> str:
        return "database"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        # Filter out empty names (same guard as ConnectorMetaTool)
        db_names = sorted(n for n in self._stubs.keys() if n)
        database_prop: dict[str, Any] = {
            "type": "string",
            "description": "Database name",
        }
        if db_names:
            database_prop["enum"] = db_names
        return {
            "type": "object",
            "properties": {
                "subcommand": {
                    "type": "string",
                    "enum": ["list_tables", "discover", "query"],
                    "description": (
                        "list_tables: list all tables. "
                        "discover: show column schemas. "
                        "query: execute SQL."
                    ),
                },
                "database": database_prop,
                "table": {
                    "type": "string",
                    "description": (
                        "Table name (optional for discover to show one table; "
                        "omit to show all tables)"
                    ),
                },
                "sql": {
                    "type": "string",
                    "description": "SQL query to execute (required for query subcommand)",
                },
            },
            "required": ["subcommand", "database"],
        }

    async def run(self, **kwargs: Any) -> str:
        """Route to list_tables, discover, or query subcommand."""
        subcommand = kwargs.get("subcommand", "")
        database = kwargs.get("database", "")
        table = kwargs.get("table", "")
        sql = kwargs.get("sql", "")

        if not subcommand:
            return (
                "Error: 'subcommand' is required. "
                "Use 'list_tables', 'discover', or 'query'."
            )
        if not database:
            return "Error: 'database' is required."

        if subcommand == "list_tables":
            return await self._list_tables(database)
        elif subcommand == "discover":
            return self._discover(database, table or None)
        elif subcommand == "query":
            return await self._query(database, sql)
        else:
            return (
                f"Unknown subcommand: '{subcommand}'. "
                "Use 'list_tables', 'discover', or 'query'."
            )

    # ------------------------------------------------------------------
    # Subcommand implementations
    # ------------------------------------------------------------------

    async def _list_tables(self, database_name: str) -> str:
        """Return table names, descriptions, and column counts."""
        stub = self._stubs.get(database_name)
        if stub is None:
            return self._unknown_database_error(database_name)

        start_ms = time.monotonic_ns() // 1_000_000

        try:
            result = []
            for t_data in stub.schema_tables:
                entry: dict[str, Any] = {"table_name": t_data["table_name"]}
                if t_data.get("display_name"):
                    entry["display_name"] = t_data["display_name"]
                if t_data.get("description"):
                    entry["description"] = t_data["description"]
                if t_data.get("column_count"):
                    entry["column_count"] = t_data["column_count"]
                result.append(entry)

            output = json.dumps(result, ensure_ascii=False, indent=2)
            await self._log_call(
                stub, start_ms, True, action_name="list_tables"
            )
            return output
        except Exception as exc:
            await self._log_call(
                stub, start_ms, False, action_name="list_tables", error=str(exc)
            )
            return f"[Error] {exc}"

    def _discover(self, database_name: str, table_name: str | None) -> str:
        """Return formatted column schemas for one table or all tables."""
        stub = self._stubs.get(database_name)
        if stub is None:
            return self._unknown_database_error(database_name)

        if not stub.schema_tables:
            return f"Database '{database_name}' has no visible tables."

        # Build table lookup
        table_lookup: dict[str, dict[str, Any]] = {
            t["table_name"]: t for t in stub.schema_tables
        }

        if table_name:
            # Discover a single table
            table_info = table_lookup.get(table_name)
            if table_info is None:
                available = ", ".join(sorted(table_lookup.keys())[:30])
                return (
                    f"Unknown table: '{table_name}' in database '{database_name}'. "
                    f"Available tables: {available}"
                )
            return self._format_table_schema(database_name, [table_info])
        else:
            # Discover all tables
            return self._format_table_schema(
                database_name, stub.schema_tables
            )

    async def _query(self, database_name: str, sql: str) -> str:
        """Validate and execute a SQL query."""
        stub = self._stubs.get(database_name)
        if stub is None:
            return self._unknown_database_error(database_name)

        if not sql or not sql.strip():
            return "Error: 'sql' parameter is required for the query subcommand."

        start_ms = time.monotonic_ns() // 1_000_000

        try:
            # Validate SQL using existing safety module
            cleaned_sql = validate_sql(sql, allow_write=not stub.read_only)

            # Execute via connection pool
            pool = ConnectionPoolManager.get_instance()
            driver = await pool.get_driver(stub.connector_id, stub.db_config)
            result = await driver.execute_query(
                cleaned_sql,
                timeout_s=stub.query_timeout,
                max_rows=stub.max_rows,
            )

            rows, redacted = redact_rows(
                result.columns, result.rows, self._pii.get(database_name, frozenset())
            )

            output: dict[str, Any] = {
                "columns": result.columns,
                "rows": rows,
                "row_count": result.row_count,
                "execution_time_ms": result.execution_time_ms,
            }
            if result.truncated:
                output["truncated"] = True
                output["note"] = f"Results limited to {stub.max_rows} rows"
            if redacted:
                output["redacted_columns"] = redacted
                output["redaction_note"] = (
                    "Values in these columns are marked as personal data and "
                    "are masked. Re-running the query returns the same mask."
                )

            text = json.dumps(output, ensure_ascii=False, indent=2)
            await self._log_call(
                stub,
                start_ms,
                True,
                action_name="query",
                sql=cleaned_sql,
                redacted_columns=redacted,
            )
            return text

        except SqlSafetyError as exc:
            await self._log_call(
                stub, start_ms, False, action_name="query",
                error=str(exc), sql=sql,
            )
            return f"[SQL Safety Error] {exc}"
        except TimeoutError as exc:
            await self._log_call(
                stub, start_ms, False, action_name="query",
                error=str(exc), sql=sql,
            )
            return f"[Timeout] {exc}"
        except Exception as exc:
            await self._log_call(
                stub, start_ms, False, action_name="query",
                error=str(exc), sql=sql,
            )
            return f"[Error] {exc}"

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    def _format_table_schema(
        self, database_name: str, tables: list[dict[str, Any]]
    ) -> str:
        """Format table schemas in a human-readable way."""
        lines = [
            f"Database: {database_name}",
            f"Tables ({len(tables)}):",
            "",
        ]

        for t_data in tables:
            table_name = t_data["table_name"]
            desc = t_data.get("description") or ""
            display = t_data.get("display_name") or ""

            header_parts = [f"  {table_name}"]
            if display and display != table_name:
                header_parts.append(f"({display})")
            if desc:
                header_parts.append(f"-- {desc}")
            lines.append(" ".join(header_parts))

            columns = t_data.get("columns", [])
            if columns:
                lines.append("    Columns:")
                for col in columns:
                    col_parts = [
                        f"      {col['column_name']}",
                        col["data_type"],
                    ]
                    if col.get("is_primary_key"):
                        col_parts.append("PK")
                    if not col.get("is_nullable", True):
                        col_parts.append("NOT NULL")
                    col_str = " ".join(col_parts)
                    if col.get("display_name"):
                        col_str += f" ({col['display_name']})"
                    if col.get("is_pii"):
                        col_str += " [PII — values returned masked]"
                    if col.get("description"):
                        col_str += f" /* {col['description']} */"
                    lines.append(col_str)
            else:
                lines.append("    (no column info — use live introspection)")
            lines.append("")

        return "\n".join(lines)

    def _unknown_database_error(self, database_name: str) -> str:
        """Return a formatted error for unknown database names."""
        available = ", ".join(sorted(self._stubs.keys()))
        return (
            f"Unknown database: '{database_name}'. "
            f"Available databases: {available}"
        )

    # ------------------------------------------------------------------
    # Logging helper
    # ------------------------------------------------------------------

    async def _log_call(
        self,
        stub: DatabaseStub,
        start_ms: int,
        success: bool,
        *,
        action_name: str = "",
        error: str | None = None,
        sql: str = "",
        redacted_columns: list[str] | None = None,
    ) -> None:
        """Log a database tool call via the on_call_complete callback."""
        if self._on_call_complete:
            try:
                elapsed = time.monotonic_ns() // 1_000_000 - start_ms
                fences: dict[str, Any] = {"read_only": stub.read_only}
                if redacted_columns:
                    fences["redacted_columns"] = redacted_columns
                await self._on_call_complete(
                    connector_id=stub.connector_id,
                    connector_name=stub.display_name,
                    action_id=None,
                    action_name=action_name,
                    request_method="QUERY",
                    request_url=f"db://{stub.name}/{action_name}",
                    response_status=200 if success else 500,
                    response_time_ms=elapsed,
                    success=success,
                    error_message=error,
                    scope_rules_applied=json.dumps(fences, ensure_ascii=False),
                )
            except Exception:
                logger.debug("on_call_complete callback failed", exc_info=True)

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @property
    def database_names(self) -> list[str]:
        """Return sorted list of available database names."""
        return sorted(self._stubs.keys())

    @property
    def stub_count(self) -> int:
        """Return number of registered database stubs."""
        return len(self._stubs)


# ---------------------------------------------------------------------------
# Factory helper — builds a DatabaseMetaTool from collected DB connector data
# ---------------------------------------------------------------------------


def build_database_meta_tool(
    db_connectors: list[tuple[Any, dict[str, Any], list[dict[str, Any]]]],
    on_call_complete: Callable[..., Awaitable[None]] | None = None,
) -> DatabaseMetaTool:
    """Build a DatabaseMetaTool from a list of database connector tuples.

    This is the primary integration point called from ``chat.py`` when
    ``DATABASE_TOOL_MODE=progressive``.

    Args:
        db_connectors: List of tuples ``(connector_orm, decrypted_db_config, schema_tables)``.
            - ``connector_orm``: ORM Connector object with ``.id``, ``.name``, ``.description``.
            - ``decrypted_db_config``: Decrypted database config dict.
            - ``schema_tables``: List of table dicts with columns (same format as
              ``DatabaseToolAdapter.create_tools``).
        on_call_complete: Optional async callback for call logging.

    Returns:
        A fully configured DatabaseMetaTool instance.
    """
    stubs: list[DatabaseStub] = []
    seen_names: set[str] = set()

    for conn, db_config, schema_tables in db_connectors:
        # Sanitise connector name to safe identifier
        safe_name = re.sub(r"[^a-zA-Z0-9]", "_", conn.name.lower()).strip("_")
        if not safe_name:
            safe_name = f"db_{getattr(conn, 'id', '')[:8] or len(stubs)}"
        # Deduplicate: append connector ID prefix on collision
        if safe_name in seen_names:
            safe_name = f"{safe_name}_{getattr(conn, 'id', '')[:8]}"
        seen_names.add(safe_name)

        # Build lightweight table stubs for the description
        table_stubs = [
            TableStub(
                name=t["table_name"],
                display_name=t.get("display_name"),
                description=t.get("description"),
                column_count=t.get("column_count", len(t.get("columns", []))),
            )
            for t in schema_tables
        ]

        # Extract query config from db_config
        read_only = db_config.get("read_only", True)
        max_rows = int(db_config.get("max_rows", 1000))
        query_timeout = int(db_config.get("query_timeout", 30))

        stub = DatabaseStub(
            name=safe_name,
            display_name=conn.name,
            description=conn.description or conn.name,
            table_count=len(table_stubs),
            tables=table_stubs,
            schema_tables=schema_tables,
            db_config=db_config,
            connector_id=getattr(conn, "id", ""),
            read_only=read_only,
            max_rows=max_rows,
            query_timeout=query_timeout,
        )
        stubs.append(stub)

    return DatabaseMetaTool(
        stubs=stubs,
        on_call_complete=on_call_complete,
    )


def get_database_tool_mode(agent_cfg: dict[str, Any] | None = None) -> str:
    """Determine the database tool mode from environment or agent config.

    Priority:
        1. Agent-level ``model_config_json.database_tool_mode``
        2. Environment variable ``DATABASE_TOOL_MODE``
        3. Default: ``"progressive"``

    Returns:
        ``"progressive"`` or ``"legacy"``
    """
    # Check agent-level config first
    if agent_cfg:
        model_cfg = agent_cfg.get("model_config_json") or {}
        if isinstance(model_cfg, dict):
            agent_mode = model_cfg.get("database_tool_mode")
            if isinstance(agent_mode, str) and agent_mode in ("progressive", "legacy"):
                return agent_mode

    # Fall back to environment variable
    env_mode = os.environ.get("DATABASE_TOOL_MODE", "progressive").lower()
    if env_mode in ("progressive", "legacy"):
        return env_mode

    return "progressive"

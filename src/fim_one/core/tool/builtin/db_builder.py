"""Builder tools for managing Database Connector schemas via LLM agent.

Tools in this module are injected exclusively for DB Builder Agents (is_builder=True)
that have "db_builder" in their tool_categories. They are excluded from
auto-discovery to prevent regular agents from accessing them.
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..base import BaseTool

logger = logging.getLogger(__name__)


class _DbBuilderBase(BaseTool, ABC):
    """Shared base for all DB-builder tools."""

    def __init__(self, connector_id: str, user_id: str) -> None:
        self.connector_id = connector_id
        self.user_id = user_id

    @property
    def category(self) -> str:
        return "db_builder"

    async def _get_connector(self, db: Any) -> Any:
        from fim_one.web.models.connector import Connector

        result = await db.execute(
            select(Connector).where(
                Connector.id == self.connector_id,
                Connector.user_id == self.user_id,
            )
        )
        return result.scalar_one_or_none()

    async def _get_schemas(self, db: Any) -> list[Any]:
        from fim_one.web.models.database_schema import DatabaseSchema

        result = await db.execute(
            select(DatabaseSchema)
            .options(selectinload(DatabaseSchema.columns))
            .where(DatabaseSchema.connector_id == self.connector_id)
            .order_by(DatabaseSchema.table_name)
        )
        return list(result.scalars().all())


# ------------------------------------------------------------------
# DbGetConnectorSettingsTool
# ------------------------------------------------------------------


class DbGetConnectorSettingsTool(_DbBuilderBase):
    """View the database connector's current settings."""

    @property
    def name(self) -> str:
        return "db_get_connector_settings"

    @property
    def display_name(self) -> str:
        return "Get DB Connector Settings"

    @property
    def description(self) -> str:
        return (
            "View the current database connector configuration: type, host, port, "
            "database, read_only, ssl, max_rows, query_timeout. Password is masked."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs: Any) -> str:
        from fim_one.db import create_session

        async with create_session() as db:
            connector = await self._get_connector(db)
            if connector is None:
                return "[Error] Connector not found or access denied."

            cfg = dict(connector.db_config or {})
            # Mask password
            if "password" in cfg:
                cfg["password"] = "****"

            return json.dumps(
                {
                    "id": connector.id,
                    "name": connector.name,
                    "type": connector.type,
                    "db_config": cfg,
                },
                ensure_ascii=False,
                indent=2,
            )


# ------------------------------------------------------------------
# DbUpdateConnectorSettingsTool
# ------------------------------------------------------------------


class DbUpdateConnectorSettingsTool(_DbBuilderBase):
    """Update safe database connector config fields."""

    @property
    def name(self) -> str:
        return "db_update_connector_settings"

    @property
    def display_name(self) -> str:
        return "Update DB Connector Settings"

    @property
    def description(self) -> str:
        return (
            "Update safe config fields on this database connector: "
            "read_only (bool), ssl (bool), max_rows (int 1-10000), query_timeout (int 1-300). "
            "Connection credentials cannot be changed here."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "read_only": {"type": "boolean", "description": "Enable read-only mode."},
                "ssl": {"type": "boolean", "description": "Enable SSL."},
                "max_rows": {"type": "integer", "description": "Max rows returned per query (1-10000)."},
                "query_timeout": {"type": "integer", "description": "Query timeout in seconds (1-300)."},
            },
            "required": [],
        }

    async def run(self, **kwargs: Any) -> str:
        from sqlalchemy.orm.attributes import flag_modified

        from fim_one.db import create_session

        _SAFE = {"read_only", "ssl", "max_rows", "query_timeout"}
        updates = {k: v for k, v in kwargs.items() if k in _SAFE and v is not None}
        if not updates:
            return "[Error] Provide at least one of: read_only, ssl, max_rows, query_timeout."

        if "max_rows" in updates:
            updates["max_rows"] = max(1, min(10000, int(updates["max_rows"])))
        if "query_timeout" in updates:
            updates["query_timeout"] = max(1, min(300, int(updates["query_timeout"])))

        async with create_session() as db:
            connector = await self._get_connector(db)
            if connector is None:
                return "[Error] Connector not found or access denied."

            current_cfg = dict(connector.db_config or {})
            current_cfg.update(updates)
            connector.db_config = current_cfg
            flag_modified(connector, "db_config")
            await db.commit()

        return json.dumps(
            {"updated": True, "fields": list(updates.keys()), "new_values": updates},
            ensure_ascii=False,
        )


# ------------------------------------------------------------------
# DbTestConnectionTool
# ------------------------------------------------------------------


class DbTestConnectionTool(_DbBuilderBase):
    """Test the database connection."""

    @property
    def name(self) -> str:
        return "db_test_connection"

    @property
    def display_name(self) -> str:
        return "Test DB Connection"

    @property
    def description(self) -> str:
        return "Verify the database connection is working. Returns ok/error and latency_ms."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs: Any) -> str:
        from fim_one.core.security.encryption import decrypt_db_config
        from fim_one.core.tool.connector.database.pool import ConnectionPoolManager
        from fim_one.db import create_session

        async with create_session() as db:
            connector = await self._get_connector(db)
            if connector is None:
                return "[Error] Connector not found or access denied."
            raw_config = connector.db_config or {}

        try:
            config = decrypt_db_config(raw_config)
        except Exception as exc:
            return json.dumps({"ok": False, "error": f"Config decryption failed: {exc}"}, ensure_ascii=False)

        pool = ConnectionPoolManager.get_instance()
        t0 = time.monotonic()
        try:
            driver = await pool.get_driver(self.connector_id, config)
            success, version = await driver.test_connection()
            latency_ms = round((time.monotonic() - t0) * 1000)
            if success:
                return json.dumps({"ok": True, "latency_ms": latency_ms, "db_version": version}, ensure_ascii=False)
            else:
                return json.dumps({"ok": False, "error": version, "latency_ms": latency_ms}, ensure_ascii=False)
        except Exception as exc:
            latency_ms = round((time.monotonic() - t0) * 1000)
            return json.dumps({"ok": False, "error": str(exc), "latency_ms": latency_ms}, ensure_ascii=False)


# ------------------------------------------------------------------
# DbListTablesTool
# ------------------------------------------------------------------


class DbListTablesTool(_DbBuilderBase):
    """List all tables for the database connector."""

    @property
    def name(self) -> str:
        return "db_list_tables"

    @property
    def display_name(self) -> str:
        return "List DB Tables"

    @property
    def description(self) -> str:
        return (
            "List all tables for this database connector with their visibility, "
            "display_name, description, and column count."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs: Any) -> str:
        from fim_one.db import create_session

        async with create_session() as db:
            schemas = await self._get_schemas(db)

        tables = [
            {
                "table_name": s.table_name,
                "display_name": s.display_name,
                "description": s.description,
                "is_visible": s.is_visible,
                "column_count": len(s.columns or []),
            }
            for s in schemas
        ]
        return json.dumps({"total": len(tables), "tables": tables}, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------
# DbGetTableDetailTool
# ------------------------------------------------------------------


class DbGetTableDetailTool(_DbBuilderBase):
    """Get column details for a specific table."""

    @property
    def name(self) -> str:
        return "db_get_table_detail"

    @property
    def display_name(self) -> str:
        return "Get Table Detail"

    @property
    def description(self) -> str:
        return (
            "Get the column list for a specific table: column_name, data_type, "
            "display_name, description, is_visible, is_primary_key."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "description": "The table name to inspect."},
            },
            "required": ["table_name"],
        }

    async def run(self, **kwargs: Any) -> str:
        from fim_one.db import create_session
        from fim_one.web.models.database_schema import DatabaseSchema

        table_name = kwargs["table_name"]
        async with create_session() as db:
            result = await db.execute(
                select(DatabaseSchema)
                .options(selectinload(DatabaseSchema.columns))
                .where(
                    DatabaseSchema.connector_id == self.connector_id,
                    DatabaseSchema.table_name == table_name,
                )
            )
            schema = result.scalar_one_or_none()
            if schema is None:
                return f"[Error] Table '{table_name}' not found."

            columns = [
                {
                    "column_name": c.column_name,
                    "data_type": c.data_type,
                    "display_name": c.display_name,
                    "description": c.description,
                    "is_visible": c.is_visible,
                    "is_primary_key": c.is_primary_key,
                    "is_nullable": c.is_nullable,
                }
                for c in (schema.columns or [])
            ]
            return json.dumps(
                {
                    "table_name": schema.table_name,
                    "display_name": schema.display_name,
                    "description": schema.description,
                    "is_visible": schema.is_visible,
                    "columns": columns,
                },
                ensure_ascii=False,
                indent=2,
            )


# ------------------------------------------------------------------
# DbAnnotateTableTool
# ------------------------------------------------------------------


class DbAnnotateTableTool(_DbBuilderBase):
    """Set display_name and description for a table."""

    @property
    def name(self) -> str:
        return "db_annotate_table"

    @property
    def display_name(self) -> str:
        return "Annotate Table"

    @property
    def description(self) -> str:
        return "Set the display_name and description for a specific table."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "description": "The table to annotate."},
                "display_name": {"type": "string", "description": "Human-readable name for the table."},
                "description": {"type": "string", "description": "Brief description of what this table stores."},
            },
            "required": ["table_name", "display_name", "description"],
        }

    async def run(self, **kwargs: Any) -> str:
        from fim_one.db import create_session
        from fim_one.web.models.database_schema import DatabaseSchema

        table_name = kwargs["table_name"]
        async with create_session() as db:
            result = await db.execute(
                select(DatabaseSchema).where(
                    DatabaseSchema.connector_id == self.connector_id,
                    DatabaseSchema.table_name == table_name,
                )
            )
            schema = result.scalar_one_or_none()
            if schema is None:
                return f"[Error] Table '{table_name}' not found."

            schema.display_name = kwargs["display_name"]
            schema.description = kwargs["description"]
            await db.commit()

        return json.dumps(
            {"updated": True, "table_name": table_name, "display_name": kwargs["display_name"]},
            ensure_ascii=False,
        )


# ------------------------------------------------------------------
# DbAnnotateColumnTool
# ------------------------------------------------------------------


class DbAnnotateColumnTool(_DbBuilderBase):
    """Set display_name and description for a specific column."""

    @property
    def name(self) -> str:
        return "db_annotate_column"

    @property
    def display_name(self) -> str:
        return "Annotate Column"

    @property
    def description(self) -> str:
        return "Set the display_name and description for a specific column in a table."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "description": "The table containing the column."},
                "column_name": {"type": "string", "description": "The column to annotate."},
                "display_name": {"type": "string", "description": "Human-readable name for the column."},
                "description": {"type": "string", "description": "Brief description of what this column stores."},
            },
            "required": ["table_name", "column_name", "display_name", "description"],
        }

    async def run(self, **kwargs: Any) -> str:
        from fim_one.db import create_session
        from fim_one.web.models.database_schema import DatabaseSchema, SchemaColumn

        table_name = kwargs["table_name"]
        column_name = kwargs["column_name"]
        async with create_session() as db:
            result = await db.execute(
                select(SchemaColumn)
                .join(DatabaseSchema, SchemaColumn.schema_id == DatabaseSchema.id)
                .where(
                    DatabaseSchema.connector_id == self.connector_id,
                    DatabaseSchema.table_name == table_name,
                    SchemaColumn.column_name == column_name,
                )
            )
            col = result.scalar_one_or_none()
            if col is None:
                return f"[Error] Column '{column_name}' in table '{table_name}' not found."

            col.display_name = kwargs["display_name"]
            col.description = kwargs["description"]
            await db.commit()

        return json.dumps(
            {
                "updated": True,
                "table_name": table_name,
                "column_name": column_name,
                "display_name": kwargs["display_name"],
            },
            ensure_ascii=False,
        )


# ------------------------------------------------------------------
# DbSetTableVisibilityTool
# ------------------------------------------------------------------


class DbSetTableVisibilityTool(_DbBuilderBase):
    """Show or hide a specific table (and all its columns)."""

    @property
    def name(self) -> str:
        return "db_set_table_visibility"

    @property
    def display_name(self) -> str:
        return "Set Table Visibility"

    @property
    def description(self) -> str:
        return "Show or hide a specific table and all its columns."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "description": "The table to show or hide."},
                "visible": {"type": "boolean", "description": "True to show, False to hide."},
            },
            "required": ["table_name", "visible"],
        }

    async def run(self, **kwargs: Any) -> str:
        from fim_one.db import create_session
        from fim_one.web.models.database_schema import DatabaseSchema

        table_name = kwargs["table_name"]
        visible = bool(kwargs["visible"])
        async with create_session() as db:
            result = await db.execute(
                select(DatabaseSchema)
                .options(selectinload(DatabaseSchema.columns))
                .where(
                    DatabaseSchema.connector_id == self.connector_id,
                    DatabaseSchema.table_name == table_name,
                )
            )
            schema = result.scalar_one_or_none()
            if schema is None:
                return f"[Error] Table '{table_name}' not found."

            schema.is_visible = visible
            for col in (schema.columns or []):
                col.is_visible = visible
            await db.commit()

        action = "shown" if visible else "hidden"
        return json.dumps({"updated": True, "table_name": table_name, "action": action}, ensure_ascii=False)


# ------------------------------------------------------------------
# DbBatchSetVisibilityTool
# ------------------------------------------------------------------


class DbBatchSetVisibilityTool(_DbBuilderBase):
    """Bulk show/hide tables by prefix or exact name."""

    @property
    def name(self) -> str:
        return "db_batch_set_visibility"

    @property
    def display_name(self) -> str:
        return "Batch Set Table Visibility"

    @property
    def description(self) -> str:
        return (
            "Bulk show or hide tables by prefix list or exact name list. "
            "Matches by prefix (e.g. 'sys_', 'django_') OR exact table_name. "
            "At least one of prefixes or table_names must be provided."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prefixes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Table name prefixes to match (e.g. ['sys_', 'tmp_', 'django_']).",
                },
                "table_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Exact table names to match.",
                },
                "visible": {"type": "boolean", "description": "True to show, False to hide."},
            },
            "required": ["visible"],
        }

    async def run(self, **kwargs: Any) -> str:
        from fim_one.db import create_session

        prefixes: list[str] = kwargs.get("prefixes") or []
        table_names_filter: list[str] = kwargs.get("table_names") or []
        visible = bool(kwargs["visible"])

        if not prefixes and not table_names_filter:
            return "[Error] Provide at least one of: prefixes, table_names."

        exact_set = set(table_names_filter)

        async with create_session() as db:
            schemas = await self._get_schemas(db)
            matched = 0
            for s in schemas:
                hit = s.table_name in exact_set or any(
                    s.table_name.startswith(p) for p in prefixes
                )
                if hit:
                    s.is_visible = visible
                    for col in (s.columns or []):
                        col.is_visible = visible
                    matched += 1
            await db.commit()

        action = "shown" if visible else "hidden"
        return json.dumps(
            {"updated": True, "matched": matched, "action": action},
            ensure_ascii=False,
        )


# ------------------------------------------------------------------
# DbRunSampleQueryTool
# ------------------------------------------------------------------


class DbRunSampleQueryTool(_DbBuilderBase):
    """Run a sample SELECT query on a table to understand its content."""

    @property
    def name(self) -> str:
        return "db_run_sample_query"

    @property
    def display_name(self) -> str:
        return "Run Sample Query"

    @property
    def description(self) -> str:
        return (
            "Run SELECT * FROM {table} LIMIT {limit} to inspect sample data. "
            "Useful for understanding what a table stores before annotating."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "description": "The table to sample."},
                "limit": {"type": "integer", "description": "Max rows to return (default 5, max 20)."},
            },
            "required": ["table_name"],
        }

    async def run(self, **kwargs: Any) -> str:
        import re

        from fim_one.core.security.encryption import decrypt_db_config
        from fim_one.core.tool.connector.database.pool import ConnectionPoolManager
        from fim_one.db import create_session

        table_name = kwargs["table_name"]
        limit = min(int(kwargs.get("limit") or 5), 20)

        # Validate table name to prevent SQL injection
        if not re.match(r'^[a-zA-Z0-9_\.]+$', table_name):
            return "[Error] Invalid table name."

        async with create_session() as db:
            connector = await self._get_connector(db)
            if connector is None:
                return "[Error] Connector not found or access denied."
            raw_config = connector.db_config or {}

        try:
            config = decrypt_db_config(raw_config)
        except Exception as exc:
            return json.dumps({"ok": False, "error": f"Config decryption failed: {exc}"}, ensure_ascii=False)

        pool = ConnectionPoolManager.get_instance()
        try:
            from fim_one.core.tool.connector.database.safety import validate_sql

            query = f"SELECT * FROM {table_name} LIMIT {limit}"
            query = validate_sql(query)  # defense-in-depth
            driver = await pool.get_driver(self.connector_id, config)
            result = await driver.execute_query(
                query,
                timeout_s=30,
                max_rows=limit,
            )
            return json.dumps(
                {
                    "table_name": table_name,
                    "columns": result.columns,
                    "rows": result.rows,
                    "row_count": result.row_count,
                },
                ensure_ascii=False,
                indent=2,
            )
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)

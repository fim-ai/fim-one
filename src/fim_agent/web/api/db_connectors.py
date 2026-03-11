"""Database connector management API.

Endpoints for testing connections, introspecting schemas, managing
table/column annotations, executing test queries, and AI annotation.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fim_agent.core.security.encryption import decrypt_db_config
from fim_agent.core.tool.connector.database.pool import ConnectionPoolManager
from fim_agent.core.tool.connector.database.safety import SqlSafetyError, validate_sql
from fim_agent.db import get_session
from fim_agent.web.auth import get_current_user
from fim_agent.web.exceptions import AppError
from fim_agent.web.models.connector import Connector
from fim_agent.web.models.database_schema import DatabaseSchema, SchemaColumn
from fim_agent.web.models.user import User
from fim_agent.web.schemas.common import ApiResponse
from fim_agent.web.schemas.db_connector import (
    AiAnnotateRequest,
    AiAnnotateResponse,
    BulkSchemaUpdate,
    IntrospectResponse,
    QueryRequest,
    QueryResponse,
    SchemaColumnResponse,
    SchemaColumnUpdate,
    SchemaTableResponse,
    SchemaTableUpdate,
    TestConnectionRequest,
    TestConnectionResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connectors", tags=["db-connectors"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_db_connector(
    connector_id: str, user_id: str, db: AsyncSession
) -> Connector:
    """Load a connector and verify it belongs to the user and is type=database."""
    result = await db.execute(
        select(Connector).where(
            Connector.id == connector_id,
            Connector.user_id == user_id,
        )
    )
    connector = result.scalar_one_or_none()
    if connector is None:
        raise AppError("connector_not_found", status_code=404)
    if connector.type != "database":
        raise AppError(
            "connector_not_database",
            status_code=400,
            detail="This endpoint only works with database connectors",
        )
    return connector


def _get_decrypted_config(connector: Connector) -> dict[str, Any]:
    """Decrypt the db_config from a connector."""
    if not connector.db_config:
        raise AppError(
            "db_config_missing",
            status_code=400,
            detail="Database connector has no connection config",
        )
    return decrypt_db_config(connector.db_config)


def _schema_table_to_response(table: DatabaseSchema) -> SchemaTableResponse:
    """Convert a DatabaseSchema ORM instance to response schema."""
    return SchemaTableResponse(
        id=table.id,
        table_name=table.table_name,
        display_name=table.display_name,
        description=table.description,
        is_visible=table.is_visible,
        columns=[
            SchemaColumnResponse(
                id=col.id,
                column_name=col.column_name,
                display_name=col.display_name,
                description=col.description,
                data_type=col.data_type,
                is_nullable=col.is_nullable,
                is_primary_key=col.is_primary_key,
                is_visible=col.is_visible,
            )
            for col in (table.columns or [])
        ],
    )


# ---------------------------------------------------------------------------
# Test Connection
# ---------------------------------------------------------------------------


@router.post("/test-connection", response_model=ApiResponse)
async def test_connection_adhoc(
    body: TestConnectionRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Test database connectivity with provided config (no saved connector needed).

    When the password field is the masked sentinel ``***`` and a
    ``connector_id`` is supplied, the real password is fetched from
    the saved connector's encrypted config.
    """
    from fim_agent.core.tool.connector.database.drivers import DRIVER_REGISTRY

    config = body.db_config.model_dump(by_alias=True)

    # Resolve masked password from saved connector
    password = config.get("password", "")
    if password == "***" and body.connector_id:
        connector = await db.get(Connector, body.connector_id)
        if connector and connector.db_config:
            real_config = decrypt_db_config(connector.db_config)
            config["password"] = real_config.get("password", "")

    driver_name = config.get("driver", "postgresql")
    driver_cls = DRIVER_REGISTRY.get(driver_name)
    if not driver_cls:
        return ApiResponse(
            data=TestConnectionResponse(
                success=False, error=f"Unsupported driver: {driver_name}"
            ).model_dump()
        )

    driver = driver_cls(config)
    try:
        success, version = await driver.test_connection()
        resp = TestConnectionResponse(
            success=success,
            db_version=version if success else None,
            error=version if not success else None,
        )
        return ApiResponse(data=resp.model_dump())
    except Exception as exc:
        return ApiResponse(
            data=TestConnectionResponse(success=False, error=str(exc)).model_dump()
        )
    finally:
        await driver.disconnect()


@router.post("/{connector_id}/test-connection", response_model=ApiResponse)
async def test_connection(
    connector_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Test database connectivity for a connector."""
    connector = await _get_db_connector(connector_id, current_user.id, db)
    config = _get_decrypted_config(connector)

    pool = ConnectionPoolManager.get_instance()
    # Close existing driver to force reconnect with current config
    await pool.close_driver(connector_id)

    try:
        driver = await pool.get_driver(connector_id, config)
        success, version = await driver.test_connection()

        resp = TestConnectionResponse(
            success=success,
            db_version=version if success else None,
            error=version if not success else None,
        )
        return ApiResponse(data=resp.model_dump())
    except Exception as exc:
        resp = TestConnectionResponse(success=False, error=str(exc))
        return ApiResponse(data=resp.model_dump())


# ---------------------------------------------------------------------------
# Introspect
# ---------------------------------------------------------------------------


@router.post("/{connector_id}/introspect", response_model=ApiResponse)
async def introspect(
    connector_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Auto-discover tables and columns, upsert into database_schemas/schema_columns."""
    connector = await _get_db_connector(connector_id, current_user.id, db)
    config = _get_decrypted_config(connector)

    pool = ConnectionPoolManager.get_instance()
    driver = await pool.get_driver(connector_id, config)
    schema_name = config.get("schema")

    # Discover tables
    tables = await driver.list_tables(schema=schema_name)

    # Load existing schemas for this connector
    existing_result = await db.execute(
        select(DatabaseSchema)
        .options(selectinload(DatabaseSchema.columns))
        .where(DatabaseSchema.connector_id == connector_id)
    )
    existing_schemas = {s.table_name: s for s in existing_result.scalars().all()}

    total_columns = 0

    for table_info in tables:
        col_infos = await driver.describe_table(table_info.table_name, schema=schema_name)
        total_columns += len(col_infos)

        existing = existing_schemas.get(table_info.table_name)
        if existing:
            # Update existing schema — preserve user annotations
            existing_col_lookup = {c.column_name: c for c in (existing.columns or [])}

            for col in col_infos:
                if col.column_name in existing_col_lookup:
                    # Update data_type and nullability if changed
                    ec = existing_col_lookup[col.column_name]
                    ec.data_type = col.data_type
                    ec.is_nullable = col.is_nullable
                    ec.is_primary_key = col.is_primary_key
                else:
                    # New column
                    new_col = SchemaColumn(
                        id=str(uuid.uuid4()),
                        schema_id=existing.id,
                        column_name=col.column_name,
                        data_type=col.data_type,
                        is_nullable=col.is_nullable,
                        is_primary_key=col.is_primary_key,
                    )
                    db.add(new_col)

            # Remove columns that no longer exist
            live_col_names = {c.column_name for c in col_infos}
            for ec in list(existing.columns or []):
                if ec.column_name not in live_col_names:
                    await db.delete(ec)
        else:
            # New table
            schema_obj = DatabaseSchema(
                id=str(uuid.uuid4()),
                connector_id=connector_id,
                table_name=table_info.table_name,
            )
            db.add(schema_obj)
            await db.flush()

            for col in col_infos:
                col_obj = SchemaColumn(
                    id=str(uuid.uuid4()),
                    schema_id=schema_obj.id,
                    column_name=col.column_name,
                    data_type=col.data_type,
                    is_nullable=col.is_nullable,
                    is_primary_key=col.is_primary_key,
                )
                db.add(col_obj)

    # Remove schemas for tables that no longer exist
    live_table_names = {t.table_name for t in tables}
    for table_name, schema_obj in existing_schemas.items():
        if table_name not in live_table_names:
            await db.delete(schema_obj)

    await db.commit()

    resp = IntrospectResponse(
        tables_discovered=len(tables),
        columns_discovered=total_columns,
    )
    return ApiResponse(data=resp.model_dump())


# ---------------------------------------------------------------------------
# Schema CRUD
# ---------------------------------------------------------------------------


@router.get("/{connector_id}/schemas", response_model=ApiResponse)
async def list_schemas(
    connector_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """List all table schemas with columns for a database connector."""
    await _get_db_connector(connector_id, current_user.id, db)

    result = await db.execute(
        select(DatabaseSchema)
        .options(selectinload(DatabaseSchema.columns))
        .where(DatabaseSchema.connector_id == connector_id)
        .order_by(DatabaseSchema.table_name)
    )
    schemas = result.scalars().all()

    return ApiResponse(
        data=[_schema_table_to_response(s).model_dump() for s in schemas]
    )


@router.put("/{connector_id}/schemas/{schema_id}", response_model=ApiResponse)
async def update_schema_table(
    connector_id: str,
    schema_id: str,
    body: SchemaTableUpdate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Update annotations for a table schema."""
    await _get_db_connector(connector_id, current_user.id, db)

    result = await db.execute(
        select(DatabaseSchema)
        .options(selectinload(DatabaseSchema.columns))
        .where(
            DatabaseSchema.id == schema_id,
            DatabaseSchema.connector_id == connector_id,
        )
    )
    schema_obj = result.scalar_one_or_none()
    if schema_obj is None:
        raise AppError("schema_not_found", status_code=404)

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(schema_obj, field, value)

    await db.commit()

    # Reload
    result = await db.execute(
        select(DatabaseSchema)
        .options(selectinload(DatabaseSchema.columns))
        .where(DatabaseSchema.id == schema_id)
    )
    schema_obj = result.scalar_one()
    return ApiResponse(data=_schema_table_to_response(schema_obj).model_dump())


@router.put(
    "/{connector_id}/schemas/{schema_id}/columns/{col_id}",
    response_model=ApiResponse,
)
async def update_schema_column(
    connector_id: str,
    schema_id: str,
    col_id: str,
    body: SchemaColumnUpdate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Update annotations for a schema column."""
    await _get_db_connector(connector_id, current_user.id, db)

    result = await db.execute(
        select(SchemaColumn).where(
            SchemaColumn.id == col_id,
            SchemaColumn.schema_id == schema_id,
        )
    )
    col_obj = result.scalar_one_or_none()
    if col_obj is None:
        raise AppError("column_not_found", status_code=404)

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(col_obj, field, value)

    await db.commit()

    result = await db.execute(
        select(SchemaColumn).where(SchemaColumn.id == col_id)
    )
    col_obj = result.scalar_one()
    return ApiResponse(
        data=SchemaColumnResponse(
            id=col_obj.id,
            column_name=col_obj.column_name,
            display_name=col_obj.display_name,
            description=col_obj.description,
            data_type=col_obj.data_type,
            is_nullable=col_obj.is_nullable,
            is_primary_key=col_obj.is_primary_key,
            is_visible=col_obj.is_visible,
        ).model_dump()
    )


@router.put("/{connector_id}/schemas/bulk", response_model=ApiResponse)
async def bulk_update_schemas(
    connector_id: str,
    body: BulkSchemaUpdate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Bulk update annotations for tables and columns."""
    await _get_db_connector(connector_id, current_user.id, db)

    # Load all schemas for this connector
    result = await db.execute(
        select(DatabaseSchema)
        .options(selectinload(DatabaseSchema.columns))
        .where(DatabaseSchema.connector_id == connector_id)
    )
    schema_lookup = {s.table_name: s for s in result.scalars().all()}

    updated_count = 0

    for table_update in body.tables:
        table_name = table_update.get("table_name")
        if not table_name or table_name not in schema_lookup:
            continue

        schema_obj = schema_lookup[table_name]

        # Update table-level fields
        for field in ("display_name", "description", "is_visible"):
            if field in table_update:
                setattr(schema_obj, field, table_update[field])
                updated_count += 1

        # Update column-level fields
        col_updates = table_update.get("columns", [])
        if col_updates:
            col_lookup = {c.column_name: c for c in (schema_obj.columns or [])}
            for cu in col_updates:
                col_name = cu.get("column_name")
                if not col_name or col_name not in col_lookup:
                    continue
                col_obj = col_lookup[col_name]
                for field in ("display_name", "description", "is_visible"):
                    if field in cu:
                        setattr(col_obj, field, cu[field])
                        updated_count += 1

    await db.commit()
    return ApiResponse(data={"updated_count": updated_count})


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


@router.post("/{connector_id}/query", response_model=ApiResponse)
async def execute_query(
    connector_id: str,
    body: QueryRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Execute a test SQL query against the database connector."""
    connector = await _get_db_connector(connector_id, current_user.id, db)
    config = _get_decrypted_config(connector)

    read_only = config.get("read_only", True)
    max_rows = int(config.get("max_rows", 1000))
    query_timeout = int(config.get("query_timeout", 30))

    try:
        cleaned_sql = validate_sql(body.sql, allow_write=not read_only)
    except SqlSafetyError as exc:
        resp = QueryResponse(error=str(exc))
        return ApiResponse(data=resp.model_dump())

    pool = ConnectionPoolManager.get_instance()
    try:
        driver = await pool.get_driver(connector_id, config)
        result = await driver.execute_query(
            cleaned_sql, timeout_s=query_timeout, max_rows=max_rows
        )
        resp = QueryResponse(
            columns=result.columns,
            rows=result.rows,
            row_count=result.row_count,
            truncated=result.truncated,
            execution_time_ms=result.execution_time_ms,
        )
        return ApiResponse(data=resp.model_dump())
    except Exception as exc:
        resp = QueryResponse(error=str(exc))
        return ApiResponse(data=resp.model_dump())


# ---------------------------------------------------------------------------
# AI Annotate
# ---------------------------------------------------------------------------


_AI_ANNOTATE_SYSTEM_PROMPT = """\
You are a database schema expert. Given table and column metadata from a \
database, generate human-readable Chinese display names and brief Chinese \
descriptions.

Rules:
- display_name: concise Chinese name (2-6 characters), e.g. "用户信息", "订单明细"
- description: one-sentence Chinese description of what this table/column stores
- For common patterns (id, created_at, updated_at, is_deleted, etc.) use \
standard translations
- Infer meaning from the table context — e.g. a column "status" in an \
"orders" table should be "订单状态", not just "状态"

Respond with ONLY a JSON array (no markdown fences, no explanation):
[
  {
    "table_name": "original_table_name",
    "display_name": "中文表名",
    "description": "中文表描述",
    "columns": [
      {"column_name": "original_col", "display_name": "中文列名", "description": "中文列描述"},
      ...
    ]
  },
  ...
]"""


def _build_annotate_user_prompt(
    schemas: list[DatabaseSchema],
) -> str:
    """Build the user prompt listing tables and columns for annotation."""
    lines: list[str] = ["Annotate the following tables:\n"]
    for idx, schema_obj in enumerate(schemas, 1):
        lines.append(f"{idx}. Table: {schema_obj.table_name}")
        col_parts: list[str] = []
        for col in schema_obj.columns or []:
            parts = [col.column_name, col.data_type]
            if col.is_primary_key:
                parts.append("PK")
            if not col.is_nullable:
                parts.append("NOT NULL")
            col_parts.append(f"{' '.join(parts)}")
        if col_parts:
            lines.append(f"   Columns: {', '.join(col_parts)}")
        lines.append("")
    return "\n".join(lines)


def _parse_llm_annotations(
    raw_text: str,
) -> list[dict[str, Any]] | None:
    """Parse the LLM JSON response, tolerating markdown fences.

    Returns a list of table annotation dicts, or ``None`` on parse failure.
    """
    import json
    import re

    text = raw_text.strip()
    # Strip markdown code fences if present
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Try to find the first JSON array in the text
        arr_match = re.search(r"\[.*\]", text, re.DOTALL)
        if arr_match:
            try:
                parsed = json.loads(arr_match.group(0))
            except json.JSONDecodeError:
                return None
        else:
            return None

    if isinstance(parsed, list):
        return parsed

    # json_object mode often wraps the array in an object like {"tables": [...]}
    if isinstance(parsed, dict):
        for value in parsed.values():
            if isinstance(value, list) and value:
                return value

    return None


def _apply_annotations(
    schemas: list[DatabaseSchema],
    annotations: list[dict[str, Any]],
) -> int:
    """Apply parsed LLM annotations to ORM objects.

    Only overwrites fields that the LLM returned (non-empty strings).
    Returns the count of fields actually updated.
    """
    ann_by_table = {a["table_name"]: a for a in annotations if "table_name" in a}
    updated = 0

    for schema_obj in schemas:
        ann = ann_by_table.get(schema_obj.table_name)
        if not ann:
            continue

        # Table-level annotations
        if ann.get("display_name"):
            schema_obj.display_name = ann["display_name"]
            updated += 1
        if ann.get("description"):
            schema_obj.description = ann["description"]
            updated += 1

        # Column-level annotations
        col_anns = ann.get("columns")
        if not col_anns or not isinstance(col_anns, list):
            continue

        col_ann_lookup = {
            c["column_name"]: c for c in col_anns if isinstance(c, dict) and "column_name" in c
        }
        for col in schema_obj.columns or []:
            ca = col_ann_lookup.get(col.column_name)
            if not ca:
                continue
            if ca.get("display_name"):
                col.display_name = ca["display_name"]
                updated += 1
            if ca.get("description"):
                col.description = ca["description"]
                updated += 1

    return updated


def _humanize_name(name: str) -> str:
    """Convert snake_case or camelCase to a human-readable name (fallback)."""
    import re

    # Insert space before uppercase letters (camelCase)
    result = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    # Replace underscores and hyphens with spaces
    result = result.replace("_", " ").replace("-", " ")
    # Title case
    return result.title()


def _apply_humanize_fallback(schemas: list[DatabaseSchema]) -> int:
    """Apply _humanize_name as a fallback when LLM is unavailable.

    Only fills in display_name where it is not already set.
    Returns the count of fields updated.
    """
    updated = 0
    for schema_obj in schemas:
        if not schema_obj.display_name:
            schema_obj.display_name = _humanize_name(schema_obj.table_name)
            updated += 1
        for col in schema_obj.columns or []:
            if not col.display_name:
                col.display_name = _humanize_name(col.column_name)
                updated += 1
    return updated


@router.post("/{connector_id}/ai/annotate", response_model=ApiResponse)
async def ai_annotate(
    connector_id: str,
    body: AiAnnotateRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Generate LLM-powered Chinese annotations for table and column metadata.

    Sends table/column names and types to the fast LLM, which returns
    display_name and description in Chinese. Falls back to simple
    ``_humanize_name()`` if the LLM call fails.
    """
    await _get_db_connector(connector_id, current_user.id, db)

    # Build query — table_ids takes precedence over table_names
    stmt = (
        select(DatabaseSchema)
        .options(selectinload(DatabaseSchema.columns))
        .where(DatabaseSchema.connector_id == connector_id)
    )
    if body.table_ids:
        stmt = stmt.where(DatabaseSchema.id.in_(body.table_ids))
    elif body.table_names:
        stmt = stmt.where(DatabaseSchema.table_name.in_(body.table_names))

    result = await db.execute(stmt)
    schemas = list(result.scalars().all())

    if not schemas:
        return ApiResponse(
            data=AiAnnotateResponse(annotated_count=0, preview=[]).model_dump()
        )

    # --- Attempt LLM annotation ---
    annotated = 0
    llm_used = False

    try:
        from fim_agent.core.model.types import ChatMessage
        from fim_agent.web.deps import get_effective_fast_llm

        llm = await get_effective_fast_llm(db)
        user_prompt = _build_annotate_user_prompt(schemas)

        llm_result = await llm.chat(
            messages=[
                ChatMessage(role="system", content=_AI_ANNOTATE_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user_prompt),
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        raw_text = llm_result.message.content or ""
        parsed = _parse_llm_annotations(raw_text)

        if parsed:
            annotated = _apply_annotations(schemas, parsed)
            llm_used = True
            logger.info(
                "AI annotate: LLM updated %d fields for %d tables (connector=%s)",
                annotated,
                len(schemas),
                connector_id,
            )
        else:
            logger.warning(
                "AI annotate: LLM returned unparseable response, "
                "falling back to humanize (connector=%s)",
                connector_id,
            )
    except Exception:
        logger.exception(
            "AI annotate: LLM call failed, falling back to humanize (connector=%s)",
            connector_id,
        )

    # Fallback: fill any remaining empty display_names with humanize
    if not llm_used:
        annotated = _apply_humanize_fallback(schemas)

    await db.commit()

    # Build preview
    preview: list[dict[str, Any]] = []
    for schema_obj in schemas:
        col_previews = [
            {
                "column_name": col.column_name,
                "display_name": col.display_name,
                "description": col.description,
            }
            for col in (schema_obj.columns or [])
        ]
        preview.append({
            "table_name": schema_obj.table_name,
            "display_name": schema_obj.display_name,
            "description": schema_obj.description,
            "columns": col_previews,
        })

    resp = AiAnnotateResponse(annotated_count=annotated, preview=preview)
    return ApiResponse(data=resp.model_dump())

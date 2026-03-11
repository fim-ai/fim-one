"""Database connector management API.

Endpoints for testing connections, introspecting schemas, managing
table/column annotations, executing test queries, and AI annotation.
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fim_agent.core.model.base import BaseLLM
from fim_agent.core.utils import get_language_directive
from fim_agent.core.security.encryption import decrypt_db_config
from fim_agent.core.tool.connector.database.pool import ConnectionPoolManager
from fim_agent.core.tool.connector.database.safety import SqlSafetyError, validate_sql
from fim_agent.db import create_session, get_session
from fim_agent.web.auth import get_current_user
from fim_agent.web.exceptions import AppError
from fim_agent.web.models.connector import Connector
from fim_agent.web.models.database_schema import DatabaseSchema, SchemaColumn
from fim_agent.web.models.user import User
from fim_agent.web.schemas.common import ApiResponse
from fim_agent.web.schemas.db_connector import (
    AiAnnotateJobResponse,
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


_DB_CHAT_INTENT_SYSTEM_PROMPT = """\
You are a database schema management assistant. Analyze the user message and \
determine their intent.

Return a JSON object with:
- "intent": one of "annotate", "show", "hide", "update_settings", "unknown"
  - "annotate": user wants AI-generated display names / descriptions \
(keywords like: annotate, translate, 标注, 翻译, 描述, 中文, display name, rename)
  - "show": user wants to make tables visible in the UI
  - "hide": user wants to hide tables from the UI
  - "update_settings": user wants to change connector settings — only these \
safe fields are supported: read_only (bool), ssl (bool), max_rows (int 1-10000), \
query_timeout (int 1-300). Connection credentials (host, port, database, \
username, password, driver) CANNOT be changed via chat.
  - "unknown": message is unrelated to any of the above
- "table_names": list of table names (from the available list) that should be \
affected by annotate/show/hide. Empty list means ALL tables. Irrelevant for \
update_settings.
- "settings_updates": (only when intent is "update_settings") object with the \
fields to change. Only include fields explicitly mentioned by the user. \
Example: {"read_only": false} or {"max_rows": 500, "query_timeout": 60}
- "reply": (only when intent is "unknown") a brief message explaining what \
operations are supported.

The list of available table names is provided in the user message."""

_DB_CHAT_INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["annotate", "show", "hide", "update_settings", "unknown"],
        },
        "table_names": {"type": "array", "items": {"type": "string"}},
        "settings_updates": {
            "type": ["object", "null"],
            "properties": {
                "read_only": {"type": ["boolean", "null"]},
                "ssl": {"type": ["boolean", "null"]},
                "max_rows": {"type": ["integer", "null"]},
                "query_timeout": {"type": ["integer", "null"]},
            },
        },
        "reply": {"type": ["string", "null"]},
    },
    "required": ["intent", "table_names"],
}


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

Respond with ONLY a JSON object (no markdown, no explanation):
{"tables": [{"table_name": "original_table_name", "display_name": "中文表名", "description": "中文表描述", "columns": [{"column_name": "original_col", "display_name": "中文列名", "description": "中文列描述"}]}]}"""


_ANNOTATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tables": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "table_name": {"type": "string"},
                    "display_name": {"type": "string"},
                    "description": {"type": "string"},
                    "columns": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "column_name": {"type": "string"},
                                "display_name": {"type": "string"},
                                "description": {"type": "string"},
                            },
                            "required": ["column_name", "display_name"],
                        },
                    },
                },
                "required": ["table_name", "display_name"],
            },
        },
    },
    "required": ["tables"],
}

_ANNOTATE_BATCH_SIZE = 30  # tables per LLM call
_ANNOTATE_CONCURRENCY = 5  # parallel LLM calls


@dataclass
class _AnnotateJob:
    job_id: str
    status: str = "pending"  # pending | running | done | error
    completed_batches: int = 0
    total_batches: int = 0
    annotated_count: int = 0
    error: str | None = None


_annotate_jobs: dict[str, _AnnotateJob] = {}


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


def _build_humanize_data(schemas: list[DatabaseSchema]) -> list[dict[str, Any]]:
    """Build annotation dicts using _humanize_name() as a last-resort fallback.

    Returns data in the same shape as the LLM output so _apply_annotations
    can handle both paths uniformly.
    """
    result = []
    for schema_obj in schemas:
        entry: dict[str, Any] = {
            "table_name": schema_obj.table_name,
            "display_name": schema_obj.display_name or _humanize_name(schema_obj.table_name),
        }
        cols = [
            {
                "column_name": col.column_name,
                "display_name": col.display_name or _humanize_name(col.column_name),
            }
            for col in (schema_obj.columns or [])
        ]
        if cols:
            entry["columns"] = cols
        result.append(entry)
    return result


async def _run_annotate_all_job(
    job: _AnnotateJob,
    schema_ids: list[str],
    llm: BaseLLM,
) -> None:
    """Background task: annotate all tables in batches with bounded concurrency."""
    job.status = "running"
    batches = [
        schema_ids[i : i + _ANNOTATE_BATCH_SIZE]
        for i in range(0, len(schema_ids), _ANNOTATE_BATCH_SIZE)
    ]
    job.total_batches = len(batches)

    sem = asyncio.Semaphore(_ANNOTATE_CONCURRENCY)

    from fim_agent.core.model.structured import structured_llm_call
    from fim_agent.core.model.types import ChatMessage

    async def process_batch(batch_ids: list[str]) -> int:
        async with sem:
            async with create_session() as session:
                stmt = (
                    select(DatabaseSchema)
                    .options(selectinload(DatabaseSchema.columns))
                    .where(DatabaseSchema.id.in_(batch_ids))
                )
                result = await session.execute(stmt)
                batch_schemas = list(result.scalars().all())
                if not batch_schemas:
                    return 0
                messages = [
                    ChatMessage(role="system", content=_AI_ANNOTATE_SYSTEM_PROMPT),
                    ChatMessage(role="user", content=_build_annotate_user_prompt(batch_schemas)),
                ]
                sc_result = await structured_llm_call(
                    llm,
                    messages,
                    schema=_ANNOTATE_SCHEMA,
                    function_name="annotate_schema",
                    parse_fn=lambda d: d.get("tables", []),
                    default_value=_build_humanize_data(batch_schemas),
                    temperature=0.3,
                )
                annotations: list[dict[str, Any]] = sc_result.value or []
                count = _apply_annotations(batch_schemas, annotations)
                await session.commit()
                return count

    async def process_batch_tracked(batch_ids: list[str]) -> int:
        try:
            count = await process_batch(batch_ids)
        except Exception as exc:
            logger.warning("Annotate batch failed (job=%s): %s", job.job_id, exc)
            count = 0
        finally:
            job.completed_batches += 1
        return count

    try:
        results = await asyncio.gather(
            *[process_batch_tracked(b) for b in batches]
        )
        job.annotated_count = sum(results)
        job.status = "done"
    except Exception as exc:
        logger.exception("Annotate job %s failed: %s", job.job_id, exc)
        job.status = "error"
        job.error = str(exc)


class _DbChatRequest(BaseModel):
    message: str
    history: list[dict] = Field(default_factory=list)


@router.post("/{connector_id}/ai/db-chat")
async def db_ai_chat(
    connector_id: str,
    req: _DbChatRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict:
    """Natural language interface for DB schema management (annotate, visibility toggle)."""
    try:
        from fim_agent.core.model.structured import structured_llm_call
        from fim_agent.core.model.types import ChatMessage
        from fim_agent.web.deps import get_effective_fast_llm

        connector = await _get_db_connector(connector_id, current_user.id, db)

        result = await db.execute(
            select(DatabaseSchema)
            .options(selectinload(DatabaseSchema.columns))
            .where(DatabaseSchema.connector_id == connector_id)
            .order_by(DatabaseSchema.table_name)
        )
        schemas = list(result.scalars().all())

        if not schemas:
            return {
                "ok": False,
                "message": "No schema found. Please introspect the database first.",
                "changes": 0,
            }

        # Step 1: lightweight intent detection — only pass table names, not full schema.
        table_names_list = ", ".join(s.table_name for s in schemas)
        intent_user_msg = (
            f"Available tables: {table_names_list}\n\nUser message: {req.message}"
        )
        lang_directive = get_language_directive(current_user.preferred_language)
        intent_system = _DB_CHAT_INTENT_SYSTEM_PROMPT
        if lang_directive:
            intent_system = (
                intent_system
                + f"\n\n{lang_directive} "
                "This applies to the 'reply' field only. Keep JSON keys in English."
            )
        llm = await get_effective_fast_llm(db)
        intent_messages = [ChatMessage(role="system", content=intent_system)]
        for turn in (req.history or []):
            intent_messages.append(ChatMessage(role=turn["role"], content=turn["content"]))
        intent_messages.append(ChatMessage(role="user", content=intent_user_msg))
        intent_result = await structured_llm_call(
            llm,
            intent_messages,
            schema=_DB_CHAT_INTENT_SCHEMA,
            function_name="detect_intent",
            default_value={"intent": "annotate", "table_names": []},
            temperature=0.0,
        )
        intent_data: dict = intent_result.value or {"intent": "annotate", "table_names": []}
        intent = intent_data.get("intent", "annotate")
        targeted_names: list[str] = intent_data.get("table_names") or []

        # Map LLM-identified names back to ORM objects; empty = all tables.
        name_set = set(targeted_names)
        target_schemas = (
            [s for s in schemas if s.table_name in name_set] if name_set else schemas
        )
        if not target_schemas:
            target_schemas = schemas

        # Step 2: execute the identified intent.
        if intent == "unknown":
            reply = intent_data.get("reply") or "我只能处理表的标注和显示/隐藏操作。"
            return {"ok": False, "message": reply, "changes": 0}

        if intent == "annotate":
            if len(target_schemas) > 50:
                # Large DB: delegate to background batch job to avoid sync timeout.
                schema_ids = [s.id for s in target_schemas]
                job_id = str(uuid.uuid4())
                job = _AnnotateJob(job_id=job_id, total_batches=0)
                _annotate_jobs[job_id] = job
                asyncio.create_task(_run_annotate_all_job(job, schema_ids, llm))
                n_batches = math.ceil(len(schema_ids) / 30)
                return {
                    "ok": True,
                    "message": (
                        f"已启动批量标注任务（共 {n_batches} 批，{len(schema_ids)} 张表），"
                        "请稍后在 Schema 页面查看进度"
                    ),
                    "changes": 0,
                    "job_id": job_id,
                }

            annotate_result = await structured_llm_call(
                llm,
                [
                    ChatMessage(role="system", content=_AI_ANNOTATE_SYSTEM_PROMPT),
                    ChatMessage(role="user", content=_build_annotate_user_prompt(target_schemas)),
                ],
                schema=_ANNOTATE_SCHEMA,
                function_name="annotate_schema",
                parse_fn=lambda d: d.get("tables", []),
                default_value=_build_humanize_data(target_schemas),
                temperature=0.3,
            )
            annotations: list[dict[str, Any]] = annotate_result.value or []
            changes = _apply_annotations(target_schemas, annotations)
            await db.commit()
            return {"ok": True, "message": f"标注完成，更新了 {changes} 个字段", "changes": changes}

        if intent == "update_settings":
            from sqlalchemy.orm.attributes import flag_modified
            from fim_agent.web.api.connectors import _connector_to_response

            updates: dict = intent_data.get("settings_updates") or {}
            _SAFE_SETTINGS = {"read_only", "ssl", "max_rows", "query_timeout"}
            filtered = {k: v for k, v in updates.items() if k in _SAFE_SETTINGS and v is not None}
            if not filtered:
                return {"ok": False, "message": "未识别到需要修改的设置项。", "changes": 0}

            # Clamp numeric fields to their valid ranges
            if "max_rows" in filtered:
                filtered["max_rows"] = max(1, min(10000, int(filtered["max_rows"])))
            if "query_timeout" in filtered:
                filtered["query_timeout"] = max(1, min(300, int(filtered["query_timeout"])))

            current_cfg: dict = dict(connector.db_config or {})
            current_cfg.update(filtered)
            connector.db_config = current_cfg
            flag_modified(connector, "db_config")
            await db.commit()

            # Reload with actions so _connector_to_response can iterate them.
            reloaded = await db.execute(
                select(Connector)
                .options(selectinload(Connector.actions))
                .where(Connector.id == connector_id)
            )
            connector = reloaded.scalar_one()

            parts = []
            labels = {
                "read_only": lambda v: f"只读模式{'开启' if v else '关闭'}",
                "ssl": lambda v: f"SSL {'开启' if v else '关闭'}",
                "max_rows": lambda v: f"最大行数设为 {v}",
                "query_timeout": lambda v: f"查询超时设为 {v} 秒",
            }
            for k, v in filtered.items():
                parts.append(labels[k](v))
            msg = "，".join(parts)
            return {
                "ok": True,
                "message": f"已更新：{msg}",
                "changes": len(filtered),
                "connector": _connector_to_response(connector).model_dump(),
            }

        # intent == "show" or "hide"
        should_hide = intent == "hide"
        changes = 0
        for schema_obj in target_schemas:
            schema_obj.is_visible = not should_hide
            for col in (schema_obj.columns or []):
                col.is_visible = not should_hide
            changes += 1
        await db.commit()
        action = "隐藏" if should_hide else "显示"
        return {"ok": True, "message": f"已{action} {changes} 张表", "changes": changes}

    except Exception as exc:
        logger.exception("db_ai_chat error (connector=%s): %s", connector_id, exc)
        return {"ok": False, "message": str(exc), "changes": 0}


@router.post("/{connector_id}/ai/annotate", response_model=ApiResponse)
async def ai_annotate(
    connector_id: str,
    body: AiAnnotateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    """LLM-powered annotation.

    Single-table (table_ids provided): runs synchronously, returns AiAnnotateResponse.
    Full-annotate (no table_ids): starts background job, returns {"job_id": ...}.
    """
    await _get_db_connector(connector_id, current_user.id, db)

    # ------------------------------------------------------------------
    # Sync path: specific table_ids (single-table annotate, fast)
    # ------------------------------------------------------------------
    if body.table_ids:
        stmt = (
            select(DatabaseSchema)
            .options(selectinload(DatabaseSchema.columns))
            .where(
                DatabaseSchema.connector_id == connector_id,
                DatabaseSchema.id.in_(body.table_ids),
            )
        )
        result = await db.execute(stmt)
        schemas = list(result.scalars().all())

        if not schemas:
            return ApiResponse(data=AiAnnotateResponse(annotated_count=0, preview=[]).model_dump())

        from fim_agent.core.model.structured import structured_llm_call
        from fim_agent.core.model.types import ChatMessage
        from fim_agent.web.deps import get_effective_fast_llm

        llm = await get_effective_fast_llm(db)
        messages = [
            ChatMessage(role="system", content=_AI_ANNOTATE_SYSTEM_PROMPT),
            ChatMessage(role="user", content=_build_annotate_user_prompt(schemas)),
        ]
        sc_result = await structured_llm_call(
            llm,
            messages,
            schema=_ANNOTATE_SCHEMA,
            function_name="annotate_schema",
            parse_fn=lambda d: d.get("tables", []),
            default_value=_build_humanize_data(schemas),
            temperature=0.3,
        )
        annotations: list[dict[str, Any]] = sc_result.value or []
        annotated = _apply_annotations(schemas, annotations)
        await db.commit()

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

        return ApiResponse(data=AiAnnotateResponse(annotated_count=annotated, preview=preview).model_dump())

    # ------------------------------------------------------------------
    # Async path: full annotate — background job
    # ------------------------------------------------------------------
    stmt = select(DatabaseSchema.id).where(DatabaseSchema.connector_id == connector_id)
    if body.table_names:
        stmt = stmt.where(DatabaseSchema.table_name.in_(body.table_names))
    result = await db.execute(stmt)
    schema_ids = [row[0] for row in result.all()]

    if not schema_ids:
        return ApiResponse(data=AiAnnotateResponse(annotated_count=0, preview=[]).model_dump())

    from fim_agent.web.deps import get_effective_fast_llm

    llm = await get_effective_fast_llm(db)

    job_id = str(uuid.uuid4())
    job = _AnnotateJob(job_id=job_id, total_batches=0)
    _annotate_jobs[job_id] = job

    asyncio.create_task(_run_annotate_all_job(job, schema_ids, llm))

    logger.info(
        "Started annotate-all job %s for connector=%s (%d tables)",
        job_id, connector_id, len(schema_ids),
    )
    return ApiResponse(data={"job_id": job_id, "table_count": len(schema_ids)})


@router.get("/{connector_id}/ai/annotate/status/{job_id}", response_model=ApiResponse)
async def ai_annotate_status(
    connector_id: str,
    job_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> ApiResponse:
    """Poll the status of a background annotation job."""
    job = _annotate_jobs.get(job_id)
    if not job:
        raise AppError(404, "Job not found")
    resp = AiAnnotateJobResponse(
        job_id=job.job_id,
        status=job.status,
        completed_batches=job.completed_batches,
        total_batches=job.total_batches,
        annotated_count=job.annotated_count,
        error=job.error,
    )
    return ApiResponse(data=resp.model_dump())

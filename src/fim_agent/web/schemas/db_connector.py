"""Pydantic schemas for database connector endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# -- Connection config --


class DbConnectionConfig(BaseModel):
    """Database connection configuration."""

    host: str = Field(min_length=1, max_length=500)
    port: int = Field(ge=1, le=65535)
    database: str = Field(min_length=1, max_length=200)
    schema_name: str | None = Field(default=None, max_length=200, alias="schema")
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(default="", max_length=1000)
    driver: str = Field(default="postgresql", pattern=r"^(postgresql|mysql|kingbasees|highgo)$")
    ssl: bool = False
    ca_cert: str | None = None
    read_only: bool = True
    max_rows: int = Field(default=1000, ge=1, le=10000)
    query_timeout: int = Field(default=30, ge=1, le=300)

    model_config = {"populate_by_name": True}


# -- Create / Update --


class DbConnectorCreate(BaseModel):
    """Create a database connector."""

    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    icon: str | None = None
    db_config: DbConnectionConfig


# -- Test connection --


class TestConnectionRequest(BaseModel):
    """Request body for ad-hoc connection test (no saved connector needed).

    If ``connector_id`` is provided and the password is the masked
    sentinel ``***``, the real password is looked up from the saved
    connector's encrypted config.
    """

    db_config: DbConnectionConfig
    connector_id: str | None = None


class TestConnectionResponse(BaseModel):
    """Response from a connection test."""

    success: bool
    db_version: str | None = None
    error: str | None = None


# -- Introspection --


class IntrospectResponse(BaseModel):
    """Response from schema introspection."""

    tables_discovered: int
    columns_discovered: int


# -- Schema management --


class SchemaColumnResponse(BaseModel):
    """A single column in a schema table."""

    id: str
    column_name: str
    display_name: str | None
    description: str | None
    data_type: str
    is_nullable: bool
    is_primary_key: bool
    is_visible: bool


class SchemaTableResponse(BaseModel):
    """A database table with its columns."""

    id: str
    table_name: str
    display_name: str | None
    description: str | None
    is_visible: bool
    columns: list[SchemaColumnResponse] = []


class SchemaTableUpdate(BaseModel):
    """Partial update for a table schema annotation."""

    display_name: str | None = None
    description: str | None = None
    is_visible: bool | None = None


class SchemaColumnUpdate(BaseModel):
    """Partial update for a column annotation."""

    display_name: str | None = None
    description: str | None = None
    is_visible: bool | None = None


class BulkSchemaUpdate(BaseModel):
    """Bulk update for multiple table/column annotations."""

    tables: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "List of {table_name, display_name?, description?, is_visible?, "
            "columns?: [{column_name, display_name?, description?, is_visible?}]}"
        ),
    )


# -- Query --


class QueryRequest(BaseModel):
    """Execute a SQL query."""

    sql: str = Field(min_length=1, max_length=50000)


class QueryResponse(BaseModel):
    """Result of a SQL query."""

    columns: list[str] = []
    rows: list[list[Any]] = []
    row_count: int = 0
    truncated: bool = False
    execution_time_ms: float = 0
    error: str | None = None


# -- AI Annotate --


class AiAnnotateRequest(BaseModel):
    """Request LLM-generated annotations for tables/columns.

    Accepts either ``table_names`` or ``table_ids`` to select which tables
    to annotate.  If neither is provided, all visible tables are annotated.
    """

    table_names: list[str] | None = Field(
        default=None,
        description="Filter by table name. None = all visible tables.",
    )
    table_ids: list[str] | None = Field(
        default=None,
        description="Filter by table schema ID. Takes precedence over table_names.",
    )


class AiAnnotateResponse(BaseModel):
    """Result of AI annotation."""

    annotated_count: int
    preview: list[dict[str, Any]] = []


class AiAnnotateJobResponse(BaseModel):
    """Status of a background annotation job."""

    job_id: str
    status: str  # "pending" | "running" | "done" | "error"
    completed_batches: int = 0
    total_batches: int = 0
    annotated_count: int = 0
    error: str | None = None

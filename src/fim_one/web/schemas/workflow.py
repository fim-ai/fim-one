"""Workflow request/response schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Create / Update
# ---------------------------------------------------------------------------


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    icon: str | None = None
    description: str | None = None
    blueprint: dict = Field(
        default_factory=lambda: {"nodes": [], "edges": [], "viewport": {}}
    )
    status: str = "draft"
    is_active: bool = True


class WorkflowUpdate(BaseModel):
    name: str | None = None
    icon: str | None = None
    description: str | None = None
    blueprint: dict | None = None
    status: str | None = None
    is_active: bool | None = None


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class WorkflowResponse(BaseModel):
    id: str
    user_id: str
    name: str
    icon: str | None
    description: str | None
    blueprint: dict
    input_schema: dict | None
    output_schema: dict | None
    status: str
    is_active: bool = True
    visibility: str = "personal"
    org_id: str | None = None
    publish_status: str | None = None
    published_at: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    review_note: str | None = None
    created_at: str
    updated_at: str | None


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


class WorkflowRunRequest(BaseModel):
    """Input payload to execute a workflow."""

    inputs: dict[str, Any] = Field(default_factory=dict)


class NodeRunResult(BaseModel):
    """Result of a single node execution."""

    node_id: str
    node_type: str
    status: str  # completed | failed | skipped
    output: Any = None
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: int | None = None


class WorkflowRunResponse(BaseModel):
    id: str
    workflow_id: str
    user_id: str
    status: str
    inputs: dict | None
    outputs: dict | None
    node_results: dict | None
    started_at: str | None
    completed_at: str | None
    duration_ms: int | None
    error: str | None
    created_at: str
    updated_at: str | None


# ---------------------------------------------------------------------------
# Export / Import
# ---------------------------------------------------------------------------


class WorkflowExportData(BaseModel):
    """Portable workflow representation for export."""

    name: str
    icon: str | None = None
    description: str | None = None
    blueprint: dict
    input_schema: dict | None = None
    output_schema: dict | None = None
    version: str = "1.0"


class WorkflowImportRequest(BaseModel):
    """Request body for importing a workflow."""

    data: WorkflowExportData


# ---------------------------------------------------------------------------
# Env vars
# ---------------------------------------------------------------------------


class WorkflowEnvVarsUpdate(BaseModel):
    """Encrypted env vars key-value pairs."""

    env_vars: dict[str, str] = Field(default_factory=dict)

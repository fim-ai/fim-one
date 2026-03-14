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
    webhook_url: str | None = None


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
    webhook_url: str | None = None
    created_at: str
    updated_at: str | None


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


class WorkflowRunRequest(BaseModel):
    """Input payload to execute a workflow."""

    inputs: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = Field(
        default=False,
        description="When true, validate and return the execution plan without running.",
    )


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
# Validate / Dry Run
# ---------------------------------------------------------------------------


class BlueprintWarningItem(BaseModel):
    """A single non-fatal validation warning."""

    node_id: str | None = None
    code: str
    message: str


class WorkflowValidateResponse(BaseModel):
    """Structural validation result for a workflow blueprint."""

    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[BlueprintWarningItem] = Field(default_factory=list)
    node_count: int = 0
    edge_count: int = 0
    topology_order: list[str] = Field(default_factory=list)


class DryRunNodePlan(BaseModel):
    """Planned execution info for a single node in dry-run mode."""

    node_id: str
    node_type: str
    position: int  # 0-based index in the execution order
    has_warnings: bool = False


class WorkflowDryRunResponse(BaseModel):
    """Dry-run result: execution plan without actual execution."""

    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[BlueprintWarningItem] = Field(default_factory=list)
    node_count: int = 0
    edge_count: int = 0
    topology_order: list[str] = Field(default_factory=list)
    execution_plan: list[DryRunNodePlan] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------


class WorkflowVersionResponse(BaseModel):
    id: str
    workflow_id: str
    version_number: int
    blueprint: dict
    input_schema: dict | None
    output_schema: dict | None
    change_summary: str | None
    created_by: str | None
    created_at: str


# ---------------------------------------------------------------------------
# Export / Import
# ---------------------------------------------------------------------------


class WorkflowExportData(BaseModel):
    """Portable workflow representation for export (inner payload)."""

    name: str
    icon: str | None = None
    description: str | None = None
    blueprint: dict
    input_schema: dict | None = None
    output_schema: dict | None = None


class WorkflowExportFile(BaseModel):
    """Top-level envelope for exported workflow files."""

    format: str = "fim_workflow_v1"
    exported_at: str
    workflow: WorkflowExportData


class WorkflowImportRequest(BaseModel):
    """Request body for importing a workflow (legacy wrapper)."""

    data: WorkflowExportData


class WorkflowImportFileRequest(BaseModel):
    """Request body matching the exported file envelope.

    Accepts ``{ "format": "fim_workflow_v1", "exported_at": ..., "workflow": {...} }``
    as well as the legacy ``{ "data": {...} }`` shape.
    """

    format: str | None = None
    exported_at: str | None = None
    workflow: WorkflowExportData | None = None
    # Legacy fallback
    data: WorkflowExportData | None = None


# ---------------------------------------------------------------------------
# Env vars
# ---------------------------------------------------------------------------


class WorkflowEnvVarsUpdate(BaseModel):
    """Encrypted env vars key-value pairs."""

    env_vars: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Duplicate / Templates
# ---------------------------------------------------------------------------


class WorkflowFromTemplateRequest(BaseModel):
    """Request body for creating a workflow from a built-in template."""

    template_id: str = Field(min_length=1)
    name: str | None = None


class WorkflowTemplateResponse(BaseModel):
    """A built-in workflow template descriptor."""

    id: str
    name: str
    description: str
    icon: str
    category: str
    blueprint: dict


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


class RunsPerDay(BaseModel):
    """Aggregated run counts for a single calendar day."""

    date: str
    count: int
    completed: int = 0
    failed: int = 0


class MostFailedNode(BaseModel):
    """A node that has failed across workflow runs."""

    node_id: str
    failure_count: int
    total_runs: int


class WorkflowAnalyticsResponse(BaseModel):
    """Comprehensive workflow execution analytics."""

    total_runs: int
    status_distribution: dict[str, int]
    success_rate: float | None = None
    avg_duration_ms: int | None = None
    p50_duration_ms: int | None = None
    p95_duration_ms: int | None = None
    p99_duration_ms: int | None = None
    runs_per_day: list[RunsPerDay] = Field(default_factory=list)
    most_failed_nodes: list[MostFailedNode] = Field(default_factory=list)
    avg_nodes_per_run: float | None = None

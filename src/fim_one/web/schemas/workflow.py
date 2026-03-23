"""Workflow request/response schemas."""

from __future__ import annotations

import re
import datetime as _dt
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Create / Update
# ---------------------------------------------------------------------------


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    icon: str | None = None
    description: str | None = None
    blueprint: dict[str, Any] = Field(
        default_factory=lambda: {"nodes": [], "edges": [], "viewport": {}}
    )
    status: str = "draft"
    is_active: bool = True


class WorkflowUpdate(BaseModel):
    name: str | None = None
    icon: str | None = None
    description: str | None = None
    blueprint: dict[str, Any] | None = None
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
    blueprint: dict[str, Any]
    input_schema: dict[str, Any] | None
    output_schema: dict[str, Any] | None
    status: str
    is_active: bool = True
    visibility: str = "personal"
    org_id: str | None = None
    change_summary: str | None = None
    publish_status: str | None = None
    published_at: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    review_note: str | None = None
    webhook_url: str | None = None
    schedule_cron: str | None = None
    schedule_enabled: bool = False
    schedule_inputs: dict[str, Any] | None = None
    schedule_timezone: str = "UTC"
    has_api_key: bool = False
    total_runs: int = 0
    last_run_at: str | None = None
    success_rate: float | None = None
    source: str | None = None
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
    inputs: dict[str, Any] | None
    outputs: dict[str, Any] | None
    node_results: dict[str, Any] | None
    started_at: str | None
    completed_at: str | None
    duration_ms: int | None
    error: str | None
    created_at: str
    updated_at: str | None


# ---------------------------------------------------------------------------
# Batch Run
# ---------------------------------------------------------------------------


class WorkflowBatchRunRequest(BaseModel):
    """Input payload to execute a workflow with multiple input sets."""

    inputs: list[dict[str, Any]] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="List of input sets to run the workflow with (1-100 items).",
    )
    max_parallel: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum number of concurrent workflow executions (1-10).",
    )


class BatchRunResultItem(BaseModel):
    """Result of a single workflow execution within a batch."""

    run_id: str
    inputs: dict[str, Any]
    status: str  # completed | failed | cancelled
    outputs: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int | None = None


class WorkflowBatchRunResponse(BaseModel):
    """Response for a batch workflow execution."""

    batch_id: str
    total: int
    results: list[BatchRunResultItem] = Field(default_factory=list)


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
    blueprint: dict[str, Any]
    input_schema: dict[str, Any] | None
    output_schema: dict[str, Any] | None
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
    blueprint: dict[str, Any]
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


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


class UnresolvedReferenceItem(BaseModel):
    """A single unresolved external resource reference in an imported blueprint."""

    node_id: str
    node_type: str
    field_name: str
    referenced_id: str
    resource_type: str


class WorkflowImportResponse(BaseModel):
    """Response for workflow import — includes the created workflow plus any
    unresolved reference warnings."""

    workflow: WorkflowResponse
    unresolved_references: list[UnresolvedReferenceItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


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
    """Request body for creating a workflow from a template (built-in or DB-stored)."""

    template_id: str = Field(min_length=1)
    name: str | None = None


class WorkflowTemplateResponse(BaseModel):
    """A workflow template descriptor (built-in or DB-stored)."""

    id: str
    name: str
    description: str
    icon: str
    category: str
    blueprint: dict[str, Any]
    created_at: str | None = None


class WorkflowTemplateCreate(BaseModel):
    """Request body for creating a DB-stored workflow template (admin only)."""

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    icon: str = "🔄"
    category: str = Field(min_length=1, max_length=100)
    blueprint: dict[str, Any]
    is_active: bool = True
    sort_order: int = 0


class WorkflowTemplateUpdate(BaseModel):
    """Request body for updating a DB-stored workflow template (admin only)."""

    name: str | None = None
    description: str | None = None
    icon: str | None = None
    category: str | None = None
    blueprint: dict[str, Any] | None = None
    is_active: bool | None = None
    sort_order: int | None = None


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


# ---------------------------------------------------------------------------
# Schedule (cron-based trigger)
# ---------------------------------------------------------------------------

# Allowed ranges for 5-field cron expressions (min hour dom month dow)
_CRON_FIELD_RANGES = [
    (0, 59),   # minute
    (0, 23),   # hour
    (1, 31),   # day of month
    (1, 12),   # month
    (0, 7),    # day of week (0 and 7 both = Sunday)
]

# Matches a single cron token: number, range (1-5), step (*/2, 1-5/2), or list (1,3,5)
# Also allows named days/months (MON-FRI, JAN-DEC)
_CRON_TOKEN_RE = re.compile(
    r"^(?:\*|[a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)?)(?:/\d+)?$"
)


def _validate_cron(expr: str) -> str | None:
    """Validate a 5-field cron expression.

    Returns None if valid, or an error message string if invalid.
    Supports: ``*``, ranges (``1-5``), steps (``*/2``), lists (``1,3,5``),
    and named days/months (``MON-FRI``, ``JAN``).
    """
    parts = expr.strip().split()
    if len(parts) != 5:
        return f"Cron expression must have exactly 5 fields, got {len(parts)}"

    for i, part in enumerate(parts):
        # A field can be a comma-separated list of tokens
        tokens = part.split(",")
        for token in tokens:
            if not token:
                return f"Empty token in field {i + 1}"
            if not _CRON_TOKEN_RE.match(token):
                return f"Invalid token '{token}' in field {i + 1}"

    return None


def _compute_next_run(cron_expr: str, tz_name: str = "UTC") -> str | None:
    """Compute the next scheduled run time from a cron expression.

    Uses ``croniter`` if available; otherwise returns ``None``.
    """
    try:
        import importlib

        _croniter_mod = importlib.import_module("croniter")
        croniter = _croniter_mod.croniter
    except (ImportError, AttributeError):
        return None

    try:
        import zoneinfo

        tz: _dt.tzinfo = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc

    now = datetime.now(tz=tz)
    try:
        it = croniter(cron_expr, now)
        next_dt: datetime = it.get_next(datetime)
        return next_dt.isoformat()
    except Exception:
        return None


class WorkflowScheduleUpdate(BaseModel):
    """Request body to set or update a workflow's scheduled trigger."""

    cron: str | None = Field(
        None,
        max_length=100,
        description="5-field cron expression (min hour dom month dow).",
        examples=["0 9 * * MON-FRI", "*/15 * * * *"],
    )
    enabled: bool = False
    inputs: dict[str, Any] | None = None
    timezone: str = Field(
        default="UTC",
        max_length=50,
        description="IANA timezone name for schedule evaluation.",
    )

    @field_validator("cron")
    @classmethod
    def validate_cron_format(cls, v: str | None) -> str | None:
        if v is None:
            return v
        err = _validate_cron(v)
        if err:
            raise ValueError(err)
        return v.strip()


class WorkflowScheduleResponse(BaseModel):
    """Current schedule configuration for a workflow."""

    schedule_cron: str | None = None
    schedule_enabled: bool = False
    schedule_inputs: dict[str, Any] | None = None
    schedule_timezone: str = "UTC"
    next_run_at: str | None = None


# ---------------------------------------------------------------------------
# Public trigger (API key)
# ---------------------------------------------------------------------------


class WorkflowTriggerRequest(BaseModel):
    """Input payload for the public trigger endpoint."""

    inputs: dict[str, Any] = Field(default_factory=dict)


class WorkflowTriggerResponse(BaseModel):
    """Response from the public trigger endpoint."""

    run_id: str
    status: str
    outputs: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int | None = None


class WorkflowApiKeyResponse(BaseModel):
    """Response after generating an API key (shown once)."""

    api_key: str
    workflow_id: str


# ---------------------------------------------------------------------------
# Test Node (single-node isolated execution)
# ---------------------------------------------------------------------------


class NodeTestRequest(BaseModel):
    """Request body for testing a single workflow node in isolation."""

    node_id: str = Field(min_length=1, description="ID of the node to test")
    variables: dict[str, Any] = Field(
        default_factory=dict,
        description="Mock variable values to populate the variable store",
    )
    env_vars: dict[str, str] = Field(
        default_factory=dict,
        description="Optional environment variable overrides for this test run",
    )


class NodeTestResponse(BaseModel):
    """Result of a single-node test execution."""

    node_id: str
    node_type: str
    status: str  # completed | failed
    output: Any = None
    error: str | None = None
    duration_ms: int
    variables_after: dict[str, Any] = Field(
        default_factory=dict,
        description="Variable store snapshot after execution",
    )


# ---------------------------------------------------------------------------
# Admin batch operations
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


class WorkflowApprovalResponse(BaseModel):
    """Response schema for a workflow approval record."""

    id: str
    workflow_run_id: str
    node_id: str
    title: str
    description: str | None
    status: str  # pending, approved, rejected, expired
    assignee: str | None
    decision_by: str | None
    decision_note: str | None
    timeout_hours: float
    created_at: str
    resolved_at: str | None
    updated_at: str | None


class ApprovalDecisionRequest(BaseModel):
    """Request body for approving or rejecting a workflow approval."""

    note: str | None = None


# ---------------------------------------------------------------------------
# Admin batch operations
# ---------------------------------------------------------------------------


class BatchWorkflowDeleteRequest(BaseModel):
    """Request body for batch-deleting workflows."""

    workflow_ids: list[str] = Field(..., min_length=1, max_length=100)


class BatchWorkflowToggleRequest(BaseModel):
    """Request body for batch-toggling workflow is_active state."""

    workflow_ids: list[str] = Field(..., min_length=1, max_length=100)
    is_active: bool


class BatchWorkflowPublishRequest(BaseModel):
    """Request body for batch-publishing/unpublishing workflows."""

    workflow_ids: list[str] = Field(..., min_length=1, max_length=100)
    status: str  # "active" or "draft"


class BatchOperationResponse(BaseModel):
    """Response for batch operations."""

    count: int
    message: str

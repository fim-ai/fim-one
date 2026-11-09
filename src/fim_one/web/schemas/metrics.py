"""Response schemas for the admin metrics/analytics API."""

from __future__ import annotations

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# GET /api/metrics/overview
# ---------------------------------------------------------------------------


class ConversationMetrics(BaseModel):
    total: int
    active_today: int


class WorkflowRunMetrics(BaseModel):
    total: int
    success_rate: float
    avg_duration_ms: float


class ConnectorCallMetrics(BaseModel):
    total: int
    failure_rate: float


class TokenUsageMetrics(BaseModel):
    total_input: int
    total_output: int


class MetricsOverview(BaseModel):
    conversations: ConversationMetrics
    workflow_runs: WorkflowRunMetrics
    connector_calls: ConnectorCallMetrics
    token_usage: TokenUsageMetrics
    active_users: int


# ---------------------------------------------------------------------------
# GET /api/metrics/token-usage
# ---------------------------------------------------------------------------


class DailyTokenUsage(BaseModel):
    date: str
    input_tokens: int
    output_tokens: int
    model: str


class ModelTokenBreakdown(BaseModel):
    input: int
    output: int


class TokenUsageTotals(BaseModel):
    input: int
    output: int


class TokenUsageResponse(BaseModel):
    daily: list[DailyTokenUsage]
    by_model: dict[str, ModelTokenBreakdown]
    total: TokenUsageTotals


# ---------------------------------------------------------------------------
# GET /api/metrics/connector-usage
# ---------------------------------------------------------------------------


class ConnectorUsageItem(BaseModel):
    # What was called: "connector", "database" or "mcp". For "mcp",
    # ``connector_id`` and ``connector_name`` are the MCP server's.
    kind: str = "connector"
    connector_id: str
    connector_name: str
    total_calls: int
    success_rate: float
    avg_latency_ms: float
    last_called_at: str | None


class ConnectorUsageResponse(BaseModel):
    connectors: list[ConnectorUsageItem]


# ---------------------------------------------------------------------------
# GET /api/metrics/workflow-performance
# ---------------------------------------------------------------------------


class WorkflowPerformanceItem(BaseModel):
    workflow_id: str
    name: str
    total_runs: int
    success_rate: float
    avg_duration_ms: float
    p95_duration_ms: float


class WorkflowPerformanceResponse(BaseModel):
    workflows: list[WorkflowPerformanceItem]

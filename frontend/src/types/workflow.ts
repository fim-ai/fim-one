// --- Core workflow types ---

export interface WorkflowResponse {
  id: string
  user_id: string
  name: string
  icon: string | null
  description: string | null
  blueprint: WorkflowBlueprint
  input_schema: Record<string, unknown> | null
  output_schema: Record<string, unknown> | null
  status: "draft" | "active"
  is_active: boolean
  visibility: string
  org_id?: string | null
  publish_status: string | null
  published_at: string | null
  reviewed_by: string | null
  reviewed_at: string | null
  review_note: string | null
  webhook_url: string | null
  schedule_cron?: string | null
  schedule_enabled?: boolean
  schedule_inputs?: Record<string, unknown> | null
  schedule_timezone?: string
  has_api_key: boolean
  forked_from: string | null
  total_runs: number
  last_run_at: string | null
  success_rate: number | null
  created_at: string
  updated_at: string
}

export interface WorkflowVariable {
  name: string
  type: "string" | "number" | "boolean" | "json"
  default_value: string
  description: string
}

export interface WorkflowBlueprint {
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  viewport: { x: number; y: number; zoom: number }
  variables?: WorkflowVariable[]
}

export type ErrorStrategy = "stop_workflow" | "continue" | "fail_branch"

export interface WorkflowNode {
  id: string
  type: WorkflowNodeType
  position: { x: number; y: number }
  data: Record<string, unknown>
  error_strategy?: ErrorStrategy
  timeout_ms?: number
}

export interface WorkflowEdge {
  id: string
  source: string
  target: string
  sourceHandle?: string
  targetHandle?: string
}

export type WorkflowNodeType =
  | "start"
  | "end"
  | "llm"
  | "conditionBranch"
  | "agent"
  | "knowledgeRetrieval"
  | "connector"
  | "humanIntervention"
  | "mcp"

// --- Per-node data interfaces ---

export interface StartNodeData {
  variables: Array<{
    name: string
    type: string
    default_value?: string
    required?: boolean
  }>
}

export interface EndNodeData {
  output_mapping: Record<string, string>
}

export interface LLMNodeData {
  system_prompt?: string
  prompt_template: string
  output_variable: string
  temperature?: number
  max_tokens?: number
}

export interface ConditionNodeData {
  mode: "expression" | "llm"
  conditions: Array<{
    id: string
    label: string
    expression?: string
    llm_prompt?: string
  }>
}

export interface AgentNodeData {
  agent_id: string
  prompt_template?: string
  output_variable: string
}

export interface KnowledgeRetrievalNodeData {
  kb_id: string
  query_template: string
  top_k?: number
  output_variable: string
}

export interface ConnectorNodeData {
  connector_id: string
  action: string
  parameters: Record<string, string>
  output_variable: string
}

export interface HumanInterventionNodeData {
  prompt_message: string
  assignee: string
  timeout_hours: number
  output_variable: string
}

export interface MCPNodeData {
  server_id: string
  tool_name: string
  parameters: Record<string, unknown>
  output_variable: string
}

// --- Validation types ---

export interface BlueprintWarningItem {
  node_id: string | null
  code: string
  message: string
}

export interface WorkflowValidateResponse {
  valid: boolean
  errors: string[]
  warnings: BlueprintWarningItem[]
  node_count: number
  edge_count: number
  topology_order: string[]
}

// --- Import result types ---

export interface UnresolvedReference {
  node_id: string
  node_type: string
  field_name: string
  referenced_id: string
  resource_type: string
}

export interface WorkflowImportResult {
  workflow: WorkflowResponse
  unresolved_references: UnresolvedReference[]
  warnings: string[]
}

// --- Create / Update payloads ---

export interface WorkflowCreate {
  name: string
  icon?: string | null
  description?: string | null
  blueprint?: WorkflowBlueprint
}

export interface WorkflowUpdate {
  name?: string
  icon?: string | null
  description?: string | null
  blueprint?: WorkflowBlueprint
  input_schema?: Record<string, unknown> | null
  output_schema?: Record<string, unknown> | null
  status?: "draft" | "active"
  webhook_url?: string | null
}

// --- Run types ---

export interface WorkflowRunResponse {
  id: string
  workflow_id: string
  status: "pending" | "running" | "completed" | "failed" | "cancelled"
  inputs: Record<string, unknown> | null
  outputs: Record<string, unknown> | null
  node_results: Record<string, NodeRunResult> | null
  started_at: string | null
  completed_at: string | null
  duration_ms: number | null
  error: string | null
  created_at: string
}

export interface NodeRunResult {
  node_id?: string
  node_type?: string
  status: "pending" | "running" | "completed" | "failed" | "skipped" | "retrying"
  output: unknown
  error: string | null
  started_at: string | null
  completed_at: string | null
  duration_ms: number | null
  input_preview?: unknown
  retryAttempt?: number
  maxRetries?: number
  trace?: {
    variable_snapshot?: Record<string, unknown> | null
    input_snapshot?: Record<string, unknown> | null
    [key: string]: unknown
  }
}

/** Overlay data injected into node.data during workflow runs for canvas display */
export interface NodeRunOverlayData {
  durationMs: number | null
  inputPreview: string | null
  outputPreview: string | null
  runError: string | null
}

// --- Node run status for canvas overlay ---

export type NodeRunStatus = "pending" | "running" | "completed" | "failed" | "skipped" | "retrying"

// --- Execution log event for run panel ---

export type WorkflowLogEventType =
  | "node_started"
  | "node_completed"
  | "node_failed"
  | "node_skipped"
  | "node_retrying"
  | "run_completed"
  | "run_failed"

export interface WorkflowLogEvent {
  timestamp: number
  eventType: WorkflowLogEventType
  nodeId: string | null
  details: Record<string, unknown>
}

// --- Analytics (detailed) ---

export interface RunsPerDayEntry {
  date: string
  count: number
  completed: number
  failed: number
}

export interface MostFailedNodeEntry {
  node_id: string
  failure_count: number
  total_runs: number
}

export interface WorkflowAnalyticsResponse {
  total_runs: number
  status_distribution: Record<string, number>
  success_rate: number
  avg_duration_ms: number
  p50_duration_ms: number
  p95_duration_ms: number
  p99_duration_ms: number
  runs_per_day: RunsPerDayEntry[]
  most_failed_nodes: MostFailedNodeEntry[]
  avg_nodes_per_run: number
}

// --- Stats ---

export interface WorkflowStats {
  total_runs: number
  completed: number
  failed: number
  cancelled: number
  success_rate: number | null
  avg_duration_ms: number | null
  last_run_at: string | null
}

// --- Per-Node Stats (from /node-stats endpoint) ---

export interface NodeStatEntry {
  node_id: string
  total_runs: number
  completed: number
  failed: number
  skipped: number
  avg_duration_ms: number | null
  min_duration_ms: number | null
  max_duration_ms: number | null
  success_rate: number | null
}

export interface NodeStatsResponse {
  runs_analyzed: number
  nodes: NodeStatEntry[]
}

// --- Templates ---

export interface WorkflowTemplate {
  id: string
  name: string
  description: string
  icon: string
  category: string
  blueprint: WorkflowBlueprint
}

export interface WorkflowFromTemplateRequest {
  template_id: string
  name?: string
}

// --- Batch Run types ---

export interface BatchRunResultItem {
  run_id: string
  inputs: Record<string, unknown>
  status: "completed" | "failed"
  outputs: Record<string, unknown> | null
  error: string | null
  duration_ms: number | null
}

export interface WorkflowBatchRunResponse {
  batch_id: string
  total: number
  results: BatchRunResultItem[]
}

// --- Schedule types ---

export interface WorkflowScheduleResponse {
  cron: string
  enabled: boolean
  inputs: Record<string, unknown> | null
  timezone: string
  next_run_at: string | null
}

export interface WorkflowScheduleUpdate {
  cron: string
  enabled: boolean
  inputs?: Record<string, unknown> | null
  timezone?: string
}

// --- Test Node types ---

export interface NodeTestRequest {
  node_id: string
  variables: Record<string, unknown>
  env_vars?: Record<string, string>
}

export interface NodeTestResponse {
  node_id: string
  node_type: string
  status: "completed" | "failed"
  output: unknown
  error: string | null
  duration_ms: number
  variables_after: Record<string, unknown>
}

// --- Per-node validation state injected into node data for canvas overlay ---

export interface NodeValidationState {
  errors: string[]
  warnings: string[]
}

// --- Version types ---

export interface WorkflowVersionResponse {
  id: string
  workflow_id: string
  version_number: number
  blueprint: WorkflowBlueprint
  input_schema: Record<string, unknown> | null
  output_schema: Record<string, unknown> | null
  change_summary: string | null
  created_by: string | null
  created_at: string
}

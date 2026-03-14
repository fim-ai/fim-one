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
  | "questionClassifier"
  | "agent"
  | "knowledgeRetrieval"
  | "connector"
  | "httpRequest"
  | "variableAssign"
  | "templateTransform"
  | "codeExecution"
  | "iterator"
  | "loop"
  | "variableAggregator"
  | "parameterExtractor"
  | "listOperation"
  | "transform"
  | "documentExtractor"
  | "questionUnderstanding"
  | "humanIntervention"
  | "mcp"
  | "builtinTool"
  | "subWorkflow"
  | "env"

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
  model?: string
  model_tier?: "fast" | "main"
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

export interface QuestionClassifierNodeData {
  model?: string
  prompt?: string
  classes: Array<{
    id: string
    label: string
    description?: string
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

export interface HTTPRequestNodeData {
  method: string
  url: string
  headers?: Record<string, string>
  body?: string
  output_variable: string
}

export interface VariableAssignNodeData {
  assignments: Array<{
    variable: string
    expression: string
  }>
}

export interface TemplateTransformNodeData {
  template: string
  output_variable: string
}

export interface CodeExecutionNodeData {
  language: "python" | "javascript"
  code: string
  output_variable: string
}

export interface IteratorNodeData {
  list_variable: string
  iterator_variable: string
  index_variable: string
  max_iterations: number
}

export interface LoopNodeData {
  condition: string
  max_iterations: number
  loop_variable: string
}

export interface VariableAggregatorNodeData {
  variables: string[]
  mode: "list" | "concat" | "merge" | "first_non_empty"
  separator: string
}

export interface ListOperationNodeData {
  input_variable: string
  operation: "filter" | "map" | "sort" | "slice" | "flatten" | "unique" | "reverse" | "length"
  expression: string
  slice_start?: number
  slice_end?: number
  output_variable: string
}

export interface TransformNodeData {
  input_variable: string
  operations: Array<{
    type: "json_path" | "type_cast" | "format" | "regex_extract" | "string_op" | "math_op"
    config: Record<string, unknown>
  }>
  output_variable: string
}

export interface DocumentExtractorNodeData {
  input_variable: string
  input_type: "text" | "base64" | "url"
  extract_mode: "full_text" | "pages" | "metadata" | "tables"
  page_range?: string
  output_variable: string
}

export interface QuestionUnderstandingNodeData {
  input_variable: string
  mode: "rewrite" | "expand" | "classify" | "decompose"
  system_prompt?: string
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

export interface BuiltinToolNodeData {
  tool_id: string
  parameters: Record<string, unknown>
  output_variable: string
}

export interface ParameterExtractorNodeData {
  input_text: string
  parameters: Array<{
    name: string
    type: string
    description: string
    required?: boolean
  }>
  extraction_prompt?: string
}

export interface SubWorkflowNodeData {
  workflow_id: string
  input_mapping: Record<string, string>
  output_variable: string
}

export interface ENVNodeData {
  env_keys: string[]
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
  status: "pending" | "running" | "completed" | "failed" | "skipped" | "retrying"
  output: unknown
  error: string | null
  started_at: string | null
  completed_at: string | null
  duration_ms: number | null
  input_preview?: unknown
  retryAttempt?: number
  maxRetries?: number
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

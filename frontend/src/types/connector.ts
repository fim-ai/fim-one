export interface ConnectorActionResponse {
  id: string
  connector_id: string
  name: string
  description: string | null
  method: string // GET | POST | PUT | DELETE
  path: string
  parameters_schema: Record<string, unknown> | null
  request_body_template: Record<string, unknown> | null
  response_extract: string | null
  requires_confirmation: boolean
  created_at: string
  updated_at: string | null
}

export interface ConnectorActionCreate {
  name: string
  description?: string | null
  method?: string
  path: string
  parameters_schema?: Record<string, unknown> | null
  request_body_template?: Record<string, unknown> | null
  response_extract?: string | null
  requires_confirmation?: boolean
}

export interface ConnectorActionUpdate {
  name?: string
  description?: string | null
  method?: string
  path?: string
  parameters_schema?: Record<string, unknown> | null
  request_body_template?: Record<string, unknown> | null
  response_extract?: string | null
  requires_confirmation?: boolean
}

// Database connection config
export interface DbConnectionConfig {
  driver: string // "postgresql" | "mysql" | "oracle" | "sqlserver" | "dm8" | "kingbasees" | "gbase" | "highgo"
  host: string
  port: number
  database: string
  schema?: string // PG/Oracle only
  username: string
  password?: string // Only in create/update, masked in response
  encrypted_password?: string // In response, always masked
  ssl: boolean
  ca_cert?: string
  read_only: boolean
  max_rows: number
  query_timeout: number
}

export interface ConnectorResponse {
  id: string
  name: string
  description: string | null
  icon: string | null
  type: string // "api" | "database"
  base_url: string
  auth_type: string // "api_key" | "bearer" | "oauth2" | "basic" | "none"
  auth_config: Record<string, unknown> | null
  db_config?: DbConnectionConfig | null // Present when type="database"
  is_official: boolean
  forked_from: string | null
  version: number
  actions: ConnectorActionResponse[]
  created_at: string
  updated_at: string | null
}

export interface ConnectorCreate {
  name: string
  description?: string | null
  icon?: string | null
  type?: string
  base_url: string
  auth_type?: string
  auth_config?: Record<string, unknown> | null
}

export interface ConnectorUpdate {
  name?: string
  description?: string | null
  icon?: string | null
  type?: string
  base_url?: string
  auth_type?: string
  auth_config?: Record<string, unknown> | null
  db_config?: DbConnectionConfig | null
}

export interface OpenAPIImportRequest {
  spec?: Record<string, unknown>
  spec_url?: string
  spec_raw?: string
  replace_existing?: boolean
}

export interface AIGenerateActionsRequest {
  instruction: string
  context?: string
}

export interface AIRefineActionRequest {
  instruction: string
  action_id?: string
}

export interface AIActionResult {
  created: ConnectorActionResponse[]
  updated: ConnectorActionResponse[]
  deleted: string[]
  connector_updated: ConnectorResponse | null
  message: string
  message_key?: string
  message_args?: Record<string, unknown>
}

export interface AICreateConnectorResult {
  connector: ConnectorResponse
  message: string
  message_key?: string
  message_args?: Record<string, unknown>
}

// Database connector create
export interface DbConnectorCreate {
  name: string
  description?: string | null
  icon?: string | null
  type: "database"
  db_config: DbConnectionConfig
}

// Schema types
export interface SchemaTable {
  id: string
  connector_id: string
  table_name: string
  display_name: string | null
  description: string | null
  is_visible: boolean
  columns: SchemaColumn[]
  created_at: string
  updated_at: string | null
}

export interface SchemaColumn {
  id: string
  schema_id: string
  column_name: string
  display_name: string | null
  description: string | null
  data_type: string
  is_nullable: boolean
  is_primary_key: boolean
  is_visible: boolean
}

export interface SchemaTableUpdate {
  display_name?: string
  description?: string
  is_visible?: boolean
}

export interface SchemaColumnUpdate {
  display_name?: string
  description?: string
  is_visible?: boolean
}

// Test connection
export interface TestConnectionResponse {
  success: boolean
  db_version: string | null
  error: string | null
}

// Introspect response
export interface IntrospectResponse {
  tables_discovered: number
  columns_discovered: number
}

// Query
export interface QueryRequest {
  sql: string
}

export interface QueryResponse {
  columns: string[]
  rows: unknown[][]
  row_count: number
  truncated: boolean
  execution_time_ms: number
  error: string | null
}

// AI annotate
export interface AIAnnotateResponse {
  annotated_count: number
  preview: Record<string, unknown>[]
}

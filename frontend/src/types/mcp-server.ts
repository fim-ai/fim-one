export interface MCPServerResponse {
  id: string
  user_id: string
  name: string
  description: string | null
  transport: "stdio" | "sse" | "streamable_http"
  command: string | null
  args: string[] | null
  env: Record<string, string> | null
  url: string | null
  working_dir: string | null
  headers: Record<string, string> | null
  is_active: boolean
  tool_count: number
  publish_status: string | null
  reviewed_by: string | null
  reviewed_at: string | null
  review_note: string | null
  allow_fallback: boolean
  my_has_credentials: boolean
  visibility?: string
  org_id?: string | null
  created_at: string
  updated_at: string | null
}

export interface MCPMyCredentialStatus {
  has_credentials: boolean
  env_keys: string[]
  env: Record<string, string>
}

export interface MCPServerCreate {
  name: string
  description?: string | null
  transport: "stdio" | "sse" | "streamable_http"
  command?: string | null
  args?: string[] | null
  env?: Record<string, string> | null
  url?: string | null
  working_dir?: string | null
  headers?: Record<string, string> | null
  is_active?: boolean
  allow_fallback?: boolean
}

export interface MCPServerUpdate {
  name?: string
  description?: string | null
  transport?: "stdio" | "sse" | "streamable_http"
  command?: string | null
  args?: string[] | null
  env?: Record<string, string> | null
  url?: string | null
  working_dir?: string | null
  headers?: Record<string, string> | null
  is_active?: boolean
  allow_fallback?: boolean
}

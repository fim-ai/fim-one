export interface AdminUser {
  id: string
  username: string | null
  display_name: string | null
  email: string | null
  is_admin: boolean
  is_active: boolean
  created_at: string
  has_active_session: boolean
  monthly_tokens: number
  token_quota: number | null
}

export interface IntegrationHealth {
  key: string
  label: string
  configured: boolean
  detail: string | null
  impact: string | null
  level: "required" | "recommended" | "optional"
}

export interface AdminConversation {
  id: string
  title: string | null
  mode: string | null
  model_name: string | null
  total_tokens: number
  message_count: number
  user_id: string
  username: string | null
  email?: string | null
  created_at: string
}

export interface AdminMessage {
  id: string
  role: string
  content: string | null
  created_at: string
}

export interface UserStorageStat {
  user_id: string
  username: string | null
  email?: string | null
  file_count: number
  total_bytes: number
}

export interface StorageStats {
  total_bytes: number
  users: UserStorageStat[]
}

export interface InviteCode {
  id: string
  code: string
  note: string | null
  max_uses: number
  use_count: number
  expires_at: string | null
  is_active: boolean
  created_at: string
}

export interface AdminMCPServer {
  id: string
  name: string
  description: string | null
  transport: string
  command: string | null
  args: string[] | null
  url: string | null
  is_active: boolean
  is_global: boolean
  tool_count: number
  cloned_from_server_id: string | null
  cloned_from_user_id: string | null
  cloned_from_username: string | null
  created_at: string
}

export interface EnvFallbackInfo {
  llm_model: string
  llm_base_url: string
  llm_temperature: number
  llm_context_size: number
  llm_max_output_tokens: number
  fast_llm_model: string
  fast_llm_context_size: number
  fast_llm_max_output_tokens: number
  has_api_key: boolean
}

export interface AdminModelsResponse {
  models: import("@/types/model_config").ModelConfigResponse[]
  env_fallback: EnvFallbackInfo
}

export interface AdminModelCreate {
  name: string
  provider: string
  model_name: string
  base_url?: string | null
  api_key?: string | null
  category?: string
  temperature?: number | null
  max_output_tokens?: number | null
  context_size?: number | null
  role?: string | null
  is_active?: boolean
}

export type AdminModelUpdate = Partial<AdminModelCreate>

export interface AdminUserFile {
  file_id: string
  filename: string
  size: number
  mime_type: string
  stored_name: string
}

export interface AdminGlobalAgentInfo {
  id: string
  name: string
  icon: string | null
  description: string | null
  instructions: string | null
  execution_mode: string
  status: string
  is_global: boolean
  is_active: boolean
  user_id: string | null
  username: string | null
  email: string | null
  model_name: string | null
  model_config_json: Record<string, unknown> | null
  tools: string | null
  tool_categories: string[] | null
  suggested_prompts: string[] | null
  sandbox_config: Record<string, unknown> | null
  kb_ids: string | null
  enable_planning: boolean
  cloned_from_agent_id: string | null
  cloned_from_user_id: string | null
  cloned_from_username: string | null
  created_at: string
}

export interface AdminAllMcpServer {
  id: string
  name: string
  description: string | null
  transport: string
  command: string | null
  args: string[] | null
  url: string | null
  is_active: boolean
  is_global: boolean
  tool_count: number
  user_id: string | null
  username: string | null
  email: string | null
  created_at: string
}

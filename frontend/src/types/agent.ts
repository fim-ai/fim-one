export interface SandboxConfig {
  memory?: string
  cpu?: number
  timeout?: number
}

export interface AgentResponse {
  id: string
  user_id: string
  name: string
  description: string | null
  icon: string | null
  instructions: string | null
  model_config_json: Record<string, unknown> | null
  tool_categories: string[] | null
  suggested_prompts: string[] | null
  kb_ids: string[] | null
  connector_ids: string[] | null
  mcp_server_ids: string[] | null
  grounding_config: Record<string, unknown> | null
  sandbox_config: SandboxConfig | null
  execution_mode: "react" | "dag" | "auto"
  status: string
  is_active: boolean
  visibility?: string // "personal" | "org" | "global"
  org_id?: string | null
  is_builder?: boolean
  compact_instructions?: string | null
  // Human-in-the-loop approval routing (Phase 2)
  confirmation_mode?: "auto" | "inline_only" | "channel_only"
  confirmation_approver_scope?: "initiator" | "agent_owner" | "org_members"
  require_confirmation_for_all?: boolean
  approval_channel_id?: string | null
  forked_from: string | null
  publish_status: string | null
  reviewed_by: string | null
  reviewed_at: string | null
  review_note: string | null
  published_at: string | null
  created_at: string
  updated_at: string | null
}

export interface AgentCreate {
  name: string
  description?: string | null
  icon?: string | null
  instructions?: string | null
  model_config_json?: Record<string, unknown>
  tool_categories?: string[]
  suggested_prompts?: string[]
  kb_ids?: string[]
  connector_ids?: string[]
  mcp_server_ids?: string[]
  grounding_config?: Record<string, unknown>
  sandbox_config?: SandboxConfig
  execution_mode?: "react" | "dag" | "auto"
  compact_instructions?: string | null
  confirmation_mode?: "auto" | "inline_only" | "channel_only"
  confirmation_approver_scope?: "initiator" | "agent_owner" | "org_members"
  require_confirmation_for_all?: boolean
  approval_channel_id?: string | null
}

export interface AgentUpdate {
  name?: string
  description?: string | null
  icon?: string | null
  instructions?: string | null
  model_config_json?: Record<string, unknown>
  tool_categories?: string[]
  suggested_prompts?: string[]
  kb_ids?: string[]
  connector_ids?: string[]
  mcp_server_ids?: string[]
  grounding_config?: Record<string, unknown>
  sandbox_config?: SandboxConfig
  execution_mode?: "react" | "dag" | "auto"
  compact_instructions?: string | null
  confirmation_mode?: "auto" | "inline_only" | "channel_only"
  confirmation_approver_scope?: "initiator" | "agent_owner" | "org_members"
  require_confirmation_for_all?: boolean
  approval_channel_id?: string | null
}

export interface AgentTemplate {
  id: string
  name: string
  description: string
  icon?: string | null
  category: string
  blueprint: Record<string, unknown>
}

export interface AICreateAgentResult {
  agent: AgentResponse
  message: string
  message_key?: string
  message_args?: Record<string, unknown>
}

export interface AIRefineAgentResult {
  agent: AgentResponse
  modified_fields: string[]
  message: string
  message_key?: string
  message_args?: Record<string, unknown>
}

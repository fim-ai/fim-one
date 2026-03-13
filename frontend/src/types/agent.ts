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
  grounding_config: Record<string, unknown> | null
  sandbox_config: SandboxConfig | null
  execution_mode: "react" | "dag" | "auto"
  status: string
  is_active: boolean
  visibility?: string // "personal" | "org" | "global"
  org_id?: string | null
  is_builder?: boolean
  discoverable?: boolean
  sub_agent_ids?: string[] | null
  skill_ids?: string[] | null
  compact_instructions?: string | null
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
  grounding_config?: Record<string, unknown>
  sandbox_config?: SandboxConfig
  execution_mode?: "react" | "dag" | "auto"
  skill_ids?: string[]
  compact_instructions?: string | null
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
  grounding_config?: Record<string, unknown>
  sandbox_config?: SandboxConfig
  execution_mode?: "react" | "dag" | "auto"
  skill_ids?: string[]
  compact_instructions?: string | null
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

export interface SandboxConfig {
  memory?: string
  cpu?: number
  timeout?: number
}

export interface AgentResponse {
  id: string
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
  execution_mode: "react" | "dag"
  status: string
  is_global?: boolean
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
  execution_mode?: "react" | "dag"
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
  execution_mode?: "react" | "dag"
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

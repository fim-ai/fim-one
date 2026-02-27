export interface AgentResponse {
  id: string
  name: string
  description: string | null
  instructions: string | null
  execution_mode: string
  model_config_json: Record<string, unknown> | null
  tool_categories: string[] | null
  suggested_prompts: string[] | null
  kb_ids: string[] | null
  grounding_config: Record<string, unknown> | null
  status: string
  published_at: string | null
  created_at: string
  updated_at: string | null
}

export interface AgentCreate {
  name: string
  description?: string
  instructions?: string
  execution_mode?: "react" | "dag"
  model_config_json?: Record<string, unknown>
  tool_categories?: string[]
  suggested_prompts?: string[]
  kb_ids?: string[]
  grounding_config?: Record<string, unknown>
}

export interface AgentUpdate {
  name?: string
  description?: string
  instructions?: string
  execution_mode?: "react" | "dag"
  model_config_json?: Record<string, unknown>
  tool_categories?: string[]
  suggested_prompts?: string[]
  kb_ids?: string[]
  grounding_config?: Record<string, unknown>
}

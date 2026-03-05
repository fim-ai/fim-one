export interface ModelConfigResponse {
  id: string
  name: string
  provider: string
  model_name: string
  base_url: string | null
  category: "llm" | "embedding" | "vision"
  temperature: number | null
  max_output_tokens: number | null
  context_size: number | null
  is_default: boolean
  is_active: boolean
  created_at: string
  updated_at: string | null
  // api_key is never returned from the backend
}

export interface ModelConfigCreate {
  name: string
  provider: string
  model_name: string
  base_url?: string | null
  api_key?: string | null
  category?: "llm" | "embedding" | "vision"
  temperature?: number | null
  max_output_tokens?: number | null
  context_size?: number | null
  is_default?: boolean
}

export type ModelConfigUpdate = Partial<ModelConfigCreate & { is_active: boolean }>

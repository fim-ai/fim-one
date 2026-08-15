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

// Organization types
export interface AdminOrganization {
  id: string
  name: string
  slug: string
  description: string | null
  icon: string | null
  owner_id: string
  owner_username: string | null
  owner_email: string
  parent_id: string | null
  is_active: boolean
  review_agents: boolean
  review_connectors: boolean
  review_kbs: boolean
  review_mcp_servers: boolean
  review_workflows: boolean
  review_skills: boolean
  member_count: number
  created_at: string
  updated_at: string | null
}

export interface OrgMember {
  id: string
  user_id: string
  username: string | null
  display_name: string | null
  email: string
  role: "owner" | "admin" | "member"
  invited_by: string | null
  created_at: string
}

export interface ReviewLogItem {
  id: string
  created_at: string
  org_id: string
  org_name: string | null
  resource_type: string
  resource_id: string
  resource_name: string | null
  action: string
  actor_id: string | null
  actor_name: string | null
}

// ---------------------------------------------------------------------------
// Billing — admin-side (mirrors src/fim_one/web/schemas/billing.py)
// ---------------------------------------------------------------------------

export interface AdminBillingPlan {
  id: number
  slug: string
  name: string
  monthly_token_quota: number
  stripe_price_id: string | null
  /** @deprecated Legacy display override; prefer ``price_display``. */
  price_cents: number | null
  /** Live Stripe Price ``unit_amount`` (cents). Null for Free / Stripe miss. */
  price_amount_cents: number | null
  /** ISO currency code from the Stripe Price (e.g. ``"usd"``). */
  price_currency: string | null
  /** Recurrence interval from the Stripe Price (``"month"``/``"year"``). */
  price_interval: string | null
  /** Pre-formatted, Stripe-sourced price string. Same value users see. */
  price_display: string
  description: string | null
  features: string[]
  features_json: Record<string, unknown>
  sort_order: number
  is_active: boolean
  is_default: boolean
  active_subscription_count: number
  created_at: string | null
}

export interface AdminBillingPlanCreate {
  slug: string
  name: string
  monthly_token_quota: number
  stripe_price_id?: string | null
  description?: string | null
  features?: string[]
  sort_order?: number
  is_active?: boolean
}

export interface AdminBillingPlanUpdate {
  name?: string
  price_cents?: number | null
  monthly_token_quota?: number
  stripe_price_id?: string | null
  description?: string | null
  features?: string[]
  sort_order?: number
  is_active?: boolean
}

export interface AdminBillingSubscription {
  id: number
  user_id: string
  user_email: string | null
  user_username: string | null
  plan_id: number
  plan_slug: string
  plan_name: string
  stripe_subscription_id: string
  stripe_price_id: string
  status: string
  current_period_start: string
  current_period_end: string
  cancel_at_period_end: boolean
  canceled_at: string | null
  created_at: string
  updated_at: string
}

export interface AdminBillingSubscriptionListResponse {
  items: AdminBillingSubscription[]
  total: number
  limit: number
  offset: number
}

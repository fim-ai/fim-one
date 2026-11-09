/**
 * Messaging channel types.
 *
 * A channel connects an external messaging platform (Feishu, WeCom, Slack, …)
 * to the FIM One agent runtime. Agents can push notifications and receive
 * interactive actions (e.g. card button clicks) through the configured channel.
 *
 * Currently only `feishu` is wired up; `wecom`/`slack`/`teams` are reserved
 * for future releases.
 */

export type ChannelType = "feishu" | "wecom" | "slack" | "teams"

/**
 * Sanitised configuration returned by the backend.
 *
 * Secret fields (`app_secret`, `verification_token`, `encrypt_key`) are never
 * echoed back — instead the API returns `*_configured` booleans so the UI can
 * show a "configured" badge or a "leave blank to keep" hint.
 */
export interface ChannelConfig {
  // Feishu fields
  app_id?: string
  chat_id?: string
  chat_name?: string
  app_secret_configured?: boolean
  verification_token_configured?: boolean
  encrypt_key_configured?: boolean

  // Reserved for future channel types
  [key: string]: unknown
}

export interface Channel {
  id: string
  name: string
  type: ChannelType
  org_id: string
  config: ChannelConfig
  /** Canonical callback URL that the third-party platform should POST to. */
  callback_url: string
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface ChannelListResponse {
  items: Channel[]
}

/**
 * Request bodies.
 *
 * For create/update, clients send the plain-text secrets. On update, an empty
 * string means "do not touch the stored value".
 */
export interface FeishuChannelConfigInput {
  app_id: string
  app_secret?: string
  chat_id: string
  verification_token?: string
  encrypt_key?: string
}

export interface ChannelCreateRequest {
  name: string
  type: ChannelType
  org_id: string
  config: FeishuChannelConfigInput | Record<string, unknown>
}

export interface ChannelUpdateRequest {
  name?: string
  config?: FeishuChannelConfigInput | Record<string, unknown>
  is_active?: boolean
}

export interface ChannelTestResponse {
  ok: boolean
  error?: string
  chat_name?: string
}

/**
 * Hook Playground — exercises the real approval-gate round-trip from the UI.
 *
 * Unlike `/test` (which sends a preview card with a sentinel id whose button
 * clicks are no-ops), `test-approval` creates a genuine `ConfirmationRequest`
 * row.  The UI then polls `getConfirmation()` until the status flips.
 */
export interface TestApprovalRequest {
  tool_name?: string
  tool_args?: Record<string, unknown>
  title?: string
  summary?: string
}

export interface TestApprovalResponse {
  ok: boolean
  confirmation_id?: string
  error?: string
}

export type ConfirmationStatusValue =
  | "pending"
  | "approved"
  | "rejected"
  | "expired"

export interface ConfirmationStatus {
  id: string
  status: ConfirmationStatusValue
  tool_name?: string | null
  tool_args?: Record<string, unknown> | null
  test_mode: boolean
  created_at: string
  responded_at?: string | null
  responded_by_open_id?: string | null
}

/**
 * Payload for `POST /api/channels/discover-chats`.
 *
 * - **Create mode**: provide `app_id` + `app_secret` + `org_id`.
 * - **Edit mode**: provide `channel_id`; the server reuses the stored
 *   (encrypted) secret. Pass `app_secret` only if the user re-typed it.
 */
export interface ChatDiscoveryRequest {
  app_id: string
  app_secret?: string
  channel_id?: string
  org_id?: string
}

export interface ChatInfo {
  chat_id: string
  name: string
  avatar?: string | null
  description?: string | null
  member_count?: number | null
  external: boolean
}

export interface ChatDiscoveryResponse {
  items: ChatInfo[]
}

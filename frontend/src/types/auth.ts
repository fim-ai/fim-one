export interface OAuthBindingInfo {
  provider: string
  email?: string | null
  display_name?: string | null
  bound_at: string
}

export interface UserInfo {
  id: string
  username: string | null
  display_name: string | null
  avatar: string | null
  is_admin: boolean
  system_instructions?: string | null
  preferred_language?: "auto" | "en" | "zh" | "ja" | "ko" | "de" | "fr"
  timezone?: string | null
  oauth_provider?: string | null
  email?: string | null
  has_password?: boolean
  oauth_bindings?: OAuthBindingInfo[]
  onboarding_completed: boolean
  has_connector?: boolean
  has_agent?: boolean
  has_conversation?: boolean
  security_notice?: SecurityNotice | null
}

/** One kind of thing switched off when the address was first verified. */
export interface SecurityNoticeItem {
  kind: string
  count: number
  items: { id: string; name: string; role?: string }[]
}

/**
 * What was switched off when an emailed code first verified the account's
 * address (`/api/auth/me`), until the account holder dismisses it.
 */
export interface SecurityNotice {
  at: string
  sign_in: {
    password_removed?: boolean
    second_factor_removed?: boolean
    providers_unlinked?: string[]
    api_keys?: { id: string; name: string }[]
  }
  items: SecurityNoticeItem[]
  restorable: ("system_instructions" | "default_agent")[]
}

export interface ChangePasswordRequest {
  current_password: string
  new_password: string
}

export interface SetPasswordRequest {
  new_password: string
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  user: UserInfo
}

export interface LoginRequest {
  email: string
  password: string
}

export interface LoginWithCodeRequest {
  email: string
  code: string
}

export interface RegisterRequest {
  password: string
  email: string
  invite_code?: string
  verification_code?: string
  privacy_accepted?: boolean
}

export interface SetupRequest {
  email: string
  password: string
}

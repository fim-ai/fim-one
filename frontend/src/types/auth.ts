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

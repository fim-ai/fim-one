export interface OAuthBindingInfo {
  provider: string
  email?: string | null
  display_name?: string | null
  bound_at: string
}

export interface UserInfo {
  id: string
  username: string
  display_name: string | null
  is_admin: boolean
  system_instructions?: string | null
  preferred_language?: "auto" | "en" | "zh"
  oauth_provider?: string | null
  email?: string | null
  has_password?: boolean
  oauth_bindings?: OAuthBindingInfo[]
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
  username?: string
  email?: string
  password: string
}

export interface RegisterRequest {
  username: string
  password: string
  email: string
}

export interface SetupRequest {
  username: string
  password: string
  email?: string
}

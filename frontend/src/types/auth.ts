export interface UserInfo {
  id: string
  username: string
  display_name: string | null
  is_admin: boolean
  system_instructions?: string | null
}

export interface ChangePasswordRequest {
  current_password: string
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
  username: string
  password: string
}

export interface RegisterRequest {
  username: string
  password: string
}

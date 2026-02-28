"""Authentication request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class UserInfo(BaseModel):
    id: str
    username: str
    display_name: str | None = None
    is_admin: bool
    system_instructions: str | None = None


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=50)
    password: str = Field(min_length=6, max_length=100)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    user: UserInfo


class UpdateProfileRequest(BaseModel):
    display_name: str | None = Field(None, max_length=50)
    system_instructions: str | None = Field(None, max_length=2000)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6, max_length=100)


class RefreshRequest(BaseModel):
    refresh_token: str

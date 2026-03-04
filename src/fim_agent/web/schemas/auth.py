"""Authentication request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class OAuthBindingInfo(BaseModel):
    provider: str
    email: str | None = None
    display_name: str | None = None
    bound_at: datetime


class UserInfo(BaseModel):
    id: str
    username: str
    display_name: str | None = None
    is_admin: bool
    system_instructions: str | None = None
    preferred_language: str = "auto"
    oauth_provider: str | None = None
    email: str | None = None
    has_password: bool = False
    oauth_bindings: list[OAuthBindingInfo] = []


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=50)
    password: str = Field(min_length=6, max_length=100)
    email: str = Field(..., max_length=255)


class LoginRequest(BaseModel):
    username: str | None = None
    password: str
    email: str | None = None

    @model_validator(mode="after")
    def check_identifier(self):
        if not self.username and not self.email:
            raise ValueError("username or email is required")
        return self


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    user: UserInfo


class UpdateProfileRequest(BaseModel):
    display_name: str | None = Field(None, max_length=50)
    system_instructions: str | None = Field(None, max_length=2000)
    preferred_language: str | None = Field(None, pattern=r"^(auto|en|zh)$")
    email: str | None = Field(None, max_length=255)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6, max_length=100)


class SetPasswordRequest(BaseModel):
    """For OAuth-only users to set an initial password."""

    new_password: str = Field(min_length=8, max_length=100)


class RefreshRequest(BaseModel):
    refresh_token: str


class SetupRequest(BaseModel):
    username: str = Field(min_length=2, max_length=50)
    password: str = Field(min_length=6, max_length=100)
    email: str | None = Field(None, max_length=255)

"""Authentication request/response schemas."""

from __future__ import annotations

from datetime import datetime

import re

from pydantic import BaseModel, Field, field_validator

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class OAuthBindingInfo(BaseModel):
    provider: str
    email: str | None = None
    display_name: str | None = None
    bound_at: datetime


class UserInfo(BaseModel):
    id: str
    username: str | None = None
    display_name: str | None = None
    is_admin: bool
    system_instructions: str | None = None
    preferred_language: str = "auto"
    oauth_provider: str | None = None
    email: str | None = None
    has_password: bool = False
    oauth_bindings: list[OAuthBindingInfo] = []
    onboarding_completed: bool = False
    avatar: str | None = None
    has_connector: bool = False
    has_agent: bool = False
    has_conversation: bool = False


class RegisterRequest(BaseModel):
    password: str = Field(min_length=8, max_length=100)
    email: str = Field(..., max_length=255)
    invite_code: str | None = None
    verification_code: str | None = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email address")
        return v.lower()


class SendVerificationCodeRequest(BaseModel):
    email: str = Field(..., max_length=255)
    locale: str | None = Field(None, pattern=r"^(en|zh|ja|ko|de|fr)$")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email address")
        return v.lower()



class SendLoginCodeRequest(BaseModel):
    email: str = Field(..., max_length=255)
    locale: str | None = Field(None, pattern=r"^(en|zh|ja|ko|de|fr)$")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email address")
        return v.lower()


class LoginWithCodeRequest(BaseModel):
    email: str = Field(..., max_length=255)
    code: str = Field(..., min_length=6, max_length=6)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email address")
        return v.lower()


class LoginRequest(BaseModel):
    email: str
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
    preferred_language: str | None = Field(None, pattern=r"^(auto|en|zh|ja|ko|de|fr)$")
    onboarding_completed: bool | None = None
    avatar: str | None = None  # "builtin:cat", "builtin:star", etc. or None to remove
    username: str | None = Field(None, min_length=2, max_length=50)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=100)


class SetPasswordRequest(BaseModel):
    """For OAuth-only users to set an initial password."""

    new_password: str = Field(min_length=8, max_length=100)


class ResetPasswordRequest(BaseModel):
    """For authenticated users who forgot their password — verify via OTP then set new."""
    code: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(min_length=8, max_length=100)


class SendResetCodeRequest(BaseModel):
    locale: str | None = Field(None, pattern=r"^(en|zh|ja|ko|de|fr)$")


class SendForgotCodeRequest(BaseModel):
    """Unauthenticated: send OTP to reset a forgotten password."""
    email: str = Field(..., max_length=255)
    locale: str | None = Field(None, pattern=r"^(en|zh|ja|ko|de|fr)$")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email address")
        return v.lower()


class VerifyForgotCodeRequest(BaseModel):
    """Unauthenticated: verify OTP code for forgot password."""
    email: str = Field(..., max_length=255)
    code: str = Field(..., min_length=6, max_length=6)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email address")
        return v.lower()


class ForgotPasswordRequest(BaseModel):
    """Unauthenticated: reset password using verified reset token."""
    email: str = Field(..., max_length=255)
    reset_token: str = Field(..., min_length=36, max_length=36)
    new_password: str = Field(min_length=8, max_length=100)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email address")
        return v.lower()


class RefreshRequest(BaseModel):
    refresh_token: str


class SetupRequest(BaseModel):
    password: str = Field(min_length=8, max_length=100)
    email: str = Field(..., max_length=255)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email address")
        return v.lower()

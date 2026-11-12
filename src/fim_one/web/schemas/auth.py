"""Authentication request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

import re

from pydantic import BaseModel, Field, field_validator

# Not whitespace, "@", NUL or a lone surrogate (neither can be stored).
_EMAIL_RE = re.compile(r"^[^\s@\x00\ud800-\udfff]+@[^\s@\x00\ud800-\udfff]+\.[^\s@\x00\ud800-\udfff]+$")


def normalize_email(value: str) -> str:
    """The form an address is stored, looked up and counted in.

    Lower case, without surrounding space and without the trailing dot(s)
    of a fully qualified domain (``a@example.com.`` reaches the same
    mailbox as ``a@example.com``).
    """
    local, at, domain = value.strip().lower().rpartition("@")
    return f"{local}{at}{domain.rstrip('.')}" if at else value.strip().lower()


def valid_email(value: str) -> str:
    """*value* normalised; ``ValueError`` when it is not an address."""
    normalized = normalize_email(value)
    if not _EMAIL_RE.match(normalized):
        raise ValueError("Invalid email address")
    return normalized


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
    privacy_accepted_at: datetime | None = None
    # Personal settings
    timezone: str | None = None
    default_agent_id: str | None = None
    default_exec_mode: str | None = None
    default_reasoning: bool | None = None
    totp_enabled: bool = False
    # What was switched off when the address was first proven by an emailed
    # code, until dismissed (see ``web/services/address_claim.py``).
    security_notice: dict[str, Any] | None = None


class SecurityNoticeRestoreRequest(BaseModel):
    item: Literal["system_instructions", "default_agent"]


class RegisterRequest(BaseModel):
    password: str = Field(min_length=8, max_length=100)
    email: str = Field(..., max_length=255)
    invite_code: str | None = None
    verification_code: str | None = None
    privacy_accepted: bool = False

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        return valid_email(v)


class SendVerificationCodeRequest(BaseModel):
    email: str = Field(..., max_length=255)
    locale: str | None = Field(None, pattern=r"^(en|zh|ja|ko|de|fr)$")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        return valid_email(v)



class SendLoginCodeRequest(BaseModel):
    email: str = Field(..., max_length=255)
    locale: str | None = Field(None, pattern=r"^(en|zh|ja|ko|de|fr)$")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        return valid_email(v)


class LoginWithCodeRequest(BaseModel):
    email: str = Field(..., max_length=255)
    code: str = Field(..., min_length=6, max_length=6)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        return valid_email(v)


class LoginRequest(BaseModel):
    # Bounded like the other addresses: what is typed here is looked up and,
    # for a failed attempt, recorded.
    email: str = Field(..., max_length=255)
    password: str = Field(..., max_length=1000)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        # Only values no account can hold are refused here (422), since they
        # cannot be stored or looked up.
        if "\x00" in v or any("\ud800" <= ch <= "\udfff" for ch in v):
            raise ValueError("Invalid email address")
        return v


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
    # A built-in avatar token ("builtin:cat", "builtin:cat:blue") or "" to
    # remove.  Uploaded images are set by POST /api/auth/avatar only, so this
    # field can never name a file.
    avatar: str | None = Field(
        None,
        max_length=80,
        pattern=r"^(builtin:[a-z0-9][a-z0-9_-]{0,31}(:[a-z0-9][a-z0-9_-]{0,31})?)?$",
    )
    username: str | None = Field(None, min_length=2, max_length=50)
    # Personal settings
    timezone: str | None = Field(None, max_length=50)
    default_agent_id: str | None = Field(None, max_length=36)
    default_exec_mode: str | None = Field(None, pattern=r"^(react|dag|auto)$")
    default_reasoning: bool | None = None


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
        return valid_email(v)


class VerifyForgotCodeRequest(BaseModel):
    """Unauthenticated: verify OTP code for forgot password."""
    email: str = Field(..., max_length=255)
    code: str = Field(..., min_length=6, max_length=6)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        return valid_email(v)


class ForgotPasswordRequest(BaseModel):
    """Unauthenticated: reset password using verified reset token."""
    email: str = Field(..., max_length=255)
    reset_token: str = Field(..., min_length=36, max_length=36)
    new_password: str = Field(min_length=8, max_length=100)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        return valid_email(v)


class RefreshRequest(BaseModel):
    refresh_token: str


class SetupRequest(BaseModel):
    password: str = Field(min_length=8, max_length=100)
    email: str = Field(..., max_length=255)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        return valid_email(v)

"""Schemas for /api/me/ personal settings endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# API Keys
# ---------------------------------------------------------------------------


class UserApiKeyInfo(BaseModel):
    id: str
    name: str
    key_prefix: str
    scopes: str | None = None
    is_active: bool
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    total_requests: int = 0
    created_at: str


class UserApiKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    scopes: str | None = None
    expires_at: datetime | None = None


class UserApiKeyCreateResponse(BaseModel):
    """Returned only at creation time -- includes the full key."""

    id: str
    name: str
    key: str
    key_prefix: str
    scopes: str | None = None
    expires_at: datetime | None = None
    created_at: str


class UserApiKeyToggleRequest(BaseModel):
    is_active: bool


class PaginatedUserApiKeyResponse(BaseModel):
    items: list[UserApiKeyInfo]
    total: int
    page: int
    size: int
    pages: int


# ---------------------------------------------------------------------------
# Sessions (LoginHistory)
# ---------------------------------------------------------------------------


class SessionInfo(BaseModel):
    id: str
    ip_address: str | None = None
    user_agent: str | None = None
    success: bool
    failure_reason: str | None = None
    created_at: str


class SessionListResponse(BaseModel):
    items: list[SessionInfo]
    total: int


# ---------------------------------------------------------------------------
# Credentials (read-only aggregation)
# ---------------------------------------------------------------------------


class ConnectorCredentialInfo(BaseModel):
    id: str
    connector_id: str
    created_at: str


class McpCredentialInfo(BaseModel):
    id: str
    server_id: str
    created_at: str


class CredentialsResponse(BaseModel):
    connector_credentials: list[ConnectorCredentialInfo]
    mcp_credentials: list[McpCredentialInfo]


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


class DailyUsage(BaseModel):
    date: str
    tokens: int


class AgentUsage(BaseModel):
    agent_id: str | None = None
    agent_name: str
    tokens: int


class UsageResponse(BaseModel):
    total_tokens: int
    quota: int | None = None
    quota_used_pct: float | None = None
    daily: list[DailyUsage]
    by_agent: list[AgentUsage]


# ---------------------------------------------------------------------------
# Notification Preferences
# ---------------------------------------------------------------------------


class NotificationPrefInfo(BaseModel):
    id: str
    event_type: str
    channel: str
    enabled: bool
    config: str | None = None


class NotificationPrefItem(BaseModel):
    event_type: str = Field(..., max_length=50)
    channel: str = Field(..., max_length=20)
    enabled: bool = True
    config: str | None = None


class NotificationPrefBulkRequest(BaseModel):
    preferences: list[NotificationPrefItem]


class NotificationPrefListResponse(BaseModel):
    items: list[NotificationPrefInfo]


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


class SubscriptionInfo(BaseModel):
    id: str
    resource_type: str
    resource_id: str
    resource_name: str | None = None
    org_id: str
    subscribed_at: str


class SubscriptionListResponse(BaseModel):
    items: list[SubscriptionInfo]
    total: int


# ---------------------------------------------------------------------------
# 2FA
# ---------------------------------------------------------------------------


class TwoFactorSetupResponse(BaseModel):
    secret: str
    otpauth_uri: str


class TwoFactorEnableRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6)


class TwoFactorEnableResponse(BaseModel):
    backup_codes: list[str]


class TwoFactorDisableRequest(BaseModel):
    password: str


class TwoFactorBackupCodesRequest(BaseModel):
    password: str


class TwoFactorBackupCodesResponse(BaseModel):
    backup_codes: list[str]


class TwoFactorVerifyRequest(BaseModel):
    temp_token: str
    code: str = Field(..., min_length=6, max_length=10)


class TwoFactorLoginResponse(BaseModel):
    requires_2fa: bool = True
    temp_token: str


# ---------------------------------------------------------------------------
# Email Change
# ---------------------------------------------------------------------------


class ChangeEmailRequestBody(BaseModel):
    new_email: str = Field(..., max_length=255)
    password: str


class ChangeEmailConfirmBody(BaseModel):
    new_email: str = Field(..., max_length=255)
    code: str = Field(..., min_length=6, max_length=6)

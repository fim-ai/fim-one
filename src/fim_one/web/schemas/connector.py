"""Connector request/response schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


# --- Action Schemas ---


class ActionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    method: str = "GET"
    path: str = Field(min_length=1, max_length=500)
    parameters_schema: dict[str, Any] | None = None
    request_body_template: dict[str, Any] | None = None
    response_extract: str | None = None
    requires_confirmation: bool = False


class ActionUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    method: str | None = None
    path: str | None = None
    parameters_schema: dict[str, Any] | None = None
    request_body_template: dict[str, Any] | None = None
    response_extract: str | None = None
    requires_confirmation: bool | None = None


class ActionResponse(BaseModel):
    id: str
    connector_id: str
    name: str
    description: str | None
    method: str
    path: str
    parameters_schema: dict[str, Any] | None
    request_body_template: dict[str, Any] | None
    response_extract: str | None
    requires_confirmation: bool
    created_at: str
    updated_at: str | None


# --- OpenAPI Import ---


class OpenAPIImportRequest(BaseModel):
    """Accepts an OpenAPI spec via one of three input modes."""

    spec: dict[str, Any] | None = None
    spec_url: str | None = None
    spec_raw: str | None = None
    replace_existing: bool = False


# --- Connector Schemas ---


class ConnectorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    icon: str | None = None
    type: str = Field(default="api", pattern=r"^(api|database)$")
    base_url: str | None = Field(default=None, max_length=500)
    auth_type: str = "none"
    auth_config: dict[str, Any] | None = None
    db_config: dict[str, Any] | None = None

    @field_validator("base_url")
    @classmethod
    def validate_base_url_scheme(cls, v: str | None) -> str | None:
        if v is None:
            return v
        from urllib.parse import urlparse

        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("Only http and https schemes are allowed for base_url")
        return v


class ConnectorUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    type: str | None = None
    base_url: str | None = None
    auth_type: str | None = None
    auth_config: dict[str, Any] | None = None
    db_config: dict[str, Any] | None = None
    allow_fallback: bool | None = None


class ConnectorResponse(BaseModel):
    id: str
    user_id: str
    name: str
    description: str | None
    icon: str | None
    type: str
    base_url: str | None
    auth_type: str
    auth_config: dict[str, Any] | None
    db_config: dict[str, Any] | None = None
    is_official: bool
    forked_from: str | None
    version: int
    visibility: str = "personal"
    org_id: str | None = None
    allow_fallback: bool = True
    has_default_credentials: bool = False
    actions: list[ActionResponse]
    created_at: str
    updated_at: str | None


# --- AI Action Schemas ---


class AIGenerateActionsRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)
    context: str | None = Field(default=None, max_length=10000)


class AIRefineActionRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)
    action_id: str | None = None
    history: list[dict] = Field(default_factory=list)


class AIActionResult(BaseModel):
    created: list[ActionResponse] = []
    updated: list[ActionResponse] = []
    deleted: list[str] = []
    failed: list[str] = []
    connector_updated: ConnectorResponse | None = None
    message: str = ""
    message_key: str = ""
    message_args: dict = {}


class AICreateConnectorRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=5000)
    history: list[dict] = Field(default_factory=list)


class AICreateConnectorResult(BaseModel):
    connector: ConnectorResponse
    message: str = ""
    message_key: str = ""
    message_args: dict = {}


class CredentialUpsertRequest(BaseModel):
    token: str | None = None
    api_key: str | None = None
    username: str | None = None
    password: str | None = None


class MyCredentialStatus(BaseModel):
    has_credentials: bool
    auth_type: str
    allow_fallback: bool

"""MCP Server request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class MCPServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    transport: str = "stdio"
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    working_dir: str | None = None
    headers: dict[str, str] | None = None
    is_active: bool = True

    @model_validator(mode="after")
    def validate_transport(self):
        if self.transport == "stdio" and not self.command:
            raise ValueError("command is required for stdio transport")
        if self.transport in ("sse", "streamable_http") and not self.url:
            raise ValueError("url is required for sse/streamable_http transport")
        return self


class MCPServerUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    transport: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    working_dir: str | None = None
    headers: dict[str, str] | None = None
    is_active: bool | None = None


class MCPServerResponse(BaseModel):
    id: str
    name: str
    description: str | None
    transport: str
    command: str | None
    args: list[str] | None
    env: dict[str, str] | None
    url: str | None
    working_dir: str | None
    headers: dict[str, str] | None
    is_active: bool
    tool_count: int
    allow_fallback: bool = True
    visibility: str = "personal"
    org_id: str | None = None
    created_at: str
    updated_at: str | None

"""MCP Server request/response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

# The transports the MCP client can open.  Anything else could be stored but
# never connected, and no personal credential would fit it.
MCPTransport = Literal["stdio", "sse", "streamable_http"]


class MCPServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    transport: MCPTransport = "stdio"
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    working_dir: str | None = None
    headers: dict[str, str] | None = None
    is_active: bool = True
    # Whether subscribers without their own credentials may fall back to the
    # owner's server-level env/headers. Defaults to False — sharing the owner's
    # credentials is opt-in (the owner can always use them regardless).
    allow_fallback: bool = False

    @model_validator(mode="after")
    def validate_transport(self) -> MCPServerCreate:
        if self.transport == "stdio" and not self.command:
            raise ValueError("command is required for stdio transport")
        if self.transport in ("sse", "streamable_http") and not self.url:
            raise ValueError("url is required for sse/streamable_http transport")
        return self


class MCPServerUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    transport: MCPTransport | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    working_dir: str | None = None
    headers: dict[str, str] | None = None
    is_active: bool | None = None
    allow_fallback: bool | None = None


class MCPServerResponse(BaseModel):
    id: str
    user_id: str
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
    # Fallback is off and the URL or the launch arguments carry a credential:
    # nobody but the owner can connect, whatever credential they save.
    address_private: bool = False
    # For the owner of a shared server: why others cannot use it as saved.
    # ``mcp_server_address_private`` or absent.
    warning: str | None = None
    forked_from: str | None = None
    my_has_credentials: bool = False
    visibility: str = "personal"
    org_id: str | None = None
    publish_status: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    review_note: str | None = None
    source: str | None = None
    created_at: str
    updated_at: str | None


class MCPServerForkRequest(BaseModel):
    """Optional overrides when forking (cloning) an MCP server."""

    name: str | None = None  # Custom name; defaults to "{original} (Copy)"


class MCPMyCredentialStatus(BaseModel):
    has_credentials: bool
    env_keys: list[str] = []  # kept for backwards compat
    env: dict[str, str] = {}  # full env dict for editing
    header_keys: list[str] = []  # header names (SSE / streamable HTTP servers)
    headers: dict[str, str] = {}  # full headers dict for editing


class MCPMyCredentialUpsert(BaseModel):
    env: dict[str, str] | None = None
    headers: dict[str, str] | None = None

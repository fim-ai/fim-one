"""Common response schemas shared across all API endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ApiResponse(BaseModel):
    success: bool = True
    data: Any = None
    error: str | None = None


class PaginatedResponse(BaseModel):
    items: list[Any]
    total: int
    page: int
    size: int
    pages: int


class PublishRequest(BaseModel):
    """Request body for publish endpoints (agents, connectors, KBs, MCP servers)."""

    scope: str  # "org" or "global"
    org_id: str | None = None

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
    """Request body for publish endpoints (agents, connectors, MCP servers).

    Publishing is org-scoped. The Market is the shadow "market" org, so
    publishing to it is just ``scope="org"`` with its org id. (The old
    ``"global"`` scope wrote a visibility no reader ever queried and has
    been removed.)
    """

    scope: str  # "org"
    org_id: str | None = None
    allow_fallback: bool = True

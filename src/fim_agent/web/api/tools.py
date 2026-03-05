"""Tool catalog endpoint (public, no auth required)."""

from __future__ import annotations

from fastapi import APIRouter

from fim_agent.core.tool.builtin import discover_builtin_tools
from fim_agent.core.tool.registry import ToolRegistry

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("/catalog")
async def get_tool_catalog() -> dict:
    """Return metadata for all built-in tools (no auth required)."""
    tools = discover_builtin_tools()
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    catalog = registry.to_catalog()
    categories = sorted({t["category"] for t in catalog})
    return {"tools": catalog, "categories": categories}

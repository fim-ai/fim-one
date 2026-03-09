"""Tool catalog endpoint (public, no auth required)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from fim_agent.core.tool.builtin import discover_builtin_tools
from fim_agent.core.tool.registry import ToolRegistry
from fim_agent.db import get_session
from fim_agent.web.api.admin import SETTING_DISABLED_BUILTIN_TOOLS
from fim_agent.web.api.admin_utils import get_setting

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("/catalog")
async def get_tool_catalog(
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict:
    """Return metadata for all built-in tools (no auth required)."""
    tools = discover_builtin_tools()
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    catalog = registry.to_catalog()

    # Read globally disabled tools from admin settings
    disabled_raw = await get_setting(db, SETTING_DISABLED_BUILTIN_TOOLS, default="[]")
    try:
        disabled_names: list[str] = json.loads(disabled_raw)
        if not isinstance(disabled_names, list):
            disabled_names = []
    except (json.JSONDecodeError, TypeError):
        disabled_names = []
    disabled_set = set(disabled_names)

    for entry in catalog:
        entry["disabled"] = entry["name"] in disabled_set

    all_cats = {t["category"] for t in catalog}
    # "general" always first, then the rest alphabetically
    categories = (["general"] if "general" in all_cats else []) + sorted(all_cats - {"general"})
    return {"tools": catalog, "categories": categories}

"""Hook management API endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from fim_one.core.agent.builtin_hooks import BUILTIN_HOOKS

router = APIRouter(prefix="/api/hooks", tags=["hooks"])


@router.get("/builtin")
async def list_builtin_hooks() -> dict[str, list[dict[str, str]]]:
    """Return metadata for all available built-in hooks.

    No authentication required — hook descriptions are not sensitive.
    """
    hooks = []
    for name, info in BUILTIN_HOOKS.items():
        hooks.append({
            "name": name,
            "description": info["description"],
            "hook_point": info["hook_point"],
        })
    return {"hooks": hooks}

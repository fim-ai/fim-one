"""Agent template endpoints — public listing and creation from templates.

Public endpoints serve built-in (hardcoded) agent templates in a unified list,
grouped by category.  Users can create new agents from any template.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.auth import get_current_user
from fim_one.web.exceptions import AppError
from fim_one.db.models import Agent, User
from fim_one.web.schemas.common import ApiResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public router — accessible by any authenticated user
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/agent-templates", tags=["agent-templates"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _template_to_response(t: dict[str, Any]) -> dict[str, Any]:
    """Convert a built-in template dict to a flat response dict."""
    return {
        "id": t["id"],
        "name": t["name"],
        "description": t["description"],
        "icon": t.get("icon"),
        "category": t.get("category", "basic"),
        "blueprint": t["blueprint"],
    }


# ---------------------------------------------------------------------------
# Public endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=ApiResponse)
async def list_agent_templates(
    current_user: User = Depends(get_current_user),  # noqa: ARG001, B008
) -> ApiResponse:
    """List all built-in agent templates, grouped by category."""
    from fim_one.core.agent.template_seeds import list_templates

    builtin = list_templates()
    items: list[dict[str, Any]] = [_template_to_response(t) for t in builtin]

    # Group by category
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        grouped[item["category"]].append(item)

    return ApiResponse(data={"templates": items, "by_category": dict(grouped)})


@router.post("/{template_id}/create", response_model=ApiResponse)
async def create_agent_from_template(
    template_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Create a new agent by cloning a template's blueprint."""
    from fim_one.core.agent.template_seeds import get_template

    template = get_template(template_id)
    if not template:
        raise AppError("template_not_found", status_code=404)

    blueprint = template["blueprint"]

    agent = Agent(
        user_id=current_user.id,
        name=template["name"],
        icon=None,  # template icon is a Lucide name, not an emoji
        description=template["description"],
        instructions=blueprint.get("instructions"),
        model_config_json=blueprint.get("model_config_json"),
        tool_categories=blueprint.get("tool_categories"),
        suggested_prompts=blueprint.get("suggested_prompts"),
        execution_mode=blueprint.get("execution_mode", "auto"),
        sandbox_config=blueprint.get("sandbox_config"),
        status="draft",
    )
    db.add(agent)
    await db.commit()

    # Re-fetch to get server-generated fields
    result = await db.execute(select(Agent).where(Agent.id == agent.id))
    agent = result.scalar_one()

    # Reuse the existing response builder from agents module
    from fim_one.web.api.agents import _agent_to_response

    return ApiResponse(data=_agent_to_response(agent).model_dump())

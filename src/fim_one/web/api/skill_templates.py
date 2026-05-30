"""Skill template endpoints — list built-in templates and create skills from them."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.auth import get_current_user
from fim_one.web.exceptions import AppError
from fim_one.db.models import User
from fim_one.db.models.skill import Skill
from fim_one.web.schemas.common import ApiResponse

router = APIRouter(prefix="/api/skill-templates", tags=["skill-templates"])


@router.get("", response_model=ApiResponse)
async def list_skill_templates(
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> ApiResponse:
    """List all built-in skill templates."""
    from fim_one.core.skills.template_seeds import list_templates

    return ApiResponse(data=list_templates())


@router.post("/{template_id}/create", response_model=ApiResponse)
async def create_skill_from_template(
    template_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Create a new skill from a built-in template."""
    from fim_one.core.skills.template_seeds import get_template

    template = get_template(template_id)
    if not template:
        raise AppError("template_not_found", status_code=404)

    bp = template["blueprint"]
    skill = Skill(
        user_id=current_user.id,
        name=template["name"],
        description=bp.get("description") or template.get("description"),
        content=bp.get("content", ""),
        is_active=True,
    )
    db.add(skill)
    await db.commit()
    result = await db.execute(select(Skill).where(Skill.id == skill.id))
    skill = result.scalar_one()

    from fim_one.web.api.skills import _skill_to_response

    return ApiResponse(data=_skill_to_response(skill).model_dump())

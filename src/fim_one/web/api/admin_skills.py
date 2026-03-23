"""Admin endpoints for skill management across all users."""

from __future__ import annotations

import logging
import math

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.auth import get_current_admin
from fim_one.web.exceptions import AppError
from fim_one.web.models import Agent, Skill, User
from fim_one.web.schemas.common import PaginatedResponse
from fim_one.web.schemas.workflow import BatchOperationResponse

from fim_one.web.api.admin_utils import write_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AdminSkillInfo(BaseModel):
    id: str
    name: str
    description: str | None = None
    visibility: str = "personal"
    is_active: bool = True
    status: str = "draft"
    user_id: str | None = None
    username: str | None = None
    email: str | None = None
    agent_count: int = 0
    created_at: str


class AdminSkillDetail(AdminSkillInfo):
    content: str = ""
    script: str | None = None
    script_type: str | None = None
    org_id: str | None = None


class SkillToggleRequest(BaseModel):
    is_active: bool


class BatchSkillDeleteRequest(BaseModel):
    ids: list[str] = Field(..., min_length=1, max_length=100)


class BatchSkillToggleRequest(BaseModel):
    ids: list[str] = Field(..., min_length=1, max_length=100)
    is_active: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _count_agents_using_skill(db: AsyncSession, skill_id: str) -> int:
    """Count how many agents reference this skill_id in their skill_ids JSON array.

    Since skill_ids is stored as a JSON array, we search for the skill_id string
    within the serialized JSON text. This is an approximation — exact matching
    would require dialect-specific JSON functions.
    """
    from sqlalchemy import String, cast

    pattern = f"%{skill_id}%"
    count_stmt = (
        select(func.count())
        .select_from(Agent)
        .where(Agent.skill_ids.isnot(None))
        .where(cast(Agent.skill_ids, String).like(pattern))
    )
    try:
        result = (await db.execute(count_stmt)).scalar_one()
    except Exception:
        # Fallback: return 0 if the cast doesn't work on this dialect
        result = 0
    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/skills", response_model=PaginatedResponse)
async def list_all_skills(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    """List all skills across all users. Requires admin privileges."""
    stmt = (
        select(Skill, User)
        .outerjoin(User, Skill.user_id == User.id)
    )
    count_base = select(Skill)

    if q:
        pattern = f"%{q}%"
        filter_clause = Skill.name.ilike(pattern)
        stmt = stmt.where(filter_clause)
        count_base = count_base.where(filter_clause)

    count_stmt = select(func.count()).select_from(count_base.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    rows = (
        await db.execute(
            stmt.order_by(Skill.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()

    items = []
    for skill, user in rows:
        items.append(
            AdminSkillInfo(
                id=skill.id,
                name=skill.name,
                description=skill.description,
                visibility=skill.visibility,
                is_active=skill.is_active,
                status=skill.status,
                user_id=skill.user_id,
                username=user.username if user else None,
                email=user.email if user else None,
                agent_count=0,  # Computed below if needed — skip for list perf
                created_at=skill.created_at.isoformat() if skill.created_at else "",
            ).model_dump()
        )

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total > 0 else 1,
    )


@router.get("/skills/{skill_id}", response_model=AdminSkillDetail)
async def admin_get_skill(
    skill_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> AdminSkillDetail:
    """Get skill detail with content. Requires admin privileges."""
    result = await db.execute(
        select(Skill, User)
        .outerjoin(User, Skill.user_id == User.id)
        .where(Skill.id == skill_id)
    )
    row = result.one_or_none()
    if row is None:
        raise AppError("skill_not_found", status_code=404)

    skill, user = row

    return AdminSkillDetail(
        id=skill.id,
        name=skill.name,
        description=skill.description,
        content=skill.content or "",
        script=skill.script,
        script_type=skill.script_type,
        visibility=skill.visibility,
        is_active=skill.is_active,
        status=skill.status,
        org_id=skill.org_id,
        user_id=skill.user_id,
        username=user.username if user else None,
        email=user.email if user else None,
        agent_count=0,
        created_at=skill.created_at.isoformat() if skill.created_at else "",
    )


@router.patch("/skills/{skill_id}/active")
async def toggle_skill_active(
    skill_id: str,
    body: SkillToggleRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> AdminSkillInfo:
    """Toggle skill active/visibility. Requires admin privileges."""
    result = await db.execute(
        select(Skill, User)
        .outerjoin(User, Skill.user_id == User.id)
        .where(Skill.id == skill_id)
    )
    row = result.one_or_none()
    if row is None:
        raise AppError("skill_not_found", status_code=404)

    skill, user = row
    skill.is_active = body.is_active
    await db.commit()

    action = "skill.admin_enable" if body.is_active else "skill.admin_disable"
    await write_audit(
        db,
        current_user,
        action,
        target_type="skill",
        target_id=skill_id,
        target_label=skill.name,
    )

    return AdminSkillInfo(
        id=skill.id,
        name=skill.name,
        description=skill.description,
        visibility=skill.visibility,
        is_active=skill.is_active,
        status=skill.status,
        user_id=skill.user_id,
        username=user.username if user else None,
        email=user.email if user else None,
        agent_count=0,
        created_at=skill.created_at.isoformat() if skill.created_at else "",
    )


@router.delete("/skills/{skill_id}", status_code=204)
async def admin_delete_skill(
    skill_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """Delete any skill by ID. Requires admin privileges."""
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if skill is None:
        raise AppError("skill_not_found", status_code=404)

    skill_name = skill.name
    await db.delete(skill)
    await db.commit()

    await write_audit(
        db,
        current_user,
        "skill.admin_delete",
        target_type="skill",
        target_id=skill_id,
        target_label=skill_name,
    )


@router.post("/skills/batch-delete", response_model=BatchOperationResponse)
async def batch_delete_skills(
    body: BatchSkillDeleteRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> BatchOperationResponse:
    """Batch-delete skills by IDs. Skips IDs that do not exist."""
    result = await db.execute(
        select(Skill).where(Skill.id.in_(body.ids))
    )
    skills = result.scalars().all()

    count = 0
    deleted_names: list[str] = []
    for s in skills:
        deleted_names.append(s.name)
        await db.delete(s)
        count += 1

    await db.commit()

    if count > 0:
        await write_audit(
            db,
            current_user,
            "skill.admin_batch_delete",
            target_type="skill",
            detail=f"Deleted {count} skill(s): {', '.join(deleted_names[:10])}",
        )

    return BatchOperationResponse(
        count=count,
        message=f"Deleted {count} skill(s)",
    )


@router.post("/skills/batch-toggle", response_model=BatchOperationResponse)
async def batch_toggle_skills(
    body: BatchSkillToggleRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> BatchOperationResponse:
    """Batch-set is_active for skills by IDs. Skips IDs that do not exist."""
    result = await db.execute(
        select(Skill).where(Skill.id.in_(body.ids))
    )
    skills = result.scalars().all()

    count = 0
    for s in skills:
        s.is_active = body.is_active
        count += 1

    await db.commit()

    if count > 0:
        await write_audit(
            db,
            current_user,
            "skill.admin_batch_toggle",
            target_type="skill",
            detail=f"Set is_active={body.is_active} for {count} skill(s)",
        )

    return BatchOperationResponse(
        count=count,
        message=f"Updated {count} skill(s) to is_active={body.is_active}",
    )

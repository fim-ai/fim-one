"""Skill CRUD endpoints with publish/unpublish lifecycle."""

from __future__ import annotations

import math
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.exceptions import AppError
from fim_one.web.auth import get_current_user, get_user_org_ids
from fim_one.web.platform import MARKET_ORG_ID, is_market_org
from fim_one.web.models import User
from fim_one.web.models.skill import Skill
from fim_one.web.models.resource_subscription import ResourceSubscription
from fim_one.web.schemas.common import ApiResponse, PaginatedResponse, PublishRequest
from fim_one.web.schemas.skill import SkillCreate, SkillForkRequest, SkillResponse, SkillUpdate
from fim_one.web.visibility import build_visibility_filter

router = APIRouter(prefix="/api/skills", tags=["skills"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _skill_to_response(skill: Skill) -> SkillResponse:
    return SkillResponse(
        id=skill.id,
        user_id=skill.user_id,
        name=skill.name,
        description=skill.description,
        content=skill.content,
        script=skill.script,
        script_type=skill.script_type,
        forked_from=getattr(skill, "forked_from", None),
        visibility=getattr(skill, "visibility", "personal"),
        org_id=getattr(skill, "org_id", None),
        is_active=skill.is_active,
        status=skill.status,
        publish_status=getattr(skill, "publish_status", None),
        published_at=(
            skill.published_at.isoformat()
            if skill.published_at
            else None
        ),
        reviewed_by=getattr(skill, "reviewed_by", None),
        reviewed_at=(
            skill.reviewed_at.isoformat()
            if skill.reviewed_at
            else None
        ),
        review_note=getattr(skill, "review_note", None),
        resource_refs=getattr(skill, "resource_refs", None),
        created_at=skill.created_at.isoformat() if skill.created_at else "",
        updated_at=skill.updated_at.isoformat() if skill.updated_at else None,
    )


async def _get_owned_skill(
    skill_id: str,
    user_id: str,
    db: AsyncSession,
) -> Skill:
    """Fetch a skill that the user owns."""
    result = await db.execute(
        select(Skill).where(Skill.id == skill_id, Skill.user_id == user_id)
    )
    skill = result.scalar_one_or_none()
    if skill is None:
        raise AppError("skill_not_found", status_code=404)
    return skill


async def _get_accessible_skill(
    skill_id: str,
    user_id: str,
    db: AsyncSession,
) -> Skill:
    """Fetch a skill the user owns, org-shared, or Market-installed."""
    from fim_one.web.visibility import resolve_visibility
    vis_filter, _, _ = await resolve_visibility(Skill, user_id, "skill", db)
    result = await db.execute(
        select(Skill).where(Skill.id == skill_id, vis_filter)
    )
    skill = result.scalar_one_or_none()
    if skill is None:
        raise AppError("skill_not_found", status_code=404)
    return skill


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=ApiResponse)
async def create_skill(
    body: SkillCreate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    skill = Skill(
        user_id=current_user.id,
        name=body.name,
        description=body.description,
        content=body.content,
        script=body.script,
        script_type=body.script_type,
        is_active=body.is_active,
        resource_refs=body.resource_refs,
        status="draft",
    )
    db.add(skill)
    await db.commit()
    result = await db.execute(select(Skill).where(Skill.id == skill.id))
    skill = result.scalar_one()
    return ApiResponse(data=_skill_to_response(skill).model_dump())


@router.get("", response_model=PaginatedResponse)
async def list_skills(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    user_org_ids = await get_user_org_ids(current_user.id, db)

    # Get subscribed skill IDs with org_id for source tagging
    sub_result = await db.execute(
        select(ResourceSubscription.resource_id, ResourceSubscription.org_id).where(
            ResourceSubscription.user_id == current_user.id,
            ResourceSubscription.resource_type == "skill",
        )
    )
    sub_rows = sub_result.all()
    subscribed_skill_ids = [r.resource_id for r in sub_rows]
    sub_org_map = {r.resource_id: r.org_id for r in sub_rows}

    base = select(Skill).where(
        build_visibility_filter(
            Skill, current_user.id, user_org_ids, subscribed_ids=subscribed_skill_ids
        ),
    )

    count_result = await db.execute(
        select(func.count()).select_from(base.subquery())
    )
    total = count_result.scalar_one()

    result = await db.execute(
        base.order_by(Skill.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    skills = result.scalars().all()

    subscribed_skill_ids_set = set(subscribed_skill_ids)
    items = []
    for s in skills:
        resp = _skill_to_response(s)
        if s.user_id == current_user.id:
            resp.source = "own"
        elif s.id in subscribed_skill_ids_set:
            sub_org_id = sub_org_map.get(s.id)
            resp.source = "market" if sub_org_id == MARKET_ORG_ID else "org"
        else:
            resp.source = "org"  # fallback, should not be reached
        items.append(resp.model_dump())

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total else 0,
    )


@router.get("/{skill_id}", response_model=ApiResponse)
async def get_skill(
    skill_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    skill = await _get_accessible_skill(skill_id, current_user.id, db)
    return ApiResponse(data=_skill_to_response(skill).model_dump())


@router.put("/{skill_id}", response_model=ApiResponse)
async def update_skill(
    skill_id: str,
    body: SkillUpdate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    skill = await _get_owned_skill(skill_id, current_user.id, db)

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(skill, field, value)

    content_changed = bool(update_data.keys() - {"is_active"})
    if content_changed:
        from fim_one.web.publish_review import check_edit_revert

        reverted = await check_edit_revert(skill, db)
    else:
        reverted = False

    await db.commit()
    result = await db.execute(select(Skill).where(Skill.id == skill.id))
    skill = result.scalar_one()
    data = _skill_to_response(skill).model_dump()
    if reverted:
        data["publish_status_reverted"] = True
    return ApiResponse(data=data)


@router.delete("/{skill_id}", response_model=ApiResponse)
async def delete_skill(
    skill_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    skill = await _get_owned_skill(skill_id, current_user.id, db)
    await db.delete(skill)
    await db.commit()
    return ApiResponse(data={"deleted": skill_id})


# ---------------------------------------------------------------------------
# Fork (clone)
# ---------------------------------------------------------------------------


@router.post("/{skill_id}/fork", response_model=ApiResponse)
async def fork_skill(
    skill_id: str,
    body: SkillForkRequest | None = None,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Clone an existing skill for the current user.

    Copies all content fields but NOT org_id or publish_status.
    The forked skill is set to personal/draft ownership.
    """
    # Fetch the source skill — must be visible to the user (own / org / global)
    source = await _get_accessible_skill(skill_id, current_user.id, db)

    fork_name = (body.name if body and body.name else f"{source.name} (Fork)")[:200]

    forked = Skill(
        user_id=current_user.id,
        name=fork_name,
        description=source.description,
        content=source.content,
        script=source.script,
        script_type=source.script_type,
        resource_refs=source.resource_refs,
        is_active=True,
        status="draft",
        forked_from=source.id,
        visibility="personal",
        org_id=None,
        publish_status=None,
    )
    db.add(forked)
    await db.commit()

    result = await db.execute(
        select(Skill).where(Skill.id == forked.id)
    )
    forked = result.scalar_one()
    return ApiResponse(data=_skill_to_response(forked).model_dump())


# ---------------------------------------------------------------------------
# Publish / Unpublish / Resubmit / Toggle
# ---------------------------------------------------------------------------


@router.post("/{skill_id}/publish", response_model=ApiResponse)
async def publish_skill(
    skill_id: str,
    body: PublishRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Publish skill to org or global scope."""
    skill = await _get_owned_skill(skill_id, current_user.id, db)

    if body.scope == "org":
        if not body.org_id:
            raise AppError("org_id_required", status_code=400)
        from fim_one.web.auth import require_org_member

        if not is_market_org(body.org_id):
            await require_org_member(body.org_id, current_user, db)
        skill.visibility = "org"
        skill.org_id = body.org_id
        from fim_one.web.publish_review import apply_publish_status

        await apply_publish_status(skill, body.org_id, db, resource_type="skill", publisher_id=current_user.id)
    elif body.scope == "global":
        if not current_user.is_admin:
            raise AppError("admin_required_for_global", status_code=403)
        skill.visibility = "global"
        skill.org_id = None
    else:
        raise AppError("invalid_scope", status_code=400)

    skill.published_at = datetime.now(UTC)

    # Audit log: submitted (org scope only)
    if body.scope == "org" and body.org_id:
        from fim_one.web.api.reviews import log_review_event

        await log_review_event(
            db=db,
            org_id=body.org_id,
            resource_type="skill",
            resource_id=skill.id,
            resource_name=skill.name,
            action="submitted",
            actor=current_user,
        )

    await db.commit()
    await db.refresh(skill)

    return ApiResponse(data=_skill_to_response(skill).model_dump())


@router.post("/{skill_id}/resubmit", response_model=ApiResponse)
async def resubmit_skill(
    skill_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Resubmit a rejected skill for review."""
    skill = await _get_owned_skill(skill_id, current_user.id, db)
    if skill.publish_status != "rejected":
        raise AppError("not_rejected", status_code=400)
    skill.publish_status = "pending_review"
    skill.reviewed_by = None
    skill.reviewed_at = None
    skill.review_note = None

    if skill.org_id:
        from fim_one.web.api.reviews import log_review_event

        await log_review_event(
            db=db,
            org_id=skill.org_id,
            resource_type="skill",
            resource_id=skill.id,
            resource_name=skill.name,
            action="resubmitted",
            actor=current_user,
        )

    await db.commit()
    await db.refresh(skill)
    return ApiResponse(data=_skill_to_response(skill).model_dump())


@router.post("/{skill_id}/unpublish", response_model=ApiResponse)
async def unpublish_skill(
    skill_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Revert skill to personal visibility."""
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if skill is None:
        raise AppError("skill_not_found", status_code=404)

    is_owner = skill.user_id == current_user.id
    is_admin = current_user.is_admin
    is_org_admin = False

    if skill.visibility == "org" and skill.org_id and not is_owner:
        try:
            from fim_one.web.auth import require_org_admin

            await require_org_admin(skill.org_id, current_user, db)
            is_org_admin = True
        except Exception:
            pass

    if not (is_owner or is_admin or is_org_admin):
        raise AppError("unpublish_denied", status_code=403)

    # Log BEFORE clearing org_id
    if skill.org_id:
        from fim_one.web.api.reviews import log_review_event

        await log_review_event(
            db=db,
            org_id=skill.org_id,
            resource_type="skill",
            resource_id=skill.id,
            resource_name=skill.name,
            action="unpublished",
            actor=current_user,
        )

    skill.visibility = "personal"
    skill.org_id = None
    skill.published_at = None
    skill.publish_status = None

    await db.commit()
    await db.refresh(skill)
    return ApiResponse(data=_skill_to_response(skill).model_dump())


@router.post("/{skill_id}/toggle", response_model=ApiResponse)
async def toggle_skill(
    skill_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Toggle skill is_active status."""
    skill = await _get_owned_skill(skill_id, current_user.id, db)
    skill.is_active = not skill.is_active
    await db.commit()
    await db.refresh(skill)
    return ApiResponse(data=_skill_to_response(skill).model_dump())

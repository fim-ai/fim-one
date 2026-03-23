"""Org-level publish review endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.auth import get_current_user, require_org_admin
from fim_one.web.exceptions import AppError
from fim_one.web.models.agent import Agent
from fim_one.web.models.connector import Connector
from fim_one.web.models.knowledge_base import KnowledgeBase
from fim_one.web.models.mcp_server import MCPServer
from fim_one.web.models.skill import Skill
from fim_one.web.models.workflow import Workflow
from fim_one.web.models.user import User
from fim_one.web.schemas.common import ApiResponse

router = APIRouter(prefix="/api/orgs/{org_id}/reviews", tags=["reviews"])

# ---------------------------------------------------------------------------
# Resource type registry
# ---------------------------------------------------------------------------

RESOURCE_MODELS: dict[str, Any] = {
    "agent": Agent,
    "connector": Connector,
    "knowledge_base": KnowledgeBase,
    "mcp_server": MCPServer,
    "skill": Skill,
    "workflow": Workflow,
}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ReviewActionRequest(BaseModel):
    resource_type: str
    resource_id: str
    note: str | None = None


class ReviewItem(BaseModel):
    resource_type: str
    resource_id: str
    resource_name: str
    owner_id: str | None = None
    owner_username: str | None = None
    submitted_at: str | None = None
    publish_status: str | None = None
    review_note: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_model(resource_type: str) -> Any:
    """Resolve resource type string to ORM model class."""
    model = RESOURCE_MODELS.get(resource_type)
    if model is None:
        raise AppError(
            "invalid_resource_type",
            status_code=400,
            detail=f"Unknown resource type: {resource_type}",
        )
    return model


async def _check_review_permission(
    org_id: str, current_user: User, db: AsyncSession
) -> None:
    """Verify user is org admin+ or system admin."""
    if current_user.is_admin:
        return
    await require_org_admin(org_id, current_user, db)


async def log_review_event(
    db: AsyncSession,
    org_id: str,
    resource_type: str,
    resource_id: str,
    resource_name: str,
    action: str,
    actor: User | None = None,
    note: str | None = None,
) -> None:
    """Append a review audit log entry.  Caller must commit after calling this."""
    from fim_one.web.models.review_log import ReviewLog

    entry = ReviewLog(
        org_id=org_id,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=resource_name,
        action=action,
        actor_id=actor.id if actor else None,
        actor_username=actor.username if actor else None,
        note=note,
    )
    db.add(entry)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/log", response_model=ApiResponse)
async def list_review_log(
    org_id: str,
    resource_type: str | None = Query(None),
    resource_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Return the review audit trail for an org (newest first)."""
    await _check_review_permission(org_id, current_user, db)

    from fim_one.web.models.review_log import ReviewLog

    query = select(ReviewLog).where(ReviewLog.org_id == org_id)
    if resource_type:
        query = query.where(ReviewLog.resource_type == resource_type)
    if resource_id:
        query = query.where(ReviewLog.resource_id == resource_id)
    query = query.order_by(ReviewLog.created_at.desc()).limit(limit)

    result = await db.execute(query)
    logs = result.scalars().all()

    return ApiResponse(
        data=[
            {
                "id": log.id,
                "org_id": log.org_id,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "resource_name": log.resource_name,
                "action": log.action,
                "actor_id": log.actor_id,
                "actor_username": log.actor_username,
                "note": log.note,
                "created_at": (
                    log.created_at.isoformat() if log.created_at else None
                ),
            }
            for log in logs
        ]
    )


@router.get("", response_model=ApiResponse)
async def list_reviews(
    org_id: str,
    resource_type: str | None = Query(None),
    status: str | None = Query(None),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """List pending reviews for an org."""
    await _check_review_permission(org_id, current_user, db)

    models_to_query = (
        {resource_type: _get_model(resource_type)}
        if resource_type
        else RESOURCE_MODELS
    )

    items: list[dict[str, object]] = []
    for rtype, model in models_to_query.items():
        query = select(model).where(model.org_id == org_id)
        if status:
            query = query.where(model.publish_status == status)
        result = await db.execute(query)
        resources = result.scalars().all()

        for r in resources:
            # Try to get owner username
            owner_username = None
            owner_id = getattr(r, "user_id", None)
            if owner_id:
                from fim_one.web.models.user import User as UserModel

                user_result = await db.execute(
                    select(UserModel.username).where(UserModel.id == owner_id)
                )
                owner_username = user_result.scalar_one_or_none()

            items.append(
                ReviewItem(
                    resource_type=rtype,
                    resource_id=r.id,
                    resource_name=r.name,
                    owner_id=owner_id,
                    owner_username=owner_username,
                    submitted_at=r.updated_at.isoformat() if r.updated_at else (
                        r.created_at.isoformat() if r.created_at else None
                    ),
                    publish_status=r.publish_status,
                    review_note=r.review_note,
                ).model_dump()
            )

    return ApiResponse(data=items)


@router.post("/approve", response_model=ApiResponse)
async def approve_resource(
    org_id: str,
    body: ReviewActionRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Approve a resource for publication in the org."""
    await _check_review_permission(org_id, current_user, db)

    model = _get_model(body.resource_type)
    result = await db.execute(
        select(model).where(model.id == body.resource_id, model.org_id == org_id)
    )
    resource = result.scalar_one_or_none()
    if resource is None:
        raise AppError("resource_not_found", status_code=404)

    if resource.publish_status != "pending_review":
        raise AppError(
            "not_pending_review",
            status_code=400,
            detail="Resource is not pending review",
        )

    resource.publish_status = "approved"
    resource.reviewed_by = current_user.id
    resource.reviewed_at = datetime.now(UTC)
    if body.note:
        resource.review_note = body.note

    await log_review_event(
        db=db,
        org_id=org_id,
        resource_type=body.resource_type,
        resource_id=body.resource_id,
        resource_name=resource.name,
        action="approved",
        actor=current_user,
        note=body.note,
    )

    await db.commit()
    return ApiResponse(
        data={
            "resource_type": body.resource_type,
            "resource_id": body.resource_id,
            "publish_status": "approved",
        }
    )


@router.post("/reject", response_model=ApiResponse)
async def reject_resource(
    org_id: str,
    body: ReviewActionRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Reject a resource publication in the org."""
    await _check_review_permission(org_id, current_user, db)

    model = _get_model(body.resource_type)
    result = await db.execute(
        select(model).where(model.id == body.resource_id, model.org_id == org_id)
    )
    resource = result.scalar_one_or_none()
    if resource is None:
        raise AppError("resource_not_found", status_code=404)

    if resource.publish_status != "pending_review":
        raise AppError(
            "not_pending_review",
            status_code=400,
            detail="Resource is not pending review",
        )

    resource.publish_status = "rejected"
    resource.reviewed_by = current_user.id
    resource.reviewed_at = datetime.now(UTC)
    resource.review_note = body.note

    await log_review_event(
        db=db,
        org_id=org_id,
        resource_type=body.resource_type,
        resource_id=body.resource_id,
        resource_name=resource.name,
        action="rejected",
        actor=current_user,
        note=body.note,
    )

    await db.commit()
    return ApiResponse(
        data={
            "resource_type": body.resource_type,
            "resource_id": body.resource_id,
            "publish_status": "rejected",
        }
    )

"""Admin endpoints for centralized cross-org review queue management."""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.auth import get_current_admin
from fim_one.web.exceptions import AppError
from fim_one.web.models import Agent, Connector, KnowledgeBase, MCPServer as MCPServerModel, Organization, User
from fim_one.web.models.skill import Skill
from fim_one.web.models.workflow import Workflow
from fim_one.web.models.review_log import ReviewLog
from fim_one.web.schemas.common import ApiResponse

from fim_one.web.api.admin_utils import write_audit
from fim_one.web.api.reviews import log_review_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Resource models that support publish_status
REVIEWABLE_MODELS: dict[str, Any] = {
    "agent": Agent,
    "connector": Connector,
    "knowledge_base": KnowledgeBase,
    "mcp_server": MCPServerModel,
    "skill": Skill,
    "workflow": Workflow,
}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PendingReviewItem(BaseModel):
    resource_type: str
    resource_id: str
    resource_name: str
    org_id: str | None = None
    org_name: str | None = None
    owner_id: str | None = None
    owner_username: str | None = None
    submitted_at: str | None = None
    publish_status: str | None = None


class BatchReviewRequest(BaseModel):
    """Batch approve/reject by resource type + ID pairs."""
    items: list[BatchReviewItem] = Field(..., min_length=1, max_length=100)
    reason: str | None = None


class BatchReviewItem(BaseModel):
    resource_type: str
    resource_id: str


class BatchReviewResponse(BaseModel):
    success_count: int
    failed_count: int
    errors: list[str] = Field(default_factory=list)


class ReviewStatsResponse(BaseModel):
    total_pending: int
    pending_by_org: list[OrgPendingCount] = Field(default_factory=list)
    avg_review_time_hours: float | None = None
    approval_rate: float | None = None


class OrgPendingCount(BaseModel):
    org_id: str
    org_name: str
    pending_count: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/reviews/pending", response_model=ApiResponse)
async def list_pending_reviews(
    resource_type: str | None = Query(None),
    org_id: str | None = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Centralized cross-org pending review queue. Requires admin privileges."""
    models_to_query = (
        {resource_type: REVIEWABLE_MODELS[resource_type]}
        if resource_type and resource_type in REVIEWABLE_MODELS
        else REVIEWABLE_MODELS
    )

    # Cache org names
    org_cache: dict[str, str] = {}
    user_cache: dict[str, str | None] = {}

    all_items: list[dict[str, object]] = []

    for rtype, model in models_to_query.items():
        query = select(model).where(model.publish_status == "pending_review")
        if org_id:
            query = query.where(model.org_id == org_id)

        result = await db.execute(query)
        resources = result.scalars().all()

        for r in resources:
            # Resolve org name
            r_org_id = getattr(r, "org_id", None)
            org_name = None
            if r_org_id:
                if r_org_id not in org_cache:
                    org_result = await db.execute(
                        select(Organization.name).where(Organization.id == r_org_id)
                    )
                    org_cache[r_org_id] = org_result.scalar_one_or_none() or "Unknown"
                org_name = org_cache[r_org_id]

            # Resolve owner username
            owner_id = getattr(r, "user_id", None)
            owner_username = None
            if owner_id:
                if owner_id not in user_cache:
                    user_result = await db.execute(
                        select(User.username).where(User.id == owner_id)
                    )
                    user_cache[owner_id] = user_result.scalar_one_or_none()
                owner_username = user_cache[owner_id]

            submitted_at = None
            if hasattr(r, "updated_at") and r.updated_at:
                submitted_at = r.updated_at.isoformat()
            elif hasattr(r, "created_at") and r.created_at:
                submitted_at = r.created_at.isoformat()

            all_items.append(
                PendingReviewItem(
                    resource_type=rtype,
                    resource_id=r.id,
                    resource_name=r.name,
                    org_id=r_org_id,
                    org_name=org_name,
                    owner_id=owner_id,
                    owner_username=owner_username,
                    submitted_at=submitted_at,
                    publish_status=r.publish_status,
                ).model_dump()
            )

    # Sort by submitted_at descending
    all_items.sort(key=lambda x: str(x.get("submitted_at") or ""), reverse=True)

    # Paginate
    total = len(all_items)
    offset = (page - 1) * size
    paginated = all_items[offset : offset + size]

    return ApiResponse(data={"items": paginated, "total": total, "page": page, "size": size})


@router.post("/reviews/batch-approve", response_model=BatchReviewResponse)
async def batch_approve_reviews(
    body: BatchReviewRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> BatchReviewResponse:
    """Batch approve resources. Requires admin privileges."""
    success_count = 0
    failed_count = 0
    errors: list[str] = []

    for item in body.items:
        model = REVIEWABLE_MODELS.get(item.resource_type)
        if model is None:
            errors.append(f"Unknown resource type: {item.resource_type}")
            failed_count += 1
            continue

        result = await db.execute(
            select(model).where(model.id == item.resource_id)
        )
        resource = result.scalar_one_or_none()
        if resource is None:
            errors.append(f"{item.resource_type}/{item.resource_id}: not found")
            failed_count += 1
            continue

        if resource.publish_status != "pending_review":
            errors.append(f"{item.resource_type}/{item.resource_id}: not pending review")
            failed_count += 1
            continue

        resource.publish_status = "approved"
        resource.reviewed_by = current_user.id
        resource.reviewed_at = datetime.now(UTC)
        if body.reason:
            resource.review_note = body.reason

        org_id = getattr(resource, "org_id", None)
        if org_id:
            await log_review_event(
                db=db,
                org_id=org_id,
                resource_type=item.resource_type,
                resource_id=item.resource_id,
                resource_name=resource.name,
                action="approved",
                actor=current_user,
                note=body.reason,
            )

        success_count += 1

    await db.commit()

    if success_count > 0:
        await write_audit(
            db,
            current_user,
            "review.admin_batch_approve",
            detail=f"Approved {success_count} resource(s), failed {failed_count}",
        )

    return BatchReviewResponse(
        success_count=success_count,
        failed_count=failed_count,
        errors=errors,
    )


@router.post("/reviews/batch-reject", response_model=BatchReviewResponse)
async def batch_reject_reviews(
    body: BatchReviewRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> BatchReviewResponse:
    """Batch reject resources. Requires admin privileges."""
    success_count = 0
    failed_count = 0
    errors: list[str] = []

    for item in body.items:
        model = REVIEWABLE_MODELS.get(item.resource_type)
        if model is None:
            errors.append(f"Unknown resource type: {item.resource_type}")
            failed_count += 1
            continue

        result = await db.execute(
            select(model).where(model.id == item.resource_id)
        )
        resource = result.scalar_one_or_none()
        if resource is None:
            errors.append(f"{item.resource_type}/{item.resource_id}: not found")
            failed_count += 1
            continue

        if resource.publish_status != "pending_review":
            errors.append(f"{item.resource_type}/{item.resource_id}: not pending review")
            failed_count += 1
            continue

        resource.publish_status = "rejected"
        resource.reviewed_by = current_user.id
        resource.reviewed_at = datetime.now(UTC)
        resource.review_note = body.reason

        org_id = getattr(resource, "org_id", None)
        if org_id:
            await log_review_event(
                db=db,
                org_id=org_id,
                resource_type=item.resource_type,
                resource_id=item.resource_id,
                resource_name=resource.name,
                action="rejected",
                actor=current_user,
                note=body.reason,
            )

        success_count += 1

    await db.commit()

    if success_count > 0:
        await write_audit(
            db,
            current_user,
            "review.admin_batch_reject",
            detail=f"Rejected {success_count} resource(s), failed {failed_count}",
        )

    return BatchReviewResponse(
        success_count=success_count,
        failed_count=failed_count,
        errors=errors,
    )


@router.get("/reviews/stats", response_model=ReviewStatsResponse)
async def review_stats(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ReviewStatsResponse:
    """Review pipeline stats. Requires admin privileges."""
    # Count total pending across all resource types
    total_pending = 0
    org_pending: dict[str, int] = {}

    for rtype, model in REVIEWABLE_MODELS.items():
        result = await db.execute(
            select(model.org_id, func.count(model.id))
            .where(model.publish_status == "pending_review")
            .group_by(model.org_id)
        )
        for org_id, count in result.all():
            if org_id:
                org_pending[org_id] = org_pending.get(org_id, 0) + count
            total_pending += count

    # Resolve org names
    pending_by_org: list[OrgPendingCount] = []
    if org_pending:
        org_result = await db.execute(
            select(Organization.id, Organization.name).where(
                Organization.id.in_(list(org_pending.keys()))
            )
        )
        org_names = {row[0]: row[1] for row in org_result.all()}
        for org_id, count in org_pending.items():
            pending_by_org.append(
                OrgPendingCount(
                    org_id=org_id,
                    org_name=org_names.get(org_id, "Unknown"),
                    pending_count=count,
                )
            )

    # Avg review time and approval rate from ReviewLog
    # Review time: diff between submit and approve/reject entries
    # Approximate: avg time between consecutive log entries for a resource
    review_log_stats = await db.execute(
        select(
            func.count(ReviewLog.id).label("total_reviews"),
            func.sum(
                case(
                    (ReviewLog.action == "approved", 1),
                    else_=0,
                )
            ).label("approved_count"),
        ).where(ReviewLog.action.in_(["approved", "rejected"]))
    )
    row = review_log_stats.one()
    total_reviews = row.total_reviews or 0
    approved_count = row.approved_count or 0

    approval_rate = None
    if total_reviews > 0:
        approval_rate = round((approved_count / total_reviews) * 100, 1)

    return ReviewStatsResponse(
        total_pending=total_pending,
        pending_by_org=pending_by_org,
        avg_review_time_hours=None,  # Would require paired submit/review timestamps
        approval_rate=approval_rate,
    )

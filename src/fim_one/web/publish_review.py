"""Shared publish review logic for all resource types.

When an organization has ``require_publish_review`` enabled, resources
published to that org enter a ``pending_review`` state instead of being
immediately visible to other members.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.web.models.organization import Organization


async def get_org_requires_review(org_id: str, db: AsyncSession) -> bool:
    """Check if an org requires publish review."""
    result = await db.execute(
        select(Organization.require_publish_review).where(Organization.id == org_id)
    )
    val = result.scalar_one_or_none()
    return bool(val)


async def apply_publish_status(resource, org_id: str, db: AsyncSession) -> None:
    """Set publish_status based on org review setting. Call during publish."""
    if await get_org_requires_review(org_id, db):
        resource.publish_status = "pending_review"
    else:
        resource.publish_status = None  # no review needed


async def check_edit_revert(resource, db: AsyncSession) -> bool:
    """Auto-revert publish_status on edit if resource is in a review-required org
    and currently approved.

    Returns True if status was reverted (caller may want to include this in response).
    Does NOT revert if status is 'rejected' (user must manually resubmit).
    """
    if getattr(resource, "visibility", None) != "org" or not getattr(resource, "org_id", None):
        return False
    if getattr(resource, "publish_status", None) != "approved":
        return False
    if not await get_org_requires_review(resource.org_id, db):
        return False
    # Auto-revert
    resource.publish_status = "pending_review"
    resource.reviewed_by = None
    resource.reviewed_at = None
    resource.review_note = None
    return True

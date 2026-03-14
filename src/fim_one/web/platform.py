"""Platform organisation helpers — the built-in global org."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.web.models.organization import OrgMembership, Organization

PLATFORM_ORG_SLUG = "platform"
PLATFORM_ORG_ID = "00000000-0000-0000-0000-000000000001"

# Shadow Market alias — same org, new semantic name.
# The "Platform" org is now the global Market: nobody joins it,
# resources are discovered via browse_market and accessed via
# ResourceSubscription.  Keep PLATFORM_ORG_ID for migration compat.
MARKET_ORG_ID = PLATFORM_ORG_ID


async def ensure_platform_org(db: AsyncSession, owner_id: str) -> str:
    """Create the Platform org if it doesn't exist. Return its ID."""
    result = await db.execute(
        select(Organization).where(Organization.id == PLATFORM_ORG_ID)
    )
    org = result.scalar_one_or_none()
    if org is None:
        org = Organization(
            id=PLATFORM_ORG_ID,
            name="Platform",
            slug=PLATFORM_ORG_SLUG,
            description="Default platform-wide organization. All users are members.",
            owner_id=owner_id,
            review_agents=True,
            review_connectors=True,
            review_kbs=True,
            review_mcp_servers=True,
            review_workflows=True,
        )
        db.add(org)
        await db.flush()
        # Add owner as owner member
        await ensure_platform_membership(db, owner_id, role="owner")
    return PLATFORM_ORG_ID


async def ensure_platform_membership(
    db: AsyncSession, user_id: str, role: str = "member"
) -> None:
    """Add user to Platform org if not already a member (idempotent)."""
    existing = await db.execute(
        select(OrgMembership).where(
            OrgMembership.org_id == PLATFORM_ORG_ID,
            OrgMembership.user_id == user_id,
        )
    )
    if existing.scalar_one_or_none() is None:
        db.add(OrgMembership(org_id=PLATFORM_ORG_ID, user_id=user_id, role=role))

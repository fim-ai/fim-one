"""Organization management API — CRUD for orgs and member management."""

from __future__ import annotations

import math
import re
import secrets

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fim_one.db import get_session
from fim_one.web.auth import (
    get_current_admin,
    get_current_user,
    require_org_admin,
    require_org_member,
    require_org_owner,
)
from fim_one.web.exceptions import AppError
from fim_one.web.models.organization import OrgMembership, Organization
from fim_one.web.models.user import User
from fim_one.web.platform import MARKET_ORG_ID, is_market_org
from fim_one.web.schemas.common import ApiResponse, PaginatedResponse

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/orgs", tags=["organizations"])
admin_router = APIRouter(prefix="/api/admin/organizations", tags=["admin"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class OrgCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    icon: str | None = None
    slug: str | None = Field(None, max_length=100)


class OrgUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    icon: str | None = None
    slug: str | None = Field(None, max_length=100)
    is_active: bool | None = None
    settings: dict[str, object] | None = None
    review_agents: bool | None = None
    review_connectors: bool | None = None
    review_kbs: bool | None = None
    review_mcp_servers: bool | None = None
    review_workflows: bool | None = None
    review_skills: bool | None = None


class MemberAdd(BaseModel):
    username: str | None = None
    email: str | None = None
    role: str = Field("member", pattern=r"^(admin|member)$")


class MemberRoleUpdate(BaseModel):
    role: str = Field(..., pattern=r"^(admin|member)$")


class OrgResponse(BaseModel):
    id: str
    name: str
    slug: str
    description: str | None = None
    icon: str | None = None
    owner_id: str
    parent_id: str | None = None
    settings: dict[str, object] | None = None
    is_active: bool
    review_agents: bool = False
    review_connectors: bool = False
    review_kbs: bool = False
    review_mcp_servers: bool = False
    review_workflows: bool = False
    review_skills: bool = False
    created_at: str
    updated_at: str | None = None


class OrgWithRoleResponse(OrgResponse):
    """Org detail plus the requesting user's role in it."""
    role: str
    member_count: int = 0


class MemberResponse(BaseModel):
    id: str  # membership id
    user_id: str
    username: str | None = None
    email: str
    display_name: str | None = None
    avatar: str | None = None
    role: str
    invited_by: str | None = None
    created_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    """Generate a URL-safe slug from a name."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "org"


def _org_to_response(org: Organization) -> OrgResponse:
    return OrgResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        description=org.description,
        icon=org.icon,
        owner_id=org.owner_id,
        parent_id=org.parent_id,
        settings=org.settings,
        is_active=org.is_active,
        review_agents=getattr(org, "review_agents", False),
        review_connectors=getattr(org, "review_connectors", False),
        review_kbs=getattr(org, "review_kbs", False),
        review_mcp_servers=getattr(org, "review_mcp_servers", False),
        review_workflows=getattr(org, "review_workflows", False),
        review_skills=getattr(org, "review_skills", False),
        created_at=org.created_at.isoformat() if org.created_at else "",
        updated_at=org.updated_at.isoformat() if org.updated_at else None,
    )


def _org_with_role(org: Organization, role: str, member_count: int = 0) -> OrgWithRoleResponse:
    return OrgWithRoleResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        description=org.description,
        icon=org.icon,
        owner_id=org.owner_id,
        parent_id=org.parent_id,
        settings=org.settings,
        is_active=org.is_active,
        review_agents=getattr(org, "review_agents", False),
        review_connectors=getattr(org, "review_connectors", False),
        review_kbs=getattr(org, "review_kbs", False),
        review_mcp_servers=getattr(org, "review_mcp_servers", False),
        review_workflows=getattr(org, "review_workflows", False),
        review_skills=getattr(org, "review_skills", False),
        created_at=org.created_at.isoformat() if org.created_at else "",
        updated_at=org.updated_at.isoformat() if org.updated_at else None,
        role=role,
        member_count=member_count,
    )


async def _membership_to_response(
    membership: OrgMembership, db: AsyncSession
) -> MemberResponse:
    """Build a MemberResponse, loading the user if needed."""
    result = await db.execute(select(User).where(User.id == membership.user_id))
    user = result.scalar_one_or_none()
    return MemberResponse(
        id=membership.id,
        user_id=membership.user_id,
        username=user.username if user else None,
        email=user.email if user else "",
        display_name=user.display_name if user else None,
        avatar=user.avatar if user else None,
        role=membership.role,
        invited_by=membership.invited_by,
        created_at=membership.created_at.isoformat() if membership.created_at else "",
    )


async def _ensure_unique_slug(slug: str, db: AsyncSession, exclude_id: str | None = None) -> str:
    """Return *slug* if it is unique, otherwise append a random suffix."""
    query = select(Organization.id).where(Organization.slug == slug)
    if exclude_id:
        query = query.where(Organization.id != exclude_id)
    result = await db.execute(query)
    if result.scalar_one_or_none() is None:
        return slug
    # Slug collision — append random suffix
    for _ in range(10):
        candidate = f"{slug}-{secrets.token_hex(3)}"
        result = await db.execute(
            select(Organization.id).where(Organization.slug == candidate)
        )
        if result.scalar_one_or_none() is None:
            return candidate
    # Extremely unlikely fallback
    return f"{slug}-{secrets.token_hex(6)}"


# ---------------------------------------------------------------------------
# Organization CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=ApiResponse)
async def create_org(
    body: OrgCreate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Create an organization. The creator becomes the owner."""
    slug = body.slug if body.slug else _slugify(body.name)
    slug = await _ensure_unique_slug(slug, db)

    org = Organization(
        name=body.name,
        slug=slug,
        description=body.description,
        icon=body.icon,
        owner_id=current_user.id,
    )
    db.add(org)
    await db.flush()

    # Auto-add the creator as owner member
    membership = OrgMembership(
        org_id=org.id,
        user_id=current_user.id,
        role="owner",
    )
    db.add(membership)
    await db.commit()

    return ApiResponse(data=_org_with_role(org, "owner", member_count=1).model_dump())


@router.get("", response_model=ApiResponse)
async def list_my_orgs(
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """List all organizations the current user belongs to, with role."""
    result = await db.execute(
        select(OrgMembership)
        .where(OrgMembership.user_id == current_user.id)
        .order_by(OrgMembership.created_at.desc())
    )
    memberships = result.scalars().all()

    if not memberships:
        return ApiResponse(data=[])

    org_ids = [m.org_id for m in memberships]
    org_result = await db.execute(
        select(Organization).where(Organization.id.in_(org_ids))
    )
    orgs_by_id = {o.id: o for o in org_result.scalars().all()}

    # Count memberships per org in one query
    count_result = await db.execute(
        select(OrgMembership.org_id, func.count(OrgMembership.id).label("cnt"))
        .where(OrgMembership.org_id.in_(org_ids))
        .group_by(OrgMembership.org_id)
    )
    counts_by_org = {row.org_id: row.cnt for row in count_result.all()}

    items = []
    for m in memberships:
        org = orgs_by_id.get(m.org_id)
        if not org:
            continue
        # Market org is a shadow org (the global marketplace) — never show
        # it in the user's org list; it is accessed via the Marketplace tab.
        if org.id == MARKET_ORG_ID:
            continue
        count = counts_by_org.get(m.org_id, 0)
        items.append(_org_with_role(org, m.role, count).model_dump())

    return ApiResponse(data=items)


@router.get("/{org_id}", response_model=ApiResponse)
async def get_org(
    org_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Get organization detail. Must be a member."""
    membership = await require_org_member(org_id, current_user, db)

    result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        raise AppError("org_not_found", status_code=404)

    # Hide member count for Market org (prevents leaking total user count)
    if org_id == MARKET_ORG_ID:
        member_count = 0
    else:
        count_result = await db.execute(
            select(func.count(OrgMembership.id)).where(OrgMembership.org_id == org_id)
        )
        member_count = count_result.scalar_one()
    return ApiResponse(data=_org_with_role(org, membership.role, member_count).model_dump())


@router.put("/{org_id}", response_model=ApiResponse)
async def update_org(
    org_id: str,
    body: OrgUpdate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Update an organization. Requires admin+ role."""
    membership = await require_org_admin(org_id, current_user, db)

    result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        raise AppError("org_not_found", status_code=404)

    update_data = body.model_dump(exclude_unset=True)

    # Market org review settings are locked — always require review
    if is_market_org(org_id):
        for key in (
            "review_agents", "review_connectors", "review_kbs",
            "review_mcp_servers", "review_workflows", "review_skills",
        ):
            update_data.pop(key, None)

    # Validate slug uniqueness if changing
    if "slug" in update_data and update_data["slug"] is not None:
        update_data["slug"] = await _ensure_unique_slug(
            update_data["slug"], db, exclude_id=org_id
        )

    for field, value in update_data.items():
        setattr(org, field, value)

    await db.commit()

    # Re-fetch for fresh timestamps
    result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = result.scalar_one()
    count_result = await db.execute(
        select(func.count(OrgMembership.id)).where(OrgMembership.org_id == org_id)
    )
    member_count = count_result.scalar_one()
    return ApiResponse(data=_org_with_role(org, membership.role, member_count).model_dump())


@router.delete("/{org_id}", response_model=ApiResponse)
async def delete_org(
    org_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Delete an organization. Owner only."""

    if org_id == MARKET_ORG_ID:
        raise AppError("cannot_delete_market_org", status_code=403)
    await require_org_owner(org_id, current_user, db)

    result = await db.execute(
        select(Organization)
        .options(selectinload(Organization.memberships))
        .where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        raise AppError("org_not_found", status_code=404)

    await db.delete(org)
    await db.commit()
    return ApiResponse(data={"deleted": org_id})


# ---------------------------------------------------------------------------
# Member management
# ---------------------------------------------------------------------------


@router.get("/{org_id}/members", response_model=ApiResponse)
async def list_members(
    org_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """List all members of an organization. Must be a member."""
    await require_org_member(org_id, current_user, db)

    result = await db.execute(
        select(OrgMembership)
        .where(OrgMembership.org_id == org_id)
        .order_by(OrgMembership.created_at.asc())
    )
    memberships = result.scalars().all()

    items = []
    for m in memberships:
        items.append((await _membership_to_response(m, db)).model_dump())

    return ApiResponse(data=items)


@router.post("/{org_id}/members", response_model=ApiResponse)
async def add_member(
    org_id: str,
    body: MemberAdd,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Add a member by username or email. Requires admin+ role."""
    if is_market_org(org_id):
        raise AppError("cannot_add_member_to_market_org", status_code=400)

    await require_org_admin(org_id, current_user, db)

    # Verify the org exists
    org_result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    if org_result.scalar_one_or_none() is None:
        raise AppError("org_not_found", status_code=404)

    # Look up the target user
    if not body.username and not body.email:
        raise AppError(
            "username_or_email_required",
            status_code=400,
            detail="Must provide username or email",
        )

    conditions = []
    if body.username:
        conditions.append(User.username == body.username)
    if body.email:
        conditions.append(User.email == body.email)

    user_result = await db.execute(select(User).where(or_(*conditions)))
    target_user = user_result.scalar_one_or_none()
    if target_user is None:
        raise AppError("user_not_found", status_code=404)

    # Check if already a member
    existing = await db.execute(
        select(OrgMembership).where(
            OrgMembership.org_id == org_id,
            OrgMembership.user_id == target_user.id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise AppError(
            "already_member",
            status_code=409,
            detail="User is already a member of this organization",
        )

    membership = OrgMembership(
        org_id=org_id,
        user_id=target_user.id,
        role=body.role,
        invited_by=current_user.id,
    )
    db.add(membership)
    await db.commit()

    # Re-fetch for created_at
    result = await db.execute(
        select(OrgMembership).where(OrgMembership.id == membership.id)
    )
    membership = result.scalar_one()
    return ApiResponse(
        data=(await _membership_to_response(membership, db)).model_dump()
    )


@router.patch("/{org_id}/members/{user_id}", response_model=ApiResponse)
async def update_member_role(
    org_id: str,
    user_id: str,
    body: MemberRoleUpdate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Update a member's role. Owner only. Cannot change own role."""
    await require_org_owner(org_id, current_user, db)

    if user_id == current_user.id:
        raise AppError(
            "cannot_change_own_role",
            status_code=400,
            detail="Cannot change your own role",
        )

    result = await db.execute(
        select(OrgMembership).where(
            OrgMembership.org_id == org_id,
            OrgMembership.user_id == user_id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise AppError("membership_not_found", status_code=404)

    if membership.role == "owner":
        raise AppError(
            "cannot_change_owner_role",
            status_code=400,
            detail="Cannot change the owner's role. Use ownership transfer instead.",
        )

    membership.role = body.role
    await db.commit()

    result = await db.execute(
        select(OrgMembership).where(OrgMembership.id == membership.id)
    )
    membership = result.scalar_one()
    return ApiResponse(
        data=(await _membership_to_response(membership, db)).model_dump()
    )


@router.delete("/{org_id}/members/{user_id}", response_model=ApiResponse)
async def remove_member(
    org_id: str,
    user_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Remove a member or self-leave.

    - Self-leave: anyone can leave (owner must transfer ownership first).
    - Remove others: admin+ can remove members (not other admins/owner).
    """
    is_self = user_id == current_user.id

    if org_id == MARKET_ORG_ID and is_self:
        raise AppError("cannot_leave_market_org", status_code=403)

    if not is_self:
        # Must be admin+ to remove others
        actor_membership = await require_org_admin(org_id, current_user, db)
    else:
        # Self-leave — just need to be a member
        actor_membership = await require_org_member(org_id, current_user, db)

    # Fetch the target membership
    result = await db.execute(
        select(OrgMembership).where(
            OrgMembership.org_id == org_id,
            OrgMembership.user_id == user_id,
        )
    )
    target_membership = result.scalar_one_or_none()
    if target_membership is None:
        raise AppError("membership_not_found", status_code=404)

    # Owners cannot leave without transferring ownership
    if target_membership.role == "owner" and is_self:
        raise AppError(
            "owner_cannot_leave",
            status_code=400,
            detail="Transfer ownership before leaving the organization",
        )

    # Admins cannot remove other admins or the owner
    if not is_self and target_membership.role in ("admin", "owner"):
        if actor_membership.role != "owner":
            raise AppError(
                "cannot_remove_admin",
                status_code=403,
                detail="Only the owner can remove admins",
            )

    await db.delete(target_membership)
    await db.commit()
    return ApiResponse(data={"removed": user_id, "org_id": org_id})


# ---------------------------------------------------------------------------
# Admin endpoints (system admins)
# ---------------------------------------------------------------------------


@admin_router.get("", response_model=PaginatedResponse)
async def admin_list_orgs(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
    search: str | None = Query(None, alias="q"),
    _admin: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    """List all organizations (paginated). Admin only."""
    base = select(Organization)
    if search:
        pattern = f"%{search}%"
        base = base.where(
            or_(Organization.name.ilike(pattern), Organization.slug.ilike(pattern))
        )

    count_result = await db.execute(
        select(func.count()).select_from(base.subquery())
    )
    total = count_result.scalar_one()

    result = await db.execute(
        base.order_by(Organization.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    orgs = result.scalars().all()

    if not orgs:
        return PaginatedResponse(items=[], total=total, page=page, size=size, pages=0)

    # Fetch owner info
    owner_ids = list({o.owner_id for o in orgs if o.owner_id})
    owners_by_id: dict[str, User] = {}
    if owner_ids:
        owner_result = await db.execute(
            select(User).where(User.id.in_(owner_ids))
        )
        owners_by_id = {u.id: u for u in owner_result.scalars().all()}

    # Count members per org
    org_ids = [o.id for o in orgs]
    member_counts: dict[str, int] = {}
    if org_ids:
        cnt_result = await db.execute(
            select(OrgMembership.org_id, func.count(OrgMembership.id).label("cnt"))
            .where(OrgMembership.org_id.in_(org_ids))
            .group_by(OrgMembership.org_id)
        )
        member_counts = {row.org_id: row.cnt for row in cnt_result.all()}

    items = []
    for o in orgs:
        owner = owners_by_id.get(o.owner_id)
        d = _org_to_response(o).model_dump()
        d["owner_username"] = owner.username if owner else None
        d["owner_email"] = owner.email if owner else ""
        d["member_count"] = member_counts.get(o.id, 0)
        items.append(d)

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total else 0,
    )


@admin_router.put("/{org_id}", response_model=ApiResponse)
async def admin_update_org(
    org_id: str,
    body: OrgUpdate,
    _admin: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Update any organization. Admin only."""
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if org is None:
        raise AppError("org_not_found", status_code=404)

    update_data = body.model_dump(exclude_unset=True)

    # Market org review settings are locked — always require review
    if is_market_org(org_id):
        for key in (
            "review_agents", "review_connectors", "review_kbs",
            "review_mcp_servers", "review_workflows", "review_skills",
        ):
            update_data.pop(key, None)

    if "slug" in update_data and update_data["slug"] is not None:
        update_data["slug"] = await _ensure_unique_slug(
            update_data["slug"], db, exclude_id=org_id
        )

    for field, value in update_data.items():
        setattr(org, field, value)

    await db.commit()
    await db.refresh(org)

    count_result = await db.execute(
        select(func.count(OrgMembership.id)).where(OrgMembership.org_id == org_id)
    )
    member_count = count_result.scalar_one()
    d = _org_to_response(org).model_dump()
    d["member_count"] = member_count
    return ApiResponse(data=d)


@admin_router.delete("/{org_id}", response_model=ApiResponse)
async def admin_delete_org(
    org_id: str,
    _admin: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Force-delete an organization. Admin only."""

    if org_id == MARKET_ORG_ID:
        raise AppError("cannot_delete_market_org", status_code=403)
    result = await db.execute(
        select(Organization)
        .options(selectinload(Organization.memberships))
        .where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        raise AppError("org_not_found", status_code=404)

    await db.delete(org)
    await db.commit()
    return ApiResponse(data={"deleted": org_id})

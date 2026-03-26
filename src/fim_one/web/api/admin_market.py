"""Admin API endpoints for Market organisation management."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.api.admin_utils import write_audit
from fim_one.web.auth import get_current_admin
from fim_one.web.exceptions import AppError
from fim_one.web.models import User
from fim_one.web.models.agent import Agent
from fim_one.web.models.connector import Connector
from fim_one.web.models.knowledge_base import KnowledgeBase
from fim_one.web.models.mcp_server import MCPServer
from fim_one.web.models.skill import Skill
from fim_one.web.models.workflow import Workflow
from fim_one.web.platform import MARKET_ORG_ID, ensure_market_org
from fim_one.web.solution_seeds import import_solution_templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

# ---------------------------------------------------------------------------
# Resource type → ORM model mapping
# ---------------------------------------------------------------------------

_RESOURCE_MODELS: dict[str, type[Any]] = {
    "agent": Agent,
    "skill": Skill,
    "connector": Connector,
    "mcp_server": MCPServer,
    "workflow": Workflow,
    "knowledge_base": KnowledgeBase,
}

# Models that support publish/unpublish via a `status` column
_PUBLISHABLE_TYPES = {"agent", "skill", "connector", "workflow", "knowledge_base"}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class MarketResourceInfo(BaseModel):
    id: str
    resource_type: str
    name: str
    description: str | None = None
    status: str | None = None
    publish_status: str | None = None
    owner_username: str | None = None
    created_at: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_market_resource(
    db: AsyncSession, resource_type: str, resource_id: str
) -> Any:
    """Look up a Market-org resource by type and ID, or raise."""
    model = _RESOURCE_MODELS.get(resource_type)
    if model is None:
        raise AppError(
            "invalid_resource_type",
            status_code=400,
            detail=f"Invalid resource type: {resource_type}",
        )
    result = await db.execute(
        select(model).where(model.id == resource_id, model.org_id == MARKET_ORG_ID)
    )
    resource = result.scalar_one_or_none()
    if resource is None:
        raise AppError("not_found", status_code=404)
    return resource


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/market/import-templates")
async def import_market_templates(
    db: AsyncSession = Depends(get_session),
    admin: User = Depends(get_current_admin),
) -> dict[str, object]:
    """Import (or update) the 8 prebuilt Solution Templates into the Market org.

    Uses upsert-by-name: existing templates are updated, new ones are created.
    Requires admin privileges.
    """
    market_org_id = await ensure_market_org(db, owner_id=admin.id)
    result = await import_solution_templates(
        db, market_org_id=market_org_id, owner_id=admin.id
    )
    await db.commit()
    return {
        "ok": True,
        "created": result["created"],
        "updated": result["updated"],
    }


@router.get("/market/resources")
async def list_market_resources(
    resource_type: str | None = Query(None),
    db: AsyncSession = Depends(get_session),
    admin: User = Depends(get_current_admin),
) -> dict[str, object]:
    """List published resources owned by the Market organisation.

    Only returns resources with ``status == "published"`` (for models
    that have a status field).  MCP servers (no status) are always
    included.  An optional ``resource_type`` query param filters to a
    single type.  Includes the owner username for each resource.
    """
    items: list[dict[str, Any]] = []

    # Decide which types to query
    if resource_type and resource_type in _RESOURCE_MODELS:
        types_to_query = {resource_type: _RESOURCE_MODELS[resource_type]}
    else:
        types_to_query = _RESOURCE_MODELS

    # Collect all user_ids for batch username lookup
    rows_with_type: list[tuple[str, Any]] = []

    for rtype, model in types_to_query.items():
        stmt = select(model).where(model.org_id == MARKET_ORG_ID)
        # Only show published resources (models with status field)
        if rtype in _PUBLISHABLE_TYPES:
            stmt = stmt.where(model.status == "published")
        result = await db.execute(stmt)
        for row in result.scalars().all():
            rows_with_type.append((rtype, row))

    # Batch-load owner usernames
    user_ids = {
        getattr(row, "user_id")
        for _, row in rows_with_type
        if getattr(row, "user_id", None)
    }
    username_map: dict[str, str] = {}
    if user_ids:
        user_result = await db.execute(
            select(User).where(User.id.in_(user_ids))
        )
        for user in user_result.scalars().all():
            if user.username:
                username_map[user.id] = user.username

    # Build response items
    for rtype, row in rows_with_type:
        status = getattr(row, "status", None)
        publish_status = getattr(row, "publish_status", None)
        created_at = row.created_at.isoformat() if row.created_at else None
        owner_id = getattr(row, "user_id", None)
        items.append(
            MarketResourceInfo(
                id=row.id,
                resource_type=rtype,
                name=row.name,
                description=row.description,
                status=status,
                publish_status=publish_status,
                owner_username=username_map.get(owner_id) if owner_id else None,
                created_at=created_at,
            ).model_dump()
        )

    # Sort by created_at descending (None values last)
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)

    return {"items": items, "total": len(items)}


@router.delete("/market/resources/{resource_type}/{resource_id}")
async def delete_market_resource(
    resource_type: str,
    resource_id: str,
    db: AsyncSession = Depends(get_session),
    admin: User = Depends(get_current_admin),
) -> dict[str, object]:
    """Delete a resource from the Market organisation.

    For agents, linked skills (referenced via ``agent.skill_ids``) are
    also deleted.
    """
    resource = await _get_market_resource(db, resource_type, resource_id)
    resource_name: str = resource.name

    # If deleting an agent, also remove its linked skills
    if resource_type == "agent":
        skill_ids: list[str] = resource.skill_ids or []
        if skill_ids:
            result = await db.execute(
                select(Skill).where(
                    Skill.id.in_(skill_ids),
                    Skill.org_id == MARKET_ORG_ID,
                )
            )
            linked_skills = result.scalars().all()
            for skill in linked_skills:
                await db.delete(skill)

    await db.delete(resource)
    await db.commit()

    await write_audit(
        db,
        admin,
        "market.resource_delete",
        target_type=resource_type,
        target_id=resource_id,
        target_label=resource_name,
    )

    return {"ok": True}


@router.patch("/market/resources/{resource_type}/{resource_id}/unpublish")
async def unpublish_market_resource(
    resource_type: str,
    resource_id: str,
    db: AsyncSession = Depends(get_session),
    admin: User = Depends(get_current_admin),
) -> dict[str, object]:
    """Set a Market resource's status to ``draft`` (hides it from browse)."""
    if resource_type not in _PUBLISHABLE_TYPES:
        raise AppError(
            "unpublish_not_supported",
            status_code=400,
            detail=f"Resource type '{resource_type}' does not support unpublish",
        )

    resource = await _get_market_resource(db, resource_type, resource_id)
    resource.status = "draft"
    await db.commit()

    await write_audit(
        db,
        admin,
        "market.resource_unpublish",
        target_type=resource_type,
        target_id=resource_id,
        target_label=resource.name,
    )

    return {"ok": True, "status": "draft"}



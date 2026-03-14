"""Market API — browse global Market resources and subscribe/unsubscribe.

The Market is a shadow org (MARKET_ORG_ID).  Nobody holds membership in it;
resources published there are discoverable by all authenticated users via
this API and consumed through ResourceSubscription.
"""
from __future__ import annotations

import math

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.auth import get_current_user
from fim_one.web.exceptions import AppError
from fim_one.web.models.agent import Agent
from fim_one.web.models.connector import Connector
from fim_one.web.models.knowledge_base import KnowledgeBase
from fim_one.web.models.mcp_server import MCPServer
from fim_one.web.models.organization import Organization
from fim_one.web.models.resource_subscription import ResourceSubscription
from fim_one.web.models.skill import Skill
from fim_one.web.models.user import User
from fim_one.web.models.workflow import Workflow
from fim_one.web.platform import MARKET_ORG_ID
from fim_one.web.schemas.common import ApiResponse

router = APIRouter(prefix="/api/market", tags=["market"])


class SubscribeRequest(BaseModel):
    resource_type: str  # agent | connector | knowledge_base | mcp_server | skill | workflow
    resource_id: str
    org_id: str = Field(default=MARKET_ORG_ID)


# ---------------------------------------------------------------------------
# Market-info helpers (black-box display data — no secrets exposed)
# ---------------------------------------------------------------------------


def _agent_market_info(a: Agent) -> dict:
    """Black-box agent info for Market display."""
    return {
        "id": a.id,
        "resource_type": "agent",
        "name": a.name,
        "description": a.description,
        "icon": a.icon,
        "suggested_prompts": a.suggested_prompts,
        "org_id": a.org_id,
        "user_id": a.user_id,
        "status": a.status,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def _connector_market_info(c: Connector) -> dict:
    return {
        "id": c.id,
        "resource_type": "connector",
        "name": c.name,
        "description": c.description,
        "icon": c.icon,
        "type": c.type,
        "allow_fallback": getattr(c, "allow_fallback", True),
        "org_id": c.org_id,
        "user_id": c.user_id,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _kb_market_info(kb: KnowledgeBase) -> dict:
    return {
        "id": kb.id,
        "resource_type": "knowledge_base",
        "name": kb.name,
        "description": kb.description,
        "document_count": kb.document_count,
        "org_id": kb.org_id,
        "user_id": kb.user_id,
        "created_at": kb.created_at.isoformat() if kb.created_at else None,
    }


def _mcp_market_info(srv: MCPServer) -> dict:
    return {
        "id": srv.id,
        "resource_type": "mcp_server",
        "name": srv.name,
        "description": srv.description,
        "transport": srv.transport,
        "allow_fallback": getattr(srv, "allow_fallback", True),
        "org_id": srv.org_id,
        "user_id": srv.user_id,
        "created_at": srv.created_at.isoformat() if srv.created_at else None,
    }


def _skill_market_info(s: Skill) -> dict:
    return {
        "id": s.id,
        "resource_type": "skill",
        "name": s.name,
        "description": s.description,
        "org_id": s.org_id,
        "user_id": s.user_id,
        "status": s.status,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


def _workflow_market_info(w: Workflow) -> dict:
    return {
        "id": w.id,
        "resource_type": "workflow",
        "name": w.name,
        "icon": w.icon,
        "description": w.description,
        "org_id": w.org_id,
        "user_id": w.user_id,
        "status": w.status,
        "created_at": w.created_at.isoformat() if w.created_at else None,
    }


# ---------------------------------------------------------------------------
# Browse
# ---------------------------------------------------------------------------

# All supported resource types and their (model, info_fn, active_status) tuples
_ALL_RESOURCE_TYPES = [
    "agent", "connector", "knowledge_base", "mcp_server", "skill", "workflow",
]


@router.get("", response_model=ApiResponse)
async def browse_market(
    resource_type: str | None = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Browse published resources in the global Market org.

    The Market org is a shadow org — no membership required.  Only resources
    with ``publish_status IS NULL`` (no review needed) or ``approved`` are
    shown.  The current user's own resources are excluded.
    """
    # Get already-subscribed resource ids
    sub_result = await db.execute(
        select(ResourceSubscription.resource_id).where(
            ResourceSubscription.user_id == current_user.id
        )
    )
    subscribed_ids = set(sub_result.scalars().all())

    types_to_query = (
        [resource_type]
        if resource_type in _ALL_RESOURCE_TYPES
        else _ALL_RESOURCE_TYPES
    )

    model_map: dict[str, tuple] = {
        "agent": (Agent, _agent_market_info, "published"),
        "connector": (Connector, _connector_market_info, "published"),
        "knowledge_base": (KnowledgeBase, _kb_market_info, "active"),
        "mcp_server": (MCPServer, _mcp_market_info, None),
        "skill": (Skill, _skill_market_info, "published"),
        "workflow": (Workflow, _workflow_market_info, "published"),
    }

    # Collect user_ids and org_ids for batch lookup
    user_ids_needed: set[str] = set()
    org_ids_needed: set[str] = set()

    items: list[dict] = []
    for rtype in types_to_query:
        model_cls, info_fn, active_status = model_map[rtype]
        q = select(model_cls).where(
            model_cls.visibility == "org",
            model_cls.org_id == MARKET_ORG_ID,
            model_cls.user_id != current_user.id,  # exclude own resources
            # Only show approved / no-review-needed resources
            or_(
                model_cls.publish_status == None,  # noqa: E711 — no review needed
                model_cls.publish_status == "approved",
            ),
        )
        if active_status:
            q = q.where(model_cls.status == active_status)
        result = await db.execute(q)
        for obj in result.scalars().all():
            info = info_fn(obj)
            info["is_subscribed"] = obj.id in subscribed_ids
            items.append(info)

            if obj.user_id:
                user_ids_needed.add(obj.user_id)
            if obj.org_id:
                org_ids_needed.add(obj.org_id)

    # Batch-load owner usernames
    user_cache: dict[str, str] = {}
    if user_ids_needed:
        u_result = await db.execute(
            select(User.id, User.username).where(User.id.in_(list(user_ids_needed)))
        )
        for row in u_result.all():
            user_cache[row.id] = row.username or ""

    # Batch-load org names
    org_cache: dict[str, str] = {}
    if org_ids_needed:
        o_result = await db.execute(
            select(Organization.id, Organization.name).where(
                Organization.id.in_(list(org_ids_needed))
            )
        )
        for row in o_result.all():
            org_cache[row.id] = row.name or ""

    # Enrich items with owner_username and org_name, then strip internal user_id
    for item in items:
        uid = item.pop("user_id", None)
        item["owner_username"] = user_cache.get(uid, "") if uid else ""
        item["org_name"] = org_cache.get(item.get("org_id", ""), "")

    # Simple pagination
    total = len(items)
    start = (page - 1) * size
    end = start + size
    return ApiResponse(data={
        "items": items[start:end],
        "total": total,
        "page": page,
        "pages": math.ceil(total / size) if total else 0,
    })


# ---------------------------------------------------------------------------
# Subscribe / Unsubscribe
# ---------------------------------------------------------------------------

# Model lookup for resource validation
_RESOURCE_MODELS: dict[str, type] = {
    "agent": Agent,
    "connector": Connector,
    "knowledge_base": KnowledgeBase,
    "mcp_server": MCPServer,
    "skill": Skill,
    "workflow": Workflow,
}


@router.post("/subscribe", response_model=ApiResponse)
async def subscribe_resource(
    body: SubscribeRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Subscribe to a resource from the Market.

    For Market org subscriptions the only requirement is that the resource
    exists and is approved (publish_status is NULL or 'approved').
    No membership check — the Market is open to all authenticated users.
    """
    # Validate resource exists and is approved for Market org resources
    if body.org_id == MARKET_ORG_ID:
        model_cls = _RESOURCE_MODELS.get(body.resource_type)
        if model_cls is None:
            raise AppError("invalid_resource_type", status_code=400)
        res_result = await db.execute(
            select(model_cls).where(
                model_cls.id == body.resource_id,
                model_cls.org_id == MARKET_ORG_ID,
                or_(
                    model_cls.publish_status == None,  # noqa: E711
                    model_cls.publish_status == "approved",
                ),
            )
        )
        if res_result.scalar_one_or_none() is None:
            raise AppError("resource_not_found", status_code=404)

    existing = await db.execute(
        select(ResourceSubscription).where(
            ResourceSubscription.user_id == current_user.id,
            ResourceSubscription.resource_type == body.resource_type,
            ResourceSubscription.resource_id == body.resource_id,
        )
    )
    if existing.scalar_one_or_none() is None:
        sub = ResourceSubscription(
            user_id=current_user.id,
            resource_type=body.resource_type,
            resource_id=body.resource_id,
            org_id=body.org_id,
        )
        db.add(sub)
        await db.commit()

    return ApiResponse(data={"subscribed": True})


@router.delete("/unsubscribe", response_model=ApiResponse)
async def unsubscribe_resource(
    body: SubscribeRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Unsubscribe from a resource."""
    result = await db.execute(
        select(ResourceSubscription).where(
            ResourceSubscription.user_id == current_user.id,
            ResourceSubscription.resource_type == body.resource_type,
            ResourceSubscription.resource_id == body.resource_id,
        )
    )
    sub = result.scalar_one_or_none()
    if sub:
        await db.delete(sub)
        await db.commit()
    return ApiResponse(data={"unsubscribed": True})


@router.get("/subscriptions", response_model=ApiResponse)
async def list_subscriptions(
    resource_type: str | None = Query(None),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """List current user's subscriptions."""
    q = select(ResourceSubscription).where(
        ResourceSubscription.user_id == current_user.id
    )
    if resource_type:
        q = q.where(ResourceSubscription.resource_type == resource_type)
    result = await db.execute(q)
    subs = result.scalars().all()
    return ApiResponse(data=[
        {
            "id": s.id,
            "resource_type": s.resource_type,
            "resource_id": s.resource_id,
            "org_id": s.org_id,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in subs
    ])

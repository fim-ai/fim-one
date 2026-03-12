"""Market API — browse org resources and subscribe/unsubscribe."""
from __future__ import annotations

import math

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.auth import get_current_user, get_user_org_ids
from fim_one.web.exceptions import AppError
from fim_one.web.models.agent import Agent
from fim_one.web.models.connector import Connector
from fim_one.web.models.knowledge_base import KnowledgeBase
from fim_one.web.models.mcp_server import MCPServer
from fim_one.web.models.resource_subscription import ResourceSubscription
from fim_one.web.models.user import User
from fim_one.web.schemas.common import ApiResponse

router = APIRouter(prefix="/api/market", tags=["market"])


class SubscribeRequest(BaseModel):
    resource_type: str  # agent | connector | knowledge_base | mcp_server
    resource_id: str
    org_id: str


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
        "created_at": srv.created_at.isoformat() if srv.created_at else None,
    }


@router.get("", response_model=ApiResponse)
async def browse_market(
    org_id: str | None = Query(None),
    resource_type: str | None = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Browse published resources in orgs the user belongs to."""
    user_org_ids = await get_user_org_ids(current_user.id, db)
    if not user_org_ids:
        return ApiResponse(data={"items": [], "total": 0, "page": page, "pages": 0})

    target_orgs = [org_id] if org_id and org_id in user_org_ids else user_org_ids

    # Get already-subscribed resource ids
    sub_result = await db.execute(
        select(ResourceSubscription.resource_id).where(
            ResourceSubscription.user_id == current_user.id
        )
    )
    subscribed_ids = set(sub_result.scalars().all())

    items = []
    types_to_query = (
        [resource_type]
        if resource_type in ("agent", "connector", "knowledge_base", "mcp_server")
        else ["agent", "connector", "knowledge_base", "mcp_server"]
    )

    for rtype in types_to_query:
        model_map = {
            "agent": (Agent, _agent_market_info, "published"),
            "connector": (Connector, _connector_market_info, "published"),
            "knowledge_base": (KnowledgeBase, _kb_market_info, "active"),
            "mcp_server": (MCPServer, _mcp_market_info, None),
        }
        model_cls, info_fn, active_status = model_map[rtype]
        q = select(model_cls).where(
            model_cls.visibility == "org",
            model_cls.org_id.in_(target_orgs),
            model_cls.user_id != current_user.id,  # exclude own resources
        )
        if active_status:
            q = q.where(model_cls.status == active_status)
        result = await db.execute(q)
        for obj in result.scalars().all():
            info = info_fn(obj)
            info["is_subscribed"] = obj.id in subscribed_ids
            items.append(info)

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


@router.post("/subscribe", response_model=ApiResponse)
async def subscribe_resource(
    body: SubscribeRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Subscribe to a resource from an org Market."""
    user_org_ids = await get_user_org_ids(current_user.id, db)
    if body.org_id not in user_org_ids:
        raise AppError("not_org_member", status_code=403)

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

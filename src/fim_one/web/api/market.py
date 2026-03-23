"""Market API — browse global Market resources and subscribe/unsubscribe.

The Market is a shadow org (MARKET_ORG_ID).  Nobody holds membership in it;
resources published there are discoverable by all authenticated users via
this API and consumed through ResourceSubscription.
"""
from __future__ import annotations

from typing import Any

import logging
import math

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.auth import get_current_user
from fim_one.web.dependency_analyzer import resolve_solution_dependencies
from fim_one.web.exceptions import AppError
from fim_one.web.models.agent import Agent
from fim_one.web.models.connector import Connector
from fim_one.web.models.connector_credential import ConnectorCredential
from fim_one.web.models.knowledge_base import KnowledgeBase
from fim_one.web.models.mcp_server import MCPServer
from fim_one.web.models.mcp_server_credential import MCPServerCredential
from fim_one.web.models.organization import OrgMembership, Organization
from fim_one.web.models.resource_subscription import ResourceSubscription
from fim_one.web.models.skill import Skill
from fim_one.web.models.user import User
from fim_one.web.models.workflow import Workflow
from fim_one.web.platform import MARKET_ORG_ID
from fim_one.web.schemas.common import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/market", tags=["market"])


class SubscribeRequest(BaseModel):
    resource_type: str  # agent | connector | knowledge_base | mcp_server | skill | workflow
    resource_id: str
    org_id: str = Field(default=MARKET_ORG_ID)


# ---------------------------------------------------------------------------
# Market-info helpers (black-box display data — no secrets exposed)
# ---------------------------------------------------------------------------


def _agent_market_info(a: Agent) -> dict[str, Any]:
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


def _connector_market_info(c: Connector) -> dict[str, Any]:
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


def _kb_market_info(kb: KnowledgeBase) -> dict[str, Any]:
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


def _mcp_market_info(srv: MCPServer) -> dict[str, Any]:
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


def _skill_market_info(s: Skill) -> dict[str, Any]:
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


def _workflow_market_info(w: Workflow) -> dict[str, Any]:
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

# Category constants for marketplace browsing
SOLUTION_TYPES = ["agent", "skill", "workflow"]
COMPONENT_TYPES = ["connector", "mcp_server"]
MARKET_RESOURCE_TYPES = SOLUTION_TYPES + COMPONENT_TYPES

# All supported resource types and their (model, info_fn, active_status) tuples
_ALL_RESOURCE_TYPES = [
    "agent", "connector", "knowledge_base", "mcp_server", "skill", "workflow",
]


@router.get("", response_model=ApiResponse)
async def browse_market(
    resource_type: str | None = Query(None),
    scope: str = Query("market"),  # "market" (default) or "org:{org_id}"
    category: str | None = Query(None),  # "solutions" | "components" | None (all)
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Browse published resources in the Market or an org.

    Scope determines which org to browse:
    - ``market`` (default) — the global Market shadow org, open to all.
    - ``org:{org_id}`` — a specific org (membership required).

    Only resources with ``publish_status IS NULL`` (no review needed) or
    ``approved`` are shown.  The current user's own resources are excluded.
    """
    # Parse scope → browse_org_id
    if scope == "market":
        browse_org_id = MARKET_ORG_ID
    elif scope.startswith("org:"):
        org_id = scope[4:]
        membership = await db.execute(
            select(OrgMembership).where(
                OrgMembership.org_id == org_id,
                OrgMembership.user_id == current_user.id,
            )
        )
        if membership.scalar_one_or_none() is None:
            raise AppError("not_org_member", status_code=403)
        browse_org_id = org_id
    else:
        raise AppError("invalid_scope", status_code=400)

    # Get already-subscribed resource ids
    sub_result = await db.execute(
        select(ResourceSubscription.resource_id).where(
            ResourceSubscription.user_id == current_user.id
        )
    )
    subscribed_ids = set(sub_result.scalars().all())

    # Determine resource types to query
    if resource_type in _ALL_RESOURCE_TYPES:
        types_to_query = [resource_type]
    elif category == "solutions":
        types_to_query = SOLUTION_TYPES
    elif category == "components":
        types_to_query = COMPONENT_TYPES
    elif browse_org_id == MARKET_ORG_ID:
        types_to_query = MARKET_RESOURCE_TYPES
    else:
        # Org scope: include all types (KB stays visible in org scope)
        types_to_query = _ALL_RESOURCE_TYPES

    model_map: dict[str, tuple[Any, ...]] = {
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

    items: list[dict[str, Any]] = []
    for rtype in types_to_query:
        model_cls, info_fn, active_status = model_map[rtype]
        q = select(model_cls).where(
            model_cls.visibility == "org",
            model_cls.org_id == browse_org_id,
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
            info["is_own"] = obj.user_id == current_user.id
            info["is_solution"] = rtype in SOLUTION_TYPES
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
        for org_row in o_result.all():
            org_cache[org_row.id] = org_row.name or ""

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
# Dependencies
# ---------------------------------------------------------------------------


@router.get("/dependencies", response_model=ApiResponse)
async def get_resource_dependencies(
    resource_type: str = Query(...),  # noqa: B008
    resource_id: str = Query(...),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Analyze dependencies for a Solution resource.

    Returns a DependencyManifest with content deps (auto-subscribed)
    and connection deps (requiring credentials).
    Only applicable to solution types: agent, skill, workflow.
    """
    if resource_type not in SOLUTION_TYPES:
        return ApiResponse(data={"content_deps": [], "connection_deps": []})

    manifest = await resolve_solution_dependencies(resource_type, resource_id, db)
    return ApiResponse(data=manifest.to_dict())


# ---------------------------------------------------------------------------
# Subscribe / Unsubscribe
# ---------------------------------------------------------------------------

# Model lookup for resource validation
_RESOURCE_MODELS: dict[str, Any] = {
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
    """Subscribe to a resource from the Market or an org.

    For Market org subscriptions the only requirement is that the resource
    exists and is approved (publish_status is NULL or 'approved').
    No membership check — the Market is open to all authenticated users.

    For org subscriptions the user must be a member of the org and the
    resource must exist, be visible, and be approved.
    """
    model_cls = _RESOURCE_MODELS.get(body.resource_type)
    if model_cls is None:
        raise AppError("invalid_resource_type", status_code=400)

    if body.org_id == MARKET_ORG_ID:
        # Market: no membership check needed, just validate resource
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
    else:
        # Org subscription: validate membership + resource exists and is approved
        membership = await db.execute(
            select(OrgMembership).where(
                OrgMembership.org_id == body.org_id,
                OrgMembership.user_id == current_user.id,
            )
        )
        if membership.scalar_one_or_none() is None:
            raise AppError("not_org_member", status_code=403)

        res_result = await db.execute(
            select(model_cls).where(
                model_cls.id == body.resource_id,
                model_cls.org_id == body.org_id,
                model_cls.visibility == "org",
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

    # Auto-subscribe content dependencies for Solutions.
    # NOTE: knowledge_base deps are intentionally skipped — the agent
    # accesses bound KBs via owner delegation (chat.py resolves KB owners
    # at runtime), so creating a subscription would only leak the KB into
    # the subscriber's KB list without adding functional value.
    manifest = None
    if body.resource_type in SOLUTION_TYPES:
        manifest = await resolve_solution_dependencies(
            body.resource_type, body.resource_id, db
        )
        for dep in manifest.content_deps:
            if dep.resource_type == "knowledge_base":
                continue  # black-box: accessed via owner delegation
            existing_dep = await db.execute(
                select(ResourceSubscription).where(
                    ResourceSubscription.user_id == current_user.id,
                    ResourceSubscription.resource_type == dep.resource_type,
                    ResourceSubscription.resource_id == dep.resource_id,
                )
            )
            if existing_dep.scalar_one_or_none() is None:
                dep_sub = ResourceSubscription(
                    user_id=current_user.id,
                    resource_type=dep.resource_type,
                    resource_id=dep.resource_id,
                    org_id=body.org_id,
                )
                db.add(dep_sub)

        # Auto-subscribe connection dependencies (Connector, MCP Server).
        # This makes them appear in the subscriber's resource list so they
        # can configure credentials via the existing MyCredentials forms.
        for conn_dep in manifest.connection_deps:
            existing_dep = await db.execute(
                select(ResourceSubscription).where(
                    ResourceSubscription.user_id == current_user.id,
                    ResourceSubscription.resource_type == conn_dep.resource_type,
                    ResourceSubscription.resource_id == conn_dep.resource_id,
                )
            )
            if existing_dep.scalar_one_or_none() is None:
                dep_sub = ResourceSubscription(
                    user_id=current_user.id,
                    resource_type=conn_dep.resource_type,
                    resource_id=conn_dep.resource_id,
                    org_id=body.org_id,
                )
                db.add(dep_sub)

        await db.commit()

    return ApiResponse(data={
        "subscribed": True,
        # Include manifest so frontend knows about connection deps
        **({"dependencies": manifest.to_dict()} if manifest is not None else {}),
    })


@router.delete("/unsubscribe", response_model=ApiResponse)
async def unsubscribe_resource(
    body: SubscribeRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Unsubscribe from a resource with cascade cleanup.

    1. Deletes the main subscription.
    2. For connection-type resources (connector, mcp_server): deletes the
       user's stored credential for that resource.
    3. For Solution-type resources (agent, skill, workflow): resolves content
       dependencies and removes orphaned content-dep subscriptions that are
       no longer needed by any other subscribed Solution.
    """
    result = await db.execute(
        select(ResourceSubscription).where(
            ResourceSubscription.user_id == current_user.id,
            ResourceSubscription.resource_type == body.resource_type,
            ResourceSubscription.resource_id == body.resource_id,
        )
    )
    sub = result.scalar_one_or_none()
    if not sub:
        return ApiResponse(data={"unsubscribed": True})

    # --- 1. Delete the main subscription ---
    await db.delete(sub)

    # --- 2. Clean up credentials for connection-type resources ---
    if body.resource_type == "connector":
        await db.execute(
            delete(ConnectorCredential).where(
                ConnectorCredential.connector_id == body.resource_id,
                ConnectorCredential.user_id == current_user.id,
            )
        )
    elif body.resource_type == "mcp_server":
        await db.execute(
            delete(MCPServerCredential).where(
                MCPServerCredential.server_id == body.resource_id,
                MCPServerCredential.user_id == current_user.id,
            )
        )

    # --- 3. Cascade-clean content deps for Solution-type resources ---
    if body.resource_type in SOLUTION_TYPES:
        await _cascade_clean_content_deps(
            user_id=current_user.id,
            unsubscribed_type=body.resource_type,
            unsubscribed_id=body.resource_id,
            db=db,
        )
        await _cascade_clean_connection_deps(
            user_id=current_user.id,
            unsubscribed_type=body.resource_type,
            unsubscribed_id=body.resource_id,
            db=db,
        )

    await db.commit()
    return ApiResponse(data={"unsubscribed": True})


async def _cascade_clean_content_deps(
    *,
    user_id: str,
    unsubscribed_type: str,
    unsubscribed_id: str,
    db: AsyncSession,
) -> None:
    """Remove orphaned content-dep subscriptions after a Solution is unsubscribed.

    For each content dep of the unsubscribed Solution, checks whether any
    OTHER subscribed Solution still references it.  If not, the content-dep
    subscription is deleted.
    """
    manifest = await resolve_solution_dependencies(
        unsubscribed_type, unsubscribed_id, db
    )
    if not manifest.content_deps:
        return

    # Get all remaining Solution subscriptions for this user
    other_subs_result = await db.execute(
        select(ResourceSubscription).where(
            ResourceSubscription.user_id == user_id,
            ResourceSubscription.resource_type.in_(list(SOLUTION_TYPES)),
            # Exclude the one we just deleted (already removed from session,
            # but belt-and-suspenders: filter by id)
            ResourceSubscription.resource_id != unsubscribed_id,
        )
    )
    other_solution_subs = other_subs_result.scalars().all()

    # Pre-resolve deps for each remaining Solution (cache to avoid re-querying)
    other_dep_sets: list[set[tuple[str, str]]] = []
    for other_sub in other_solution_subs:
        other_manifest = await resolve_solution_dependencies(
            other_sub.resource_type, other_sub.resource_id, db
        )
        other_dep_sets.append(
            {(d.resource_type, d.resource_id) for d in other_manifest.content_deps}
        )

    # Union all deps from other Solutions
    all_still_needed: set[tuple[str, str]] = set()
    for dep_set in other_dep_sets:
        all_still_needed |= dep_set

    # Delete orphaned content-dep subscriptions.
    # KB deps are skipped — we never auto-create KB subscriptions for agent
    # deps (they use owner delegation), so touching them here would risk
    # deleting a user's *direct* KB subscription.
    for dep in manifest.content_deps:
        if dep.resource_type == "knowledge_base":
            continue  # never auto-subscribed; skip to avoid side-effects
        dep_key = (dep.resource_type, dep.resource_id)
        if dep_key in all_still_needed:
            continue

        dep_sub_result = await db.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.user_id == user_id,
                ResourceSubscription.resource_type == dep.resource_type,
                ResourceSubscription.resource_id == dep.resource_id,
            )
        )
        dep_sub_obj = dep_sub_result.scalar_one_or_none()
        if dep_sub_obj:
            logger.info(
                "Cascade-deleting orphaned subscription: user=%s, type=%s, id=%s",
                user_id, dep.resource_type, dep.resource_id,
            )
            await db.delete(dep_sub_obj)


async def _cascade_clean_connection_deps(
    *,
    user_id: str,
    unsubscribed_type: str,
    unsubscribed_id: str,
    db: AsyncSession,
) -> None:
    """Remove orphaned connection-dep subscriptions and credentials after a Solution is unsubscribed.

    For each connection dep of the unsubscribed Solution, checks whether any
    OTHER subscribed Solution still references it. If not, the connection-dep
    subscription and associated user credentials are deleted.
    """
    manifest = await resolve_solution_dependencies(
        unsubscribed_type, unsubscribed_id, db
    )
    if not manifest.connection_deps:
        return

    # Get all remaining Solution subscriptions for this user
    other_subs_result = await db.execute(
        select(ResourceSubscription).where(
            ResourceSubscription.user_id == user_id,
            ResourceSubscription.resource_type.in_(list(SOLUTION_TYPES)),
            ResourceSubscription.resource_id != unsubscribed_id,
        )
    )
    other_solution_subs = other_subs_result.scalars().all()

    # Pre-resolve deps for each remaining Solution
    other_conn_deps: set[tuple[str, str]] = set()
    for other_sub in other_solution_subs:
        other_manifest = await resolve_solution_dependencies(
            other_sub.resource_type, other_sub.resource_id, db
        )
        for d in other_manifest.connection_deps:
            other_conn_deps.add((d.resource_type, d.resource_id))

    # Delete orphaned connection-dep subscriptions + credentials
    for dep in manifest.connection_deps:
        dep_key = (dep.resource_type, dep.resource_id)
        if dep_key in other_conn_deps:
            continue

        # Delete subscription
        dep_sub_result = await db.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.user_id == user_id,
                ResourceSubscription.resource_type == dep.resource_type,
                ResourceSubscription.resource_id == dep.resource_id,
            )
        )
        dep_sub_obj = dep_sub_result.scalar_one_or_none()
        if dep_sub_obj:
            logger.info(
                "Cascade-deleting orphaned connection subscription: user=%s, type=%s, id=%s",
                user_id, dep.resource_type, dep.resource_id,
            )
            await db.delete(dep_sub_obj)

        # Delete associated credentials
        if dep.resource_type == "connector":
            await db.execute(
                delete(ConnectorCredential).where(
                    ConnectorCredential.connector_id == dep.resource_id,
                    ConnectorCredential.user_id == user_id,
                )
            )
        elif dep.resource_type == "mcp_server":
            await db.execute(
                delete(MCPServerCredential).where(
                    MCPServerCredential.server_id == dep.resource_id,
                    MCPServerCredential.user_id == user_id,
                )
            )


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

"""Agent CRUD endpoints with publish/unpublish lifecycle."""

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
from fim_one.web.models import Agent, User
from fim_one.web.models.connector import Connector
from fim_one.web.models.knowledge_base import KnowledgeBase
from fim_one.web.models.mcp_server import MCPServer
from fim_one.web.models.resource_subscription import ResourceSubscription
from fim_one.web.schemas.agent import AgentCreate, AgentForkRequest, AgentResponse, AgentUpdate
from fim_one.web.schemas.common import ApiResponse, PaginatedResponse, PublishRequest
from fim_one.web.visibility import build_visibility_filter

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _agent_to_response(agent: Agent, *, is_owner: bool = True) -> AgentResponse:
    # Non-owners: strip internal content (instructions, model config are IP)
    if is_owner:
        instructions = agent.instructions
        compact_instructions = agent.compact_instructions
        model_config_json = agent.model_config_json
    else:
        instructions = None
        compact_instructions = None
        model_config_json = None
    return AgentResponse(
        id=agent.id,
        user_id=agent.user_id or "",
        name=agent.name,
        icon=agent.icon,
        description=agent.description,
        instructions=instructions,
        model_config_json=model_config_json,
        tool_categories=agent.tool_categories,
        suggested_prompts=agent.suggested_prompts,
        kb_ids=agent.kb_ids,
        connector_ids=agent.connector_ids,
        mcp_server_ids=agent.mcp_server_ids,
        grounding_config=agent.grounding_config,
        sandbox_config=agent.sandbox_config,
        execution_mode=agent.execution_mode,
        status=agent.status,
        published_at=(
            agent.published_at.isoformat() if agent.published_at else None
        ),
        is_active=agent.is_active,
        is_builder=agent.is_builder,
        compact_instructions=compact_instructions,
        visibility=getattr(agent, "visibility", "personal"),
        org_id=getattr(agent, "org_id", None),
        publish_status=getattr(agent, "publish_status", None),
        reviewed_by=getattr(agent, "reviewed_by", None),
        reviewed_at=(
            agent.reviewed_at.isoformat() if agent.reviewed_at else None
        ),
        review_note=getattr(agent, "review_note", None),
        forked_from=getattr(agent, "forked_from", None),
        created_at=agent.created_at.isoformat() if agent.created_at else "",
        updated_at=agent.updated_at.isoformat() if agent.updated_at else None,
    )


async def _get_owned_agent(
    agent_id: str,
    user_id: str,
    db: AsyncSession,
) -> Agent:
    """Fetch an agent that the user owns."""
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id, Agent.user_id == user_id)
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise AppError("agent_not_found", status_code=404)
    return agent


async def _get_accessible_agent(
    agent_id: str,
    user_id: str,
    db: AsyncSession,
) -> Agent:
    """Fetch an agent the user owns, org-shared, or Market-installed."""
    from fim_one.web.visibility import resolve_visibility
    vis_filter, _, _ = await resolve_visibility(Agent, user_id, "agent", db)
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id, vis_filter)
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise AppError("agent_not_found", status_code=404)
    return agent


async def _validate_binding_ownership(
    user_id: str,
    db: AsyncSession,
    connector_ids: list[str] | None = None,
    kb_ids: list[str] | None = None,
    mcp_server_ids: list[str] | None = None,
) -> None:
    """Verify that all referenced connector_ids, kb_ids, and mcp_server_ids belong to the user.

    Raises HTTP 403 if any referenced resource is not owned by the user.
    """
    if connector_ids:
        result = await db.execute(
            select(func.count())
            .select_from(Connector)
            .where(Connector.id.in_(connector_ids), Connector.user_id == user_id)
        )
        owned_count = result.scalar_one()
        if owned_count != len(connector_ids):
            raise AppError("connector_ownership_denied", status_code=403)

    if kb_ids:
        result = await db.execute(
            select(func.count())
            .select_from(KnowledgeBase)
            .where(KnowledgeBase.id.in_(kb_ids), KnowledgeBase.user_id == user_id)
        )
        owned_count = result.scalar_one()
        if owned_count != len(kb_ids):
            raise AppError("kb_ownership_denied", status_code=403)

    if mcp_server_ids:
        result = await db.execute(
            select(func.count())
            .select_from(MCPServer)
            .where(MCPServer.id.in_(mcp_server_ids), MCPServer.user_id == user_id)
        )
        owned_count = result.scalar_one()
        if owned_count != len(mcp_server_ids):
            raise AppError("mcp_server_ownership_denied", status_code=403)


@router.post("", response_model=ApiResponse)
async def create_agent(
    body: AgentCreate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    await _validate_binding_ownership(
        current_user.id, db,
        connector_ids=body.connector_ids,
        kb_ids=body.kb_ids,
        mcp_server_ids=body.mcp_server_ids,
    )
    agent = Agent(
        user_id=current_user.id,
        name=body.name,
        icon=body.icon,
        description=body.description,
        instructions=body.instructions,
        model_config_json=body.model_config_json,
        tool_categories=body.tool_categories,
        suggested_prompts=body.suggested_prompts,
        kb_ids=body.kb_ids,
        connector_ids=body.connector_ids,
        mcp_server_ids=body.mcp_server_ids,
        grounding_config=body.grounding_config,
        sandbox_config=body.sandbox_config,
        execution_mode=body.execution_mode,
        compact_instructions=body.compact_instructions,
        status="draft",
    )
    db.add(agent)
    await db.commit()
    result = await db.execute(select(Agent).where(Agent.id == agent.id))
    agent = result.scalar_one()
    return ApiResponse(data=_agent_to_response(agent).model_dump())


@router.get("", response_model=PaginatedResponse)
async def list_agents(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    agent_status: str | None = Query(None, alias="status"),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    user_org_ids = await get_user_org_ids(current_user.id, db)

    # Get subscribed agent IDs with org_id for source tagging
    sub_result = await db.execute(
        select(ResourceSubscription.resource_id, ResourceSubscription.org_id).where(
            ResourceSubscription.user_id == current_user.id,
            ResourceSubscription.resource_type == "agent",
        )
    )
    sub_rows = sub_result.all()
    subscribed_agent_ids = [r.resource_id for r in sub_rows]
    sub_org_map = {r.resource_id: r.org_id for r in sub_rows}

    base = select(Agent).where(
        Agent.is_builder == False,  # noqa: E712
        build_visibility_filter(Agent, current_user.id, user_org_ids, subscribed_ids=subscribed_agent_ids),
    )
    if agent_status is not None:
        base = base.where(Agent.status == agent_status)

    count_result = await db.execute(
        select(func.count()).select_from(base.subquery())
    )
    total = count_result.scalar_one()

    result = await db.execute(
        base.order_by(Agent.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    agents = result.scalars().all()

    subscribed_agent_ids_set = set(subscribed_agent_ids)
    items = []
    for a in agents:
        _is_owner = a.user_id == current_user.id
        resp = _agent_to_response(a, is_owner=_is_owner)
        if _is_owner:
            resp.source = "own"
        elif a.id in subscribed_agent_ids_set:
            sub_org_id = sub_org_map.get(a.id)
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


@router.get("/{agent_id}", response_model=ApiResponse)
async def get_agent(
    agent_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    agent = await _get_accessible_agent(agent_id, current_user.id, db)
    _is_owner = agent.user_id == current_user.id
    return ApiResponse(data=_agent_to_response(agent, is_owner=_is_owner).model_dump())


@router.put("/{agent_id}", response_model=ApiResponse)
async def update_agent(
    agent_id: str,
    body: AgentUpdate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    agent = await _get_owned_agent(agent_id, current_user.id, db)

    update_data = body.model_dump(exclude_unset=True)
    await _validate_binding_ownership(
        current_user.id, db,
        connector_ids=update_data.get("connector_ids"),
        kb_ids=update_data.get("kb_ids"),
        mcp_server_ids=update_data.get("mcp_server_ids"),
    )
    for field, value in update_data.items():
        setattr(agent, field, value)

    content_changed = bool(update_data.keys() - {"is_active"})
    if content_changed:
        from fim_one.web.publish_review import check_edit_revert

        reverted = await check_edit_revert(agent, db)
    else:
        reverted = False

    await db.commit()
    result = await db.execute(select(Agent).where(Agent.id == agent.id))
    agent = result.scalar_one()
    data = _agent_to_response(agent).model_dump()
    if reverted:
        data["publish_status_reverted"] = True
    return ApiResponse(data=data)


@router.delete("/{agent_id}", response_model=ApiResponse)
async def delete_agent(
    agent_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    agent = await _get_owned_agent(agent_id, current_user.id, db)
    await db.delete(agent)
    await db.commit()
    return ApiResponse(data={"deleted": agent_id})


@router.post("/{agent_id}/publish", response_model=ApiResponse)
async def publish_agent(
    agent_id: str,
    body: PublishRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Publish agent to personal, org, or global scope."""
    agent = await _get_owned_agent(agent_id, current_user.id, db)

    if body.scope == "org":
        if not body.org_id:
            raise AppError("org_id_required", status_code=400)
        from fim_one.web.auth import require_org_member
        if not is_market_org(body.org_id):
            await require_org_member(body.org_id, current_user, db)
        agent.visibility = "org"
        agent.org_id = body.org_id
        from fim_one.web.publish_review import apply_publish_status
        await apply_publish_status(agent, body.org_id, db, resource_type="agent", publisher_id=current_user.id)
    elif body.scope == "global":
        if not current_user.is_admin:
            raise AppError("admin_required_for_global", status_code=403)
        agent.visibility = "global"
        setattr(agent, "is_global", True)  # backward compat
        agent.org_id = None
    else:
        raise AppError("invalid_scope", status_code=400)

    agent.published_at = datetime.now(UTC)
    agent.status = "published"

    # Audit log: submitted (org scope only — org_id is set at this point)
    if body.scope == "org" and body.org_id:
        from fim_one.web.api.reviews import log_review_event
        await log_review_event(
            db=db,
            org_id=body.org_id,
            resource_type="agent",
            resource_id=agent.id,
            resource_name=agent.name,
            action="submitted",
            actor=current_user,
        )

    # Check referenced resources and warn about private dependencies
    warnings: list[str] = []
    if agent.kb_ids:
        for kb_id in agent.kb_ids:
            result = await db.execute(
                select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
            )
            kb = result.scalar_one_or_none()
            if kb and getattr(kb, "visibility", "personal") == "personal":
                warnings.append(
                    f"KB '{kb.name}' is private — will be accessed via owner delegation"
                )

    if agent.connector_ids:
        for cid in agent.connector_ids:
            result = await db.execute(
                select(Connector).where(Connector.id == cid)
            )
            conn = result.scalar_one_or_none()
            if conn and getattr(conn, "visibility", "personal") == "personal":
                warnings.append(
                    f"Connector '{conn.name}' is private — will be accessed via owner delegation"
                )

    await db.commit()
    await db.refresh(agent)

    data = _agent_to_response(agent).model_dump()
    data["warnings"] = warnings
    return ApiResponse(data=data)


@router.post("/{agent_id}/resubmit", response_model=ApiResponse)
async def resubmit_agent(
    agent_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Resubmit a rejected agent for review."""
    agent = await _get_owned_agent(agent_id, current_user.id, db)
    if agent.publish_status != "rejected":
        raise AppError("not_rejected", status_code=400)
    agent.publish_status = "pending_review"
    agent.reviewed_by = None
    agent.reviewed_at = None
    agent.review_note = None

    if agent.org_id:
        from fim_one.web.api.reviews import log_review_event
        await log_review_event(
            db=db,
            org_id=agent.org_id,
            resource_type="agent",
            resource_id=agent.id,
            resource_name=agent.name,
            action="resubmitted",
            actor=current_user,
        )

    await db.commit()
    await db.refresh(agent)
    return ApiResponse(data=_agent_to_response(agent).model_dump())


@router.post("/{agent_id}/unpublish", response_model=ApiResponse)
async def unpublish_agent(
    agent_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Revert agent to personal visibility."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise AppError("agent_not_found", status_code=404)

    is_owner = agent.user_id == current_user.id
    is_admin = current_user.is_admin
    is_org_admin = False

    if agent.visibility == "org" and agent.org_id and not is_owner:
        try:
            from fim_one.web.auth import require_org_admin
            await require_org_admin(agent.org_id, current_user, db)
            is_org_admin = True
        except Exception:
            pass

    if not (is_owner or is_admin or is_org_admin):
        raise AppError("unpublish_denied", status_code=403)

    # Log BEFORE clearing org_id so we capture which org it was unpublished from
    if agent.org_id:
        from fim_one.web.api.reviews import log_review_event
        await log_review_event(
            db=db,
            org_id=agent.org_id,
            resource_type="agent",
            resource_id=agent.id,
            resource_name=agent.name,
            action="unpublished",
            actor=current_user,
        )

    agent.visibility = "personal"
    agent.org_id = None
    agent.published_at = None
    agent.publish_status = None
    agent.status = "draft"

    await db.commit()
    await db.refresh(agent)
    return ApiResponse(data=_agent_to_response(agent).model_dump())


# ---------------------------------------------------------------------------
# Fork (clone)
# ---------------------------------------------------------------------------


@router.post("/{agent_id}/fork", response_model=ApiResponse)
async def fork_agent(
    agent_id: str,
    body: AgentForkRequest | None = None,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Clone an existing agent for the current user.

    Copies all configuration fields but NOT org_id, publish_status, or
    is_builder.  The forked agent is set to personal/active ownership.
    Only the owner of the source agent can fork it.
    """
    source = await _get_accessible_agent(agent_id, current_user.id, db)
    if source.user_id != current_user.id:
        raise AppError("fork_denied", status_code=403, detail="Only the owner can fork this resource")

    fork_name = (body.name if body and body.name else f"{source.name} (Copy)")[:200]

    forked = Agent(
        user_id=current_user.id,
        name=fork_name,
        description=source.description,
        icon=source.icon,
        instructions=source.instructions,
        execution_mode=source.execution_mode,
        model_config_json=source.model_config_json,
        tool_categories=source.tool_categories,
        suggested_prompts=source.suggested_prompts,
        connector_ids=source.connector_ids,
        kb_ids=source.kb_ids,
        mcp_server_ids=source.mcp_server_ids,
        skill_ids=source.skill_ids,
        compact_instructions=source.compact_instructions,
        grounding_config=source.grounding_config,
        sandbox_config=source.sandbox_config,
        is_active=True,
        is_builder=False,
        forked_from=source.id,
        visibility="personal",
        org_id=None,
        publish_status=None,
        status="draft",
    )
    db.add(forked)
    await db.commit()

    result = await db.execute(
        select(Agent).where(Agent.id == forked.id)
    )
    forked = result.scalar_one()
    return ApiResponse(data=_agent_to_response(forked).model_dump())

"""Agent CRUD endpoints with publish/unpublish lifecycle."""

from __future__ import annotations

import math
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_agent.db import get_session
from fim_agent.web.exceptions import AppError
from fim_agent.web.auth import get_current_user
from fim_agent.web.models import Agent, User
from fim_agent.web.models.connector import Connector
from fim_agent.web.models.knowledge_base import KnowledgeBase
from fim_agent.web.schemas.agent import AgentCreate, AgentResponse, AgentUpdate
from fim_agent.web.schemas.common import ApiResponse, PaginatedResponse

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _agent_to_response(agent: Agent) -> AgentResponse:
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        icon=agent.icon,
        description=agent.description,
        instructions=agent.instructions,
        model_config_json=agent.model_config_json,
        tool_categories=agent.tool_categories,
        suggested_prompts=agent.suggested_prompts,
        kb_ids=agent.kb_ids,
        connector_ids=agent.connector_ids,
        grounding_config=agent.grounding_config,
        sandbox_config=agent.sandbox_config,
        execution_mode=agent.execution_mode,
        status=agent.status,
        published_at=(
            agent.published_at.isoformat() if agent.published_at else None
        ),
        is_global=agent.is_global,
        is_builder=agent.is_builder,
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
    """Fetch an agent the user owns OR a published global agent (read-only)."""
    from sqlalchemy import and_
    result = await db.execute(
        select(Agent).where(
            Agent.id == agent_id,
            or_(
                Agent.user_id == user_id,
                and_(Agent.is_global == True, Agent.status == "published"),  # noqa: E712
            ),
        )
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
) -> None:
    """Verify that all referenced connector_ids and kb_ids belong to the user.

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
        grounding_config=body.grounding_config,
        sandbox_config=body.sandbox_config,
        execution_mode=body.execution_mode,
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
    from sqlalchemy import and_
    base = select(Agent).where(
        Agent.is_builder == False,  # noqa: E712
        or_(
            Agent.user_id == current_user.id,
            and_(Agent.is_global == True, Agent.status == "published"),  # noqa: E712
        ),
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

    return PaginatedResponse(
        items=[_agent_to_response(a).model_dump() for a in agents],
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
    return ApiResponse(data=_agent_to_response(agent).model_dump())


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
    )
    for field, value in update_data.items():
        setattr(agent, field, value)

    await db.commit()
    result = await db.execute(select(Agent).where(Agent.id == agent.id))
    agent = result.scalar_one()
    return ApiResponse(data=_agent_to_response(agent).model_dump())


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
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    agent = await _get_owned_agent(agent_id, current_user.id, db)
    agent.status = "published"
    agent.published_at = datetime.now(UTC)
    await db.commit()
    result = await db.execute(select(Agent).where(Agent.id == agent.id))
    agent = result.scalar_one()
    return ApiResponse(data=_agent_to_response(agent).model_dump())


@router.post("/{agent_id}/unpublish", response_model=ApiResponse)
async def unpublish_agent(
    agent_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    agent = await _get_owned_agent(agent_id, current_user.id, db)
    agent.status = "draft"
    agent.published_at = None
    await db.commit()
    result = await db.execute(select(Agent).where(Agent.id == agent.id))
    agent = result.scalar_one()
    return ApiResponse(data=_agent_to_response(agent).model_dump())

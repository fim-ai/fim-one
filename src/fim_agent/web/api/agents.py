"""Agent CRUD endpoints with publish/unpublish lifecycle."""

from __future__ import annotations

import math
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_agent.db import get_session
from fim_agent.web.auth import get_current_user
from fim_agent.web.models import Agent, User
from fim_agent.web.schemas.agent import AgentCreate, AgentResponse, AgentUpdate
from fim_agent.web.schemas.common import ApiResponse, PaginatedResponse

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _agent_to_response(agent: Agent) -> AgentResponse:
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        description=agent.description,
        instructions=agent.instructions,
        execution_mode=agent.execution_mode,
        model_config_json=agent.model_config_json,
        tool_categories=agent.tool_categories,
        suggested_prompts=agent.suggested_prompts,
        kb_ids=agent.kb_ids,
        grounding_config=agent.grounding_config,
        status=agent.status,
        published_at=(
            agent.published_at.isoformat() if agent.published_at else None
        ),
        created_at=agent.created_at.isoformat() if agent.created_at else "",
        updated_at=agent.updated_at.isoformat() if agent.updated_at else None,
    )


async def _get_owned_agent(
    agent_id: str,
    user_id: str,
    db: AsyncSession,
) -> Agent:
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id, Agent.user_id == user_id)
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found",
        )
    return agent


@router.post("", response_model=ApiResponse)
async def create_agent(
    body: AgentCreate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    agent = Agent(
        user_id=current_user.id,
        name=body.name,
        description=body.description,
        instructions=body.instructions,
        execution_mode=body.execution_mode,
        model_config_json=body.model_config_json,
        tool_categories=body.tool_categories,
        suggested_prompts=body.suggested_prompts,
        kb_ids=body.kb_ids,
        grounding_config=body.grounding_config,
        status="draft",
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return ApiResponse(data=_agent_to_response(agent).model_dump())


@router.get("", response_model=PaginatedResponse)
async def list_agents(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    agent_status: str | None = Query(None, alias="status"),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    base = select(Agent).where(Agent.user_id == current_user.id)
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
    agent = await _get_owned_agent(agent_id, current_user.id, db)
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
    for field, value in update_data.items():
        setattr(agent, field, value)

    await db.commit()
    await db.refresh(agent)
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
    await db.refresh(agent)
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
    await db.refresh(agent)
    return ApiResponse(data=_agent_to_response(agent).model_dump())

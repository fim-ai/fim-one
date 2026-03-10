"""Admin endpoints for global agent and knowledge base management."""

from __future__ import annotations

import json
import logging
import math
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fim_agent.db import get_session
from fim_agent.web.auth import get_current_admin
from fim_agent.web.exceptions import AppError
from fim_agent.web.models import Agent, KnowledgeBase, User
from fim_agent.web.schemas.common import PaginatedResponse

from fim_agent.web.api.admin_utils import write_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

# ---------------------------------------------------------------------------
# Upload directory (mirrors knowledge_bases.py layout)
# ---------------------------------------------------------------------------

_KB_UPLOADS_DIR = Path("uploads") / "kb"

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AdminAgentInfo(BaseModel):
    id: str
    name: str
    icon: str | None = None
    description: str | None = None
    execution_mode: str = "react"
    status: str = "draft"
    user_id: str
    username: str | None = None
    email: str | None = None
    model_name: str | None = None
    tools: str | None = None
    kb_ids: str | None = None
    enable_planning: bool = False
    created_at: str


class AdminGlobalAgentInfo(BaseModel):
    id: str
    name: str
    icon: str | None = None
    description: str | None = None
    instructions: str | None = None
    execution_mode: str = "react"
    status: str = "draft"
    is_global: bool = True
    is_active: bool = True
    visibility: str = "personal"
    org_id: str | None = None
    user_id: str | None = None
    username: str | None = None
    email: str | None = None
    model_name: str | None = None
    model_config_json: dict[str, Any] | None = None
    tools: str | None = None
    tool_categories: list[str] | None = None
    suggested_prompts: list[str] | None = None
    sandbox_config: dict[str, Any] | None = None
    kb_ids: str | None = None
    enable_planning: bool = False
    cloned_from_agent_id: str | None = None
    cloned_from_user_id: str | None = None
    cloned_from_username: str | None = None
    created_at: str


class UpdateGlobalAgentRequest(BaseModel):
    name: str | None = None
    icon: str | None = None
    description: str | None = None
    instructions: str | None = None
    execution_mode: str | None = None
    model_config_json: dict[str, Any] | None = None
    tool_categories: list[str] | None = None
    suggested_prompts: list[str] | None = None
    sandbox_config: dict[str, Any] | None = None


class SetVisibilityRequest(BaseModel):
    visibility: str  # "personal", "org", "global"
    org_id: str | None = None


class AdminKBInfo(BaseModel):
    id: str
    name: str
    description: str | None = None
    chunk_strategy: str = "recursive"
    chunk_size: int = 1000
    chunk_overlap: int = 200
    retrieval_mode: str = "hybrid"
    document_count: int = 0
    total_chunks: int = 0
    status: str = "active"
    user_id: str
    username: str | None = None
    email: str | None = None
    embedding_model: str | None = None
    created_at: str


class AdminKBDocumentInfo(BaseModel):
    id: str
    filename: str
    file_size: int = 0
    file_type: str
    chunk_count: int = 0
    status: str = "processing"
    error_message: str | None = None
    created_at: str


class AdminKBDetailResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    chunk_strategy: str = "recursive"
    chunk_size: int = 1000
    chunk_overlap: int = 200
    retrieval_mode: str = "hybrid"
    document_count: int = 0
    total_chunks: int = 0
    status: str = "active"
    user_id: str
    username: str | None = None
    email: str | None = None
    embedding_model: str | None = None
    created_at: str
    documents: list[AdminKBDocumentInfo]


# ---------------------------------------------------------------------------
# Agent Management
# ---------------------------------------------------------------------------


@router.get("/agents", response_model=PaginatedResponse)
async def list_all_agents(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    """List all user-owned agents (excludes global). Requires admin privileges."""
    non_global = Agent.is_global == False  # noqa: E712
    stmt = select(Agent, User).join(User, Agent.user_id == User.id).where(non_global)
    count_base = select(Agent).where(non_global)

    if q:
        pattern = f"%{q}%"
        filter_clause = Agent.name.ilike(pattern)
        stmt = stmt.where(filter_clause)
        count_base = count_base.where(filter_clause)

    count_stmt = select(func.count()).select_from(count_base.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    rows = (
        await db.execute(
            stmt.order_by(Agent.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()

    items = []
    for agent, user in rows:
        # Extract model_name from model_config_json dict
        model_name = None
        if agent.model_config_json and isinstance(agent.model_config_json, dict):
            model_name = agent.model_config_json.get("model_name")

        items.append(
            AdminAgentInfo(
                id=agent.id,
                name=agent.name,
                icon=agent.icon,
                description=agent.description,
                execution_mode=agent.execution_mode,
                status=agent.status,
                user_id=user.id,
                username=user.username,
                email=user.email,
                model_name=model_name,
                tools=json.dumps(agent.tool_categories) if agent.tool_categories else None,
                kb_ids=json.dumps(agent.kb_ids) if agent.kb_ids else None,
                enable_planning=agent.execution_mode == "dag",
                created_at=agent.created_at.isoformat() if agent.created_at else "",
            ).model_dump()
        )

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total > 0 else 1,
    )


@router.patch("/agents/{agent_id}/active", response_model=AdminAgentInfo)
async def toggle_agent_active(
    agent_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> AdminAgentInfo:
    """Toggle agent visibility (placeholder for future is_global field)."""
    result = await db.execute(
        select(Agent, User)
        .join(User, Agent.user_id == User.id)
        .where(Agent.id == agent_id)
    )
    row = result.one_or_none()
    if row is None:
        raise AppError("agent_not_found", status_code=404)

    agent, user = row

    # Extract model_name from model_config_json dict
    model_name = None
    if agent.model_config_json and isinstance(agent.model_config_json, dict):
        model_name = agent.model_config_json.get("model_name")

    return AdminAgentInfo(
        id=agent.id,
        name=agent.name,
        icon=agent.icon,
        description=agent.description,
        execution_mode=agent.execution_mode,
        status=agent.status,
        user_id=user.id,
        username=user.username,
        email=user.email,
        model_name=model_name,
        tools=json.dumps(agent.tool_categories) if agent.tool_categories else None,
        kb_ids=json.dumps(agent.kb_ids) if agent.kb_ids else None,
        enable_planning=agent.execution_mode == "dag",
        created_at=agent.created_at.isoformat() if agent.created_at else "",
    )


@router.delete("/agents/{agent_id}", status_code=204)
async def admin_delete_agent(
    agent_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """Delete any agent by ID. Requires admin privileges."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise AppError("agent_not_found", status_code=404)

    agent_name = agent.name
    await db.delete(agent)
    await db.commit()

    await write_audit(
        db,
        current_user,
        "agent.admin_delete",
        target_type="agent",
        target_id=agent_id,
        target_label=agent_name,
    )


# ---------------------------------------------------------------------------
# Global Agent Management
# ---------------------------------------------------------------------------


def _extract_model_name(agent: Agent) -> str | None:
    """Extract model_name from the agent's model_config_json dict."""
    if agent.model_config_json and isinstance(agent.model_config_json, dict):
        return agent.model_config_json.get("model_name")
    return None


async def _resolve_username(db: AsyncSession, user_id: str | None) -> str | None:
    """Look up a username by user_id. Returns None if user_id is None or not found."""
    if not user_id:
        return None
    result = await db.execute(select(User.username).where(User.id == user_id))
    return result.scalar_one_or_none()


def _agent_to_global_info(
    agent: Agent,
    *,
    cloned_from_username: str | None = None,
    owner_username: str | None = None,
    owner_email: str | None = None,
) -> AdminGlobalAgentInfo:
    """Convert an Agent ORM object to an AdminGlobalAgentInfo schema."""
    return AdminGlobalAgentInfo(
        id=agent.id,
        name=agent.name,
        icon=agent.icon,
        description=agent.description,
        instructions=agent.instructions,
        execution_mode=agent.execution_mode,
        status=agent.status,
        is_global=agent.is_global,
        is_active=agent.status == "published",
        visibility=getattr(agent, "visibility", "personal"),
        org_id=getattr(agent, "org_id", None),
        user_id=agent.user_id,
        username=owner_username,
        email=owner_email,
        model_name=_extract_model_name(agent),
        model_config_json=agent.model_config_json,
        tools=json.dumps(agent.tool_categories) if agent.tool_categories else None,
        tool_categories=agent.tool_categories,
        suggested_prompts=agent.suggested_prompts,
        sandbox_config=agent.sandbox_config,
        kb_ids=json.dumps(agent.kb_ids) if agent.kb_ids else None,
        enable_planning=agent.execution_mode == "dag",
        cloned_from_agent_id=agent.cloned_from_agent_id,
        cloned_from_user_id=agent.cloned_from_user_id,
        cloned_from_username=cloned_from_username,
        created_at=agent.created_at.isoformat() if agent.created_at else "",
    )


@router.get("/global-agents", response_model=PaginatedResponse)
async def list_global_agents(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    """List all global agents. Requires admin privileges."""
    base_filter = or_(Agent.is_global == True, Agent.visibility == "global")  # noqa: E712
    stmt = select(Agent).where(base_filter)
    count_base = select(Agent).where(base_filter)

    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(Agent.name.ilike(pattern))
        count_base = count_base.where(Agent.name.ilike(pattern))

    count_stmt = select(func.count()).select_from(count_base.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    rows = (
        await db.execute(
            stmt.order_by(Agent.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).scalars().all()

    # Batch-resolve cloned_from usernames to avoid N+1
    clone_user_ids = {a.cloned_from_user_id for a in rows if a.cloned_from_user_id}
    username_map: dict[str, str] = {}
    if clone_user_ids:
        user_rows = (
            await db.execute(
                select(User.id, User.username).where(User.id.in_(clone_user_ids))
            )
        ).all()
        username_map = {uid: uname for uid, uname in user_rows}

    items = [
        _agent_to_global_info(
            agent,
            cloned_from_username=username_map.get(agent.cloned_from_user_id or ""),
        ).model_dump()
        for agent in rows
    ]

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total > 0 else 1,
    )


@router.post(
    "/global-agents/clone/{agent_id}",
    response_model=AdminGlobalAgentInfo,
    status_code=201,
)
async def clone_agent_to_global(
    agent_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> AdminGlobalAgentInfo:
    """Clone a user agent to a global agent. Requires admin privileges."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    source = result.scalar_one_or_none()
    if source is None:
        raise AppError("agent_not_found", status_code=404)

    global_agent = Agent(
        user_id=None,
        is_global=True,
        visibility="global",
        name=source.name,
        icon=source.icon,
        description=source.description,
        instructions=source.instructions,
        execution_mode=source.execution_mode,
        model_config_json=source.model_config_json,
        tool_categories=source.tool_categories,
        suggested_prompts=source.suggested_prompts,
        kb_ids=[],
        connector_ids=[],
        grounding_config=None,
        sandbox_config=source.sandbox_config,
        status="published",
        cloned_from_agent_id=source.id,
        cloned_from_user_id=source.user_id,
    )
    db.add(global_agent)
    await db.commit()
    await db.refresh(global_agent)

    cloned_from_username = await _resolve_username(db, source.user_id)

    await write_audit(
        db,
        current_user,
        "agent.clone_to_global",
        target_type="agent",
        target_id=global_agent.id,
        target_label=global_agent.name,
        detail=f"Cloned from agent {source.id} (user {source.user_id})",
    )

    return _agent_to_global_info(
        global_agent, cloned_from_username=cloned_from_username
    )


@router.put("/global-agents/{agent_id}", response_model=AdminGlobalAgentInfo)
async def update_global_agent(
    agent_id: str,
    body: UpdateGlobalAgentRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> AdminGlobalAgentInfo:
    """Update a global agent. Requires admin privileges."""
    result = await db.execute(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.is_global == True,  # noqa: E712
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise AppError("global_agent_not_found", status_code=404)

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(agent, field, value)

    await db.commit()
    await db.refresh(agent)

    cloned_from_username = await _resolve_username(db, agent.cloned_from_user_id)

    await write_audit(
        db,
        current_user,
        "agent.update_global",
        target_type="agent",
        target_id=agent.id,
        target_label=agent.name,
    )

    return _agent_to_global_info(agent, cloned_from_username=cloned_from_username)


@router.delete("/global-agents/{agent_id}", status_code=204)
async def delete_global_agent(
    agent_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """Delete a global agent. Requires admin privileges."""
    result = await db.execute(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.is_global == True,  # noqa: E712
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise AppError("global_agent_not_found", status_code=404)

    agent_name = agent.name
    await db.delete(agent)
    await db.commit()

    await write_audit(
        db,
        current_user,
        "agent.delete_global",
        target_type="agent",
        target_id=agent_id,
        target_label=agent_name,
    )


@router.post("/global-agents/{agent_id}/toggle", response_model=AdminGlobalAgentInfo)
async def toggle_global_agent(
    agent_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> AdminGlobalAgentInfo:
    """Toggle a global agent between published (active) and draft (inactive)."""
    result = await db.execute(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.is_global == True,  # noqa: E712
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise AppError("global_agent_not_found", status_code=404)

    agent.status = "draft" if agent.status == "published" else "published"
    await db.commit()
    await db.refresh(agent)

    cloned_from_username = await _resolve_username(db, agent.cloned_from_user_id)

    await write_audit(
        db,
        current_user,
        "agent.toggle_global",
        target_type="agent",
        target_id=agent.id,
        target_label=agent.name,
        detail=f"Status changed to {agent.status}",
    )

    return _agent_to_global_info(agent, cloned_from_username=cloned_from_username)


# ---------------------------------------------------------------------------
# Agent Visibility Management
# ---------------------------------------------------------------------------


@router.post("/resources/agent/{agent_id}/set-visibility", response_model=AdminGlobalAgentInfo)
async def admin_set_agent_visibility(
    agent_id: str,
    body: SetVisibilityRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> AdminGlobalAgentInfo:
    """Set visibility on any agent. Admin-only."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise AppError("agent_not_found", status_code=404)

    agent.visibility = body.visibility
    agent.org_id = body.org_id if body.visibility == "org" else None
    agent.is_global = body.visibility == "global"  # backward compat

    if body.visibility == "global" and agent.status != "published":
        agent.status = "published"

    await db.commit()
    await db.refresh(agent)

    owner_username = await _resolve_username(db, agent.user_id)
    cloned_from_username = await _resolve_username(db, agent.cloned_from_user_id)

    await write_audit(
        db, current_user, "agent.set_visibility",
        target_type="agent", target_id=agent.id, target_label=agent.name,
        detail=f"Visibility set to {body.visibility}",
    )

    return _agent_to_global_info(
        agent, cloned_from_username=cloned_from_username,
        owner_username=owner_username, owner_email=None,
    )


# ---------------------------------------------------------------------------
# Knowledge Base Management
# ---------------------------------------------------------------------------


@router.get("/knowledge-bases", response_model=PaginatedResponse)
async def list_all_knowledge_bases(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    """List all knowledge bases across all users. Requires admin privileges."""
    stmt = select(KnowledgeBase, User).join(User, KnowledgeBase.user_id == User.id)
    count_base = select(KnowledgeBase)

    if q:
        pattern = f"%{q}%"
        filter_clause = KnowledgeBase.name.ilike(pattern)
        stmt = stmt.where(filter_clause)
        count_base = count_base.where(filter_clause)

    count_stmt = select(func.count()).select_from(count_base.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    rows = (
        await db.execute(
            stmt.order_by(KnowledgeBase.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()

    items = [
        AdminKBInfo(
            id=kb.id,
            name=kb.name,
            description=kb.description,
            chunk_strategy=kb.chunk_strategy,
            chunk_size=kb.chunk_size,
            chunk_overlap=kb.chunk_overlap,
            retrieval_mode=kb.retrieval_mode,
            document_count=kb.document_count,
            total_chunks=kb.total_chunks,
            status=kb.status,
            user_id=user.id,
            username=user.username,
            email=user.email,
            embedding_model=None,
            created_at=kb.created_at.isoformat() if kb.created_at else "",
        ).model_dump()
        for kb, user in rows
    ]

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total > 0 else 1,
    )


@router.get("/knowledge-bases/{kb_id}", response_model=AdminKBDetailResponse)
async def admin_get_knowledge_base(
    kb_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> AdminKBDetailResponse:
    """Get KB detail with documents. Requires admin privileges."""
    result = await db.execute(
        select(KnowledgeBase, User)
        .join(User, KnowledgeBase.user_id == User.id)
        .options(selectinload(KnowledgeBase.documents))
        .where(KnowledgeBase.id == kb_id)
    )
    row = result.one_or_none()
    if row is None:
        raise AppError("kb_not_found", status_code=404)

    kb, user = row
    documents = [
        AdminKBDocumentInfo(
            id=doc.id,
            filename=doc.filename,
            file_size=doc.file_size,
            file_type=doc.file_type,
            chunk_count=doc.chunk_count,
            status=doc.status,
            error_message=doc.error_message,
            created_at=doc.created_at.isoformat() if doc.created_at else "",
        )
        for doc in kb.documents
    ]

    return AdminKBDetailResponse(
        id=kb.id,
        name=kb.name,
        description=kb.description,
        chunk_strategy=kb.chunk_strategy,
        chunk_size=kb.chunk_size,
        chunk_overlap=kb.chunk_overlap,
        retrieval_mode=kb.retrieval_mode,
        document_count=kb.document_count,
        total_chunks=kb.total_chunks,
        status=kb.status,
        user_id=user.id,
        username=user.username,
        email=user.email,
        embedding_model=None,
        created_at=kb.created_at.isoformat() if kb.created_at else "",
        documents=documents,
    )


@router.delete("/knowledge-bases/{kb_id}", status_code=204)
async def admin_delete_knowledge_base(
    kb_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """Delete any knowledge base by ID. Requires admin privileges."""
    result = await db.execute(
        select(KnowledgeBase)
        .options(selectinload(KnowledgeBase.documents))
        .where(KnowledgeBase.id == kb_id)
    )
    kb = result.scalar_one_or_none()
    if kb is None:
        raise AppError("kb_not_found", status_code=404)

    kb_name = kb.name
    kb_user_id = kb.user_id

    # Delete vectors via KB manager (best-effort)
    try:
        from fim_agent.web.deps import get_kb_manager

        manager = get_kb_manager()
        await manager.delete_kb(kb_id=kb_id, user_id=kb_user_id)
    except Exception:
        logger.warning("Failed to delete vector data for KB %s", kb_id, exc_info=True)

    # Delete uploaded files from disk (best-effort)
    try:
        upload_dir = _KB_UPLOADS_DIR / kb_id
        if upload_dir.exists():
            shutil.rmtree(upload_dir)
    except Exception:
        logger.warning(
            "Failed to delete upload directory for KB %s", kb_id, exc_info=True
        )

    # Delete DB records (cascade deletes documents)
    await db.delete(kb)
    await db.commit()

    await write_audit(
        db,
        current_user,
        "kb.admin_delete",
        target_type="knowledge_base",
        target_id=kb_id,
        target_label=kb_name,
    )

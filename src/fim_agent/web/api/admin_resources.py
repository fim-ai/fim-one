"""Admin endpoints for global agent and knowledge base management."""

from __future__ import annotations

import logging
import math
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
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
    owner_username: str
    owner_email: str
    created_at: str


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
    owner_username: str
    owner_email: str
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
    owner_username: str
    owner_email: str
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
    """List all agents across all users. Requires admin privileges."""
    stmt = select(Agent, User).join(User, Agent.user_id == User.id)
    count_base = select(Agent)

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

    items = [
        AdminAgentInfo(
            id=agent.id,
            name=agent.name,
            icon=agent.icon,
            description=agent.description,
            execution_mode=agent.execution_mode,
            status=agent.status,
            user_id=user.id,
            owner_username=user.username or user.email,
            owner_email=user.email,
            created_at=agent.created_at.isoformat() if agent.created_at else "",
        ).model_dump()
        for agent, user in rows
    ]

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
    return AdminAgentInfo(
        id=agent.id,
        name=agent.name,
        icon=agent.icon,
        description=agent.description,
        execution_mode=agent.execution_mode,
        status=agent.status,
        user_id=user.id,
        owner_username=user.username or user.email,
        owner_email=user.email,
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
            owner_username=user.username or user.email,
            owner_email=user.email,
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
        owner_username=user.username or user.email,
        owner_email=user.email,
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

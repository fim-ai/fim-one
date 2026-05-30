"""Admin endpoints for batch operations on agents, knowledge bases, and connectors."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.auth import get_current_admin
from fim_one.db.models import Agent, Connector, KnowledgeBase, User
from fim_one.web.schemas.workflow import BatchOperationResponse

from fim_one.web.api.admin_utils import write_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

_KB_UPLOADS_DIR = Path("uploads") / "kb"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class BatchToggleRequest(BaseModel):
    ids: list[str] = Field(..., min_length=1, max_length=100)
    is_active: bool


class BatchDeleteRequest(BaseModel):
    ids: list[str] = Field(..., min_length=1, max_length=100)


class BatchResultResponse(BaseModel):
    success_count: int
    failed_count: int
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Agent Batch Operations
# ---------------------------------------------------------------------------


@router.post("/agents/batch-toggle", response_model=BatchResultResponse)
async def batch_toggle_agents(
    body: BatchToggleRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> BatchResultResponse:
    """Batch toggle agents active status. Requires admin privileges."""
    result = await db.execute(
        select(Agent).where(Agent.id.in_(body.ids))
    )
    agents = result.scalars().all()

    found_ids = {a.id for a in agents}
    missing_ids = set(body.ids) - found_ids

    count = 0
    for agent in agents:
        agent.is_active = body.is_active
        count += 1

    await db.commit()

    errors = [f"Agent {aid} not found" for aid in missing_ids]

    if count > 0:
        await write_audit(
            db,
            current_user,
            "agent.admin_batch_toggle",
            target_type="agent",
            detail=f"Set is_active={body.is_active} for {count} agent(s)",
        )

    return BatchResultResponse(
        success_count=count,
        failed_count=len(errors),
        errors=errors,
    )


@router.post("/agents/batch-delete", response_model=BatchResultResponse)
async def batch_delete_agents(
    body: BatchDeleteRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> BatchResultResponse:
    """Batch delete agents. Requires admin privileges."""
    result = await db.execute(
        select(Agent).where(Agent.id.in_(body.ids))
    )
    agents = result.scalars().all()

    found_ids = {a.id for a in agents}
    missing_ids = set(body.ids) - found_ids

    count = 0
    deleted_names: list[str] = []
    for agent in agents:
        deleted_names.append(agent.name)
        await db.delete(agent)
        count += 1

    await db.commit()

    errors = [f"Agent {aid} not found" for aid in missing_ids]

    if count > 0:
        await write_audit(
            db,
            current_user,
            "agent.admin_batch_delete",
            target_type="agent",
            detail=f"Deleted {count} agent(s): {', '.join(deleted_names[:10])}",
        )

    return BatchResultResponse(
        success_count=count,
        failed_count=len(errors),
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Knowledge Base Batch Operations
# ---------------------------------------------------------------------------


@router.post("/knowledge-bases/batch-toggle", response_model=BatchResultResponse)
async def batch_toggle_knowledge_bases(
    body: BatchToggleRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> BatchResultResponse:
    """Batch toggle knowledge bases active status. Requires admin privileges."""
    result = await db.execute(
        select(KnowledgeBase).where(KnowledgeBase.id.in_(body.ids))
    )
    kbs = result.scalars().all()

    found_ids = {kb.id for kb in kbs}
    missing_ids = set(body.ids) - found_ids

    count = 0
    for kb in kbs:
        kb.is_active = body.is_active
        count += 1

    await db.commit()

    errors = [f"KB {kid} not found" for kid in missing_ids]

    if count > 0:
        await write_audit(
            db,
            current_user,
            "kb.admin_batch_toggle",
            target_type="knowledge_base",
            detail=f"Set is_active={body.is_active} for {count} KB(s)",
        )

    return BatchResultResponse(
        success_count=count,
        failed_count=len(errors),
        errors=errors,
    )


@router.post("/knowledge-bases/batch-delete", response_model=BatchResultResponse)
async def batch_delete_knowledge_bases(
    body: BatchDeleteRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> BatchResultResponse:
    """Batch delete knowledge bases. Requires admin privileges."""
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(KnowledgeBase)
        .options(selectinload(KnowledgeBase.documents))
        .where(KnowledgeBase.id.in_(body.ids))
    )
    kbs = result.scalars().all()

    found_ids = {kb.id for kb in kbs}
    missing_ids = set(body.ids) - found_ids

    count = 0
    deleted_names: list[str] = []
    for kb in kbs:
        deleted_names.append(kb.name)

        # Delete vectors via KB manager (best-effort)
        try:
            from fim_one.web.deps import get_kb_manager

            manager = get_kb_manager()
            await manager.delete_kb(kb_id=kb.id, user_id=kb.user_id)
        except Exception:
            logger.warning("Failed to delete vector data for KB %s", kb.id, exc_info=True)

        # Delete uploaded files from disk (best-effort)
        try:
            upload_dir = _KB_UPLOADS_DIR / kb.id
            if upload_dir.exists():
                shutil.rmtree(upload_dir)
        except Exception:
            logger.warning("Failed to delete upload dir for KB %s", kb.id, exc_info=True)

        await db.delete(kb)
        count += 1

    await db.commit()

    errors = [f"KB {kid} not found" for kid in missing_ids]

    if count > 0:
        await write_audit(
            db,
            current_user,
            "kb.admin_batch_delete",
            target_type="knowledge_base",
            detail=f"Deleted {count} KB(s): {', '.join(deleted_names[:10])}",
        )

    return BatchResultResponse(
        success_count=count,
        failed_count=len(errors),
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Connector Batch Operations
# ---------------------------------------------------------------------------


@router.post("/connectors/batch-toggle", response_model=BatchResultResponse)
async def batch_toggle_connectors(
    body: BatchToggleRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> BatchResultResponse:
    """Batch toggle connectors active status. Requires admin privileges."""
    result = await db.execute(
        select(Connector).where(Connector.id.in_(body.ids))
    )
    connectors = result.scalars().all()

    found_ids = {c.id for c in connectors}
    missing_ids = set(body.ids) - found_ids

    count = 0
    for connector in connectors:
        connector.is_active = body.is_active
        count += 1

    await db.commit()

    errors = [f"Connector {cid} not found" for cid in missing_ids]

    if count > 0:
        await write_audit(
            db,
            current_user,
            "connector.admin_batch_toggle",
            target_type="connector",
            detail=f"Set is_active={body.is_active} for {count} connector(s)",
        )

    return BatchResultResponse(
        success_count=count,
        failed_count=len(errors),
        errors=errors,
    )


@router.post("/connectors/batch-delete", response_model=BatchResultResponse)
async def batch_delete_connectors(
    body: BatchDeleteRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> BatchResultResponse:
    """Batch delete connectors. Requires admin privileges."""
    result = await db.execute(
        select(Connector).where(Connector.id.in_(body.ids))
    )
    connectors = result.scalars().all()

    found_ids = {c.id for c in connectors}
    missing_ids = set(body.ids) - found_ids

    count = 0
    deleted_names: list[str] = []
    for connector in connectors:
        deleted_names.append(connector.name)
        await db.delete(connector)
        count += 1

    await db.commit()

    errors = [f"Connector {cid} not found" for cid in missing_ids]

    if count > 0:
        await write_audit(
            db,
            current_user,
            "connector.admin_batch_delete",
            target_type="connector",
            detail=f"Deleted {count} connector(s): {', '.join(deleted_names[:10])}",
        )

    return BatchResultResponse(
        success_count=count,
        failed_count=len(errors),
        errors=errors,
    )

"""Conversation CRUD endpoints with message history."""

from __future__ import annotations

import asyncio
import logging
import os
import math
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fim_agent.db import get_session
from fim_agent.web.exceptions import AppError
from fim_agent.web.auth import get_current_user
from fim_agent.web.models import Agent, Conversation, Message, User
from fim_agent.web.schemas.common import ApiResponse, PaginatedResponse
from fim_agent.web.schemas.conversation import (
    BatchDeleteRequest,
    ConversationCreate,
    ConversationDetail,
    ConversationResponse,
    ConversationUpdate,
    MessageResponse,
)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CONVERSATIONS_DIR = _PROJECT_ROOT.parent / "data" / "sandbox"
_uploads_base = Path(os.environ.get("UPLOADS_DIR", "uploads"))
_UPLOADS_CONVERSATIONS_DIR = (
    _uploads_base if _uploads_base.is_absolute() else _PROJECT_ROOT / _uploads_base
) / "conversations"
_logger = logging.getLogger(__name__)


def _conv_to_response(conv: Conversation) -> ConversationResponse:
    return ConversationResponse(
        id=conv.id,
        title=conv.title,
        mode=conv.mode,
        agent_id=conv.agent_id,
        status=conv.status,
        starred=conv.starred,
        model_name=conv.model_name,
        total_tokens=conv.total_tokens,
        created_at=conv.created_at.isoformat() if conv.created_at else "",
        updated_at=conv.updated_at.isoformat() if conv.updated_at else None,
    )


def _msg_to_response(msg: Message) -> MessageResponse:
    return MessageResponse(
        id=msg.id,
        role=msg.role,
        content=msg.content,
        message_type=msg.message_type,
        metadata=msg.metadata_,
        created_at=msg.created_at.isoformat() if msg.created_at else "",
    )


async def _get_owned_conversation(
    conversation_id: str,
    user_id: str,
    db: AsyncSession,
) -> Conversation:
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )
    conv = result.scalar_one_or_none()
    if conv is None:
        raise AppError("conversation_not_found", status_code=404)
    return conv


@router.post("", response_model=ApiResponse)
async def create_conversation(
    body: ConversationCreate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    # Resolve model_name: body > agent config > env fallback
    model_name = body.model_name
    if not model_name and body.agent_id:
        result = await db.execute(
            select(Agent.model_config_json).where(Agent.id == body.agent_id)
        )
        model_cfg = result.scalar_one_or_none()
        if model_cfg and isinstance(model_cfg, dict):
            model_name = model_cfg.get("model_name") or model_cfg.get("model")
    if not model_name:
        model_name = os.environ.get("LLM_MODEL", "") or None

    conv = Conversation(
        user_id=current_user.id,
        title=body.title,
        mode=body.mode,
        agent_id=body.agent_id,
        model_name=model_name,
    )
    db.add(conv)
    await db.commit()
    result = await db.execute(
        select(Conversation).where(Conversation.id == conv.id)
    )
    conv = result.scalar_one()
    return ApiResponse(data=_conv_to_response(conv).model_dump())


@router.get("", response_model=PaginatedResponse)
async def list_conversations(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    conv_status: str = Query("active", alias="status"),
    q: str | None = Query(None, min_length=1, max_length=200),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    base = select(Conversation).where(
        Conversation.user_id == current_user.id,
        Conversation.status == conv_status,
    )

    if q:
        pattern = f"%{q}%"
        base = (
            base.outerjoin(Message, Message.conversation_id == Conversation.id)
            .where(
                or_(
                    Conversation.title.ilike(pattern),
                    Message.content.ilike(pattern),
                )
            )
            .distinct()
        )

    count_result = await db.execute(
        select(func.count()).select_from(base.subquery())
    )
    total = count_result.scalar_one()

    result = await db.execute(
        base.order_by(Conversation.starred.desc(), Conversation.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    conversations = result.scalars().all()

    return PaginatedResponse(
        items=[_conv_to_response(c).model_dump() for c in conversations],
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total else 0,
    )


@router.delete("/batch", response_model=ApiResponse)
async def batch_delete_conversations(
    body: BatchDeleteRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    result = await db.execute(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(
            Conversation.id.in_(body.ids),
            Conversation.user_id == current_user.id,
        )
    )
    conversations = result.scalars().all()
    count = 0
    for conv in conversations:
        await db.delete(conv)
        sandbox_dir = _CONVERSATIONS_DIR / conv.id
        if sandbox_dir.exists():
            await asyncio.to_thread(shutil.rmtree, sandbox_dir, True)
            _logger.info("Removed sandbox dir for conversation %s", conv.id)
        uploads_dir = _UPLOADS_CONVERSATIONS_DIR / conv.id
        if uploads_dir.exists():
            await asyncio.to_thread(shutil.rmtree, uploads_dir, True)
            _logger.info("Removed uploads dir for conversation %s", conv.id)
        count += 1
    await db.commit()
    return ApiResponse(data={"deleted": count})


@router.get("/{conversation_id}", response_model=ApiResponse)
async def get_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    result = await db.execute(
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
        )
        .options(selectinload(Conversation.messages))
    )
    conv = result.scalar_one_or_none()
    if conv is None:
        raise AppError("conversation_not_found", status_code=404)

    messages_sorted = sorted(conv.messages, key=lambda m: m.created_at)
    detail = ConversationDetail(
        id=conv.id,
        title=conv.title,
        mode=conv.mode,
        agent_id=conv.agent_id,
        status=conv.status,
        starred=conv.starred,
        model_name=conv.model_name,
        total_tokens=conv.total_tokens,
        created_at=conv.created_at.isoformat() if conv.created_at else "",
        updated_at=conv.updated_at.isoformat() if conv.updated_at else None,
        messages=[_msg_to_response(m) for m in messages_sorted],
    )
    return ApiResponse(data=detail.model_dump())


@router.patch("/{conversation_id}", response_model=ApiResponse)
async def update_conversation(
    conversation_id: str,
    body: ConversationUpdate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    conv = await _get_owned_conversation(conversation_id, current_user.id, db)

    if body.title is not None:
        conv.title = body.title
    if body.status is not None:
        conv.status = body.status
    if body.starred is not None:
        conv.starred = body.starred

    await db.commit()
    result = await db.execute(
        select(Conversation).where(Conversation.id == conv.id)
    )
    conv = result.scalar_one()
    return ApiResponse(data=_conv_to_response(conv).model_dump())


@router.delete("/{conversation_id}", response_model=ApiResponse)
async def delete_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    # Load with messages for ORM cascade delete
    result = await db.execute(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
        )
    )
    conv = result.scalar_one_or_none()
    if conv is None:
        raise AppError("conversation_not_found", status_code=404)
    await db.delete(conv)
    await db.commit()

    # Clean up per-conversation sandbox directory (workspace, sandbox, exec).
    sandbox_dir = _CONVERSATIONS_DIR / conversation_id
    if sandbox_dir.exists():
        await asyncio.to_thread(shutil.rmtree, sandbox_dir, True)
        _logger.info("Removed sandbox dir for conversation %s", conversation_id)

    # Clean up per-conversation uploads directory (generated images, etc.).
    uploads_dir = _UPLOADS_CONVERSATIONS_DIR / conversation_id
    if uploads_dir.exists():
        await asyncio.to_thread(shutil.rmtree, uploads_dir, True)
        _logger.info("Removed uploads dir for conversation %s", conversation_id)

    return ApiResponse(data={"deleted": conversation_id})

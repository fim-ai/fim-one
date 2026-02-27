"""Conversation CRUD endpoints with message history."""

from __future__ import annotations

import logging
import math
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fim_agent.db import get_session
from fim_agent.web.auth import get_current_user
from fim_agent.web.models import Conversation, Message, User
from fim_agent.web.schemas.common import ApiResponse, PaginatedResponse
from fim_agent.web.schemas.conversation import (
    ConversationCreate,
    ConversationDetail,
    ConversationResponse,
    ConversationUpdate,
    MessageResponse,
)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _conv_to_response(conv: Conversation) -> ConversationResponse:
    return ConversationResponse(
        id=conv.id,
        title=conv.title,
        mode=conv.mode,
        agent_id=conv.agent_id,
        status=conv.status,
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    return conv


@router.post("", response_model=ApiResponse)
async def create_conversation(
    body: ConversationCreate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    conv = Conversation(
        user_id=current_user.id,
        title=body.title,
        mode=body.mode,
        agent_id=body.agent_id,
        model_name=body.model_name,
    )
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return ApiResponse(data=_conv_to_response(conv).model_dump())


@router.get("", response_model=PaginatedResponse)
async def list_conversations(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    conv_status: str = Query("active", alias="status"),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    base = select(Conversation).where(
        Conversation.user_id == current_user.id,
        Conversation.status == conv_status,
    )

    count_result = await db.execute(
        select(func.count()).select_from(base.subquery())
    )
    total = count_result.scalar_one()

    result = await db.execute(
        base.order_by(Conversation.created_at.desc())
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    messages_sorted = sorted(conv.messages, key=lambda m: m.created_at)
    detail = ConversationDetail(
        id=conv.id,
        title=conv.title,
        mode=conv.mode,
        agent_id=conv.agent_id,
        status=conv.status,
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

    await db.commit()
    await db.refresh(conv)
    return ApiResponse(data=_conv_to_response(conv).model_dump())


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CONVERSATIONS_DIR = _PROJECT_ROOT / "tmp" / "conversations"
_logger = logging.getLogger(__name__)


@router.delete("/{conversation_id}", response_model=ApiResponse)
async def delete_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    conv = await _get_owned_conversation(conversation_id, current_user.id, db)
    await db.delete(conv)
    await db.commit()

    # Clean up per-conversation sandbox directory (workspace, sandbox, exec).
    sandbox_dir = _CONVERSATIONS_DIR / conversation_id
    if sandbox_dir.exists():
        shutil.rmtree(sandbox_dir, ignore_errors=True)
        _logger.info("Removed sandbox dir for conversation %s", conversation_id)

    return ApiResponse(data={"deleted": conversation_id})

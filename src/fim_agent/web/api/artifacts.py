"""Artifact listing and download endpoints for conversation tool outputs."""

from __future__ import annotations

import logging
import math
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from fim_agent.web.auth import decode_token, get_current_user
from fim_agent.web.exceptions import AppError
from fim_agent.web.models import User
from fim_agent.web.schemas.common import ApiResponse, PaginatedResponse

_bearer_optional = HTTPBearer(auto_error=False)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/conversations", tags=["artifacts"])
global_artifacts_router = APIRouter(prefix="/api", tags=["artifacts"])

UPLOAD_ROOT = Path(os.environ.get("UPLOADS_DIR", "uploads"))


def _artifacts_dir(conversation_id: str) -> Path:
    return UPLOAD_ROOT / "conversations" / conversation_id / "artifacts"


async def _validate_conversation_ownership(
    conversation_id: str, user_id: str,
) -> None:
    """Ensure the conversation belongs to *user_id*."""
    from sqlalchemy import select as sa_select

    from fim_agent.db import create_session
    from fim_agent.web.models import Conversation

    async with create_session() as session:
        result = await session.execute(
            sa_select(Conversation.id).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise AppError("conversation_not_found", status_code=404)


@router.get("/{conversation_id}/artifacts", response_model=ApiResponse)
async def list_artifacts(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
) -> ApiResponse:
    """List all artifacts for a conversation."""
    await _validate_conversation_ownership(conversation_id, current_user.id)
    d = _artifacts_dir(conversation_id)
    if not d.exists():
        return ApiResponse(data=[])

    artifacts = []
    for f in sorted(d.iterdir()):
        if not f.is_file():
            continue
        # stored_name format: {artifact_id}_{original_name}
        parts = f.name.split("_", 1)
        artifact_id = parts[0]
        original_name = parts[1] if len(parts) > 1 else f.name
        mime, _ = mimetypes.guess_type(str(f))
        artifacts.append({
            "id": artifact_id,
            "name": original_name,
            "mime_type": mime or "application/octet-stream",
            "size": f.stat().st_size,
            "url": f"/api/conversations/{conversation_id}/artifacts/{artifact_id}",
        })
    return ApiResponse(data=artifacts)


@router.get("/{conversation_id}/artifacts/{artifact_id}")
async def download_artifact(
    conversation_id: str,
    artifact_id: str,
    token: str | None = Query(None, description="JWT token (for img src auth)"),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_optional),
) -> FileResponse:
    """Download a specific artifact.

    Supports authentication via either:
    - ``Authorization: Bearer <token>`` header (standard API calls)
    - ``?token=<jwt>`` query parameter (for ``<img src>`` tags that cannot
      carry custom headers)
    """
    jwt_token = (credentials.credentials if credentials else None) or token
    if not jwt_token:
        raise AppError("not_authenticated", status_code=401)
    payload = decode_token(jwt_token)
    user_id = payload.get("sub")
    if not user_id:
        raise AppError("not_authenticated", status_code=401)
    await _validate_conversation_ownership(conversation_id, user_id)
    d = _artifacts_dir(conversation_id)
    if not d.exists():
        raise AppError("artifact_not_found", status_code=404)

    # Find file matching the artifact_id prefix.
    for f in d.iterdir():
        if f.name.startswith(f"{artifact_id}_"):
            parts = f.name.split("_", 1)
            original_name = parts[1] if len(parts) > 1 else f.name
            mime, _ = mimetypes.guess_type(str(f))
            return FileResponse(
                path=str(f),
                filename=original_name,
                media_type=mime or "application/octet-stream",
            )

    raise AppError("artifact_not_found", status_code=404)


@global_artifacts_router.get("/artifacts", response_model=PaginatedResponse)
async def list_all_artifacts(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
) -> PaginatedResponse:
    """List all artifacts across all conversations for the current user."""
    from sqlalchemy import select as sa_select

    from fim_agent.db import create_session
    from fim_agent.web.models import Conversation

    async with create_session() as session:
        result = await session.execute(
            sa_select(Conversation.id, Conversation.title, Conversation.created_at).where(
                Conversation.user_id == current_user.id,
            )
        )
        conversations = result.all()

    artifacts: list[dict] = []
    for conv_id, conv_title, conv_created_at in conversations:
        artifacts_dir = _artifacts_dir(conv_id)
        if not artifacts_dir.exists():
            continue
        # Use conversation creation time as the baseline; file mtime is
        # unreliable after migration / scp / backup-restore.
        conv_ts = (
            conv_created_at.replace(tzinfo=timezone.utc).isoformat()
            if conv_created_at
            else datetime.now(timezone.utc).isoformat()
        )
        for f in artifacts_dir.iterdir():
            if not f.is_file():
                continue
            parts = f.name.split("_", 1)
            artifact_id = parts[0]
            original_name = parts[1] if len(parts) > 1 else f.name
            mime, _ = mimetypes.guess_type(str(f))
            artifacts.append({
                "id": artifact_id,
                "name": original_name,
                "mime_type": mime or "application/octet-stream",
                "size": f.stat().st_size,
                "url": f"/api/conversations/{conv_id}/artifacts/{artifact_id}",
                "conversation_id": conv_id,
                "conversation_title": conv_title or "Untitled",
                "created_at": conv_ts,
            })

    artifacts.sort(key=lambda a: a["created_at"], reverse=True)
    total = len(artifacts)
    start = (page - 1) * size
    items = artifacts[start : start + size]
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total else 0,
    )

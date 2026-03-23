"""Artifact listing and download endpoints for conversation tool outputs."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from fim_one.web.auth import get_current_user
from fim_one.web.exceptions import AppError
from fim_one.web.models import User
from fim_one.web.schemas.common import ApiResponse, PaginatedResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/conversations", tags=["artifacts"])
global_artifacts_router = APIRouter(prefix="/api", tags=["artifacts"])

UPLOAD_ROOT = Path(os.environ.get("UPLOADS_DIR", "uploads"))

_FALLBACK_MIMES: dict[str, str] = {
    ".md": "text/markdown",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
    ".toml": "application/toml",
    ".csv": "text/csv",
}


def _guess_mime(path: str) -> str:
    """Guess MIME type, with fallbacks for extensions that mimetypes misses."""
    mime, _ = mimetypes.guess_type(path)
    if mime:
        return mime
    suffix = Path(path).suffix.lower()
    return _FALLBACK_MIMES.get(suffix, "application/octet-stream")


def _artifacts_dir(conversation_id: str) -> Path:
    return UPLOAD_ROOT / "conversations" / conversation_id / "artifacts"


async def _validate_conversation_ownership(
    conversation_id: str, user_id: str,
) -> None:
    """Ensure the conversation belongs to *user_id*."""
    from sqlalchemy import select as sa_select

    from fim_one.db import create_session
    from fim_one.web.models import Conversation

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
        artifacts.append({
            "id": artifact_id,
            "name": original_name,
            "mime_type": _guess_mime(str(f)),
            "size": f.stat().st_size,
            "url": f"/api/conversations/{conversation_id}/artifacts/{artifact_id}",
        })
    return ApiResponse(data=artifacts)


@router.get("/{conversation_id}/artifacts/{artifact_id}")
async def download_artifact(
    conversation_id: str,
    artifact_id: str,
) -> FileResponse:
    """Download a specific artifact.

    Public capability endpoint — access is controlled by the unguessable
    conversation_id (UUID v4, 122 bits of entropy).  No explicit auth token
    is required so that ``<img src>`` and direct browser navigation work
    without extra round-trips.
    """
    d = _artifacts_dir(conversation_id)
    if not d.exists():
        raise AppError("artifact_not_found", status_code=404)

    # Find file matching the artifact_id prefix.
    for f in d.iterdir():
        if f.name.startswith(f"{artifact_id}_"):
            parts = f.name.split("_", 1)
            original_name = parts[1] if len(parts) > 1 else f.name
            return FileResponse(
                path=str(f),
                filename=original_name,
                media_type=_guess_mime(str(f)),
            )

    raise AppError("artifact_not_found", status_code=404)


def _scan_conv_artifacts(conv_id: str, conv_title: str) -> list[dict[str, object]]:
    """Sync helper: scan one conversation's artifacts dir. Called via to_thread."""
    artifacts_dir = _artifacts_dir(conv_id)
    if not artifacts_dir.exists():
        return []
    results = []
    for f in artifacts_dir.iterdir():
        if not f.is_file():
            continue
        parts = f.name.split("_", 1)
        artifact_id = parts[0]
        original_name = parts[1] if len(parts) > 1 else f.name
        stat = f.stat()
        file_ts = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        results.append({
            "id": artifact_id,
            "name": original_name,
            "mime_type": _guess_mime(str(f)),
            "size": stat.st_size,
            "url": f"/api/conversations/{conv_id}/artifacts/{artifact_id}",
            "conversation_id": conv_id,
            "conversation_title": conv_title or "Untitled",
            "created_at": file_ts,
        })
    return results


def _matches_type(mime: str, artifact_type: str) -> bool:
    """Mirror the frontend getFilter() categorisation for server-side filtering."""
    if artifact_type == "images":
        return mime.startswith("image/")
    if artifact_type == "html":
        return mime == "text/html"
    if artifact_type == "code":
        return (mime.startswith("text/") or mime == "application/json") and mime != "text/html"
    if artifact_type == "files":
        return not (mime.startswith("image/") or mime.startswith("text/") or mime == "application/json")
    return True


@global_artifacts_router.get("/artifacts", response_model=PaginatedResponse)
async def list_all_artifacts(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    artifact_type: str | None = Query(None, alias="type"),
    current_user: User = Depends(get_current_user),
) -> PaginatedResponse:
    """List all artifacts across all conversations for the current user.

    Only deliverables (final outputs classified by the fast LLM) are returned.
    Conversations without deliverable metadata are excluded entirely to avoid
    exposing intermediate artifacts.
    """
    from sqlalchemy import select as sa_select

    from fim_one.db import create_session
    from fim_one.web.models import Conversation, Message

    async with create_session() as session:
        result = await session.execute(
            sa_select(Conversation.id, Conversation.title).where(
                Conversation.user_id == current_user.id,
            )
        )
        conversations = result.all()

    conv_ids = [conv_id for conv_id, _ in conversations]

    # ── Collect deliverable URLs from assistant messages ──────────────
    # Maps conversation_id → set of deliverable artifact URLs.
    # A conversation present in this dict (even with an empty set) means
    # it has deliverable metadata and should be filtered; absence means
    # the conversation pre-dates the feature and all artifacts are kept.
    conv_deliverable_urls: dict[str, set[str]] = {}

    if conv_ids:
        async with create_session() as session:
            msg_result = await session.execute(
                sa_select(Message.conversation_id, Message.metadata_).where(
                    Message.conversation_id.in_(conv_ids),
                    Message.role == "assistant",
                    Message.metadata_.isnot(None),
                )
            )
            for row_conv_id, raw_meta in msg_result.all():
                # metadata_ may already be a dict (JSON driver) or a string (SQLite).
                meta: dict[str, object] | None = None
                if isinstance(raw_meta, str):
                    try:
                        meta = json.loads(raw_meta)
                    except (json.JSONDecodeError, TypeError):
                        continue
                elif isinstance(raw_meta, dict):
                    meta = raw_meta
                if not meta:
                    continue

                deliverables = meta.get("deliverables")
                if deliverables is None:
                    # This assistant message has metadata but no deliverables
                    # key — skip it (doesn't count as "has deliverable info").
                    continue

                # Mark this conversation as having deliverable info.
                if row_conv_id not in conv_deliverable_urls:
                    conv_deliverable_urls[row_conv_id] = set()
                for d in deliverables if isinstance(deliverables, list) else []:
                    url = d.get("url")
                    if url:
                        conv_deliverable_urls[row_conv_id].add(url)

    # ── Filesystem scan (unchanged) ──────────────────────────────────
    # Offload all filesystem scans to the thread pool concurrently so the
    # event loop is never blocked by Path.iterdir() / f.stat() calls.
    nested = await asyncio.gather(*[
        asyncio.to_thread(_scan_conv_artifacts, conv_id, conv_title)
        for conv_id, conv_title in conversations
    ])
    artifacts: list[dict[str, object]] = [item for sublist in nested for item in sublist]

    # ── Filter to deliverables only ──────────────────────────────────
    # Only keep artifacts explicitly marked as deliverables.
    # Conversations without deliverable metadata are excluded entirely
    # to avoid exposing intermediate/dirty artifacts.
    all_deliverable_urls: set[str] = set()
    for urls in conv_deliverable_urls.values():
        all_deliverable_urls |= urls

    artifacts = [a for a in artifacts if a["url"] in all_deliverable_urls]

    artifacts.sort(key=lambda a: str(a["created_at"]), reverse=True)
    if artifact_type:
        artifacts = [a for a in artifacts if _matches_type(str(a["mime_type"]), artifact_type)]
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

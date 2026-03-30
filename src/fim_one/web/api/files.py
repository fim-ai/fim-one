"""File upload, download, and listing endpoints."""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import FileResponse

from fim_one.web.auth import get_current_user
from fim_one.web.exceptions import AppError
from fim_one.web.models import User
from fim_one.web.schemas.common import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/files", tags=["files"])

UPLOAD_ROOT = Path(os.environ.get("UPLOADS_DIR", "uploads"))
MAX_UPLOAD_SIZE_MB = int(os.environ.get("MAX_UPLOAD_SIZE_MB", "50"))
MAX_FILE_SIZE = MAX_UPLOAD_SIZE_MB * 1024 * 1024
ALLOWED_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".json", ".csv",
    ".pdf", ".docx", ".doc", ".html", ".htm",
    ".xlsx", ".xls", ".pptx", ".ppt",
    # Images (vision model support)
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg",
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}

_MIME_MAP: dict[str, str] = {
    # Images
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    # Text / code (mimetypes.guess_type may return None for these)
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".json": "application/json",
    ".csv": "text/csv",
    ".html": "text/html",
    ".htm": "text/html",
}


def _is_image(suffix: str) -> bool:
    """Check whether a file extension corresponds to an image type."""
    return suffix in IMAGE_EXTENSIONS


def _guess_mime(suffix: str) -> str:
    """Return the MIME type for a given file extension."""
    return _MIME_MAP.get(suffix, "application/octet-stream")


def _extract_content(file_path: Path) -> str | None:
    """Extract text content from an uploaded file for preview.

    Returns the extracted text, a fallback message if optional
    dependencies are missing, or *None* for unsupported types.

    Delegates to :func:`fim_one.core.document.processor._extract_text_sync`
    as the single source of truth for extraction logic.
    """
    from fim_one.core.document.processor import _extract_text_sync

    return _extract_text_sync(file_path)


def _user_dir(user_id: str) -> Path:
    return UPLOAD_ROOT / f"user_{user_id}"


def _index_path(user_id: str) -> Path:
    return _user_dir(user_id) / "index.json"


@asynccontextmanager
async def _file_lock(user_id: str) -> AsyncGenerator[None, None]:
    """Cross-process file lock using fcntl.flock()."""
    lock_path = _user_dir(user_id) / ".lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = open(lock_path, "w")
    try:
        await asyncio.to_thread(fcntl.flock, fd.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        fd.close()


def _load_index(user_id: str) -> dict[str, dict[str, object]]:
    path = _index_path(user_id)
    if not path.exists():
        return {}
    result: dict[str, dict[str, object]] = json.loads(path.read_text())
    return result


def _save_index(user_id: str, index: dict[str, dict[str, object]]) -> None:
    path = _index_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, ensure_ascii=False, indent=2))


@router.post("/upload", response_model=ApiResponse)
async def upload_file(
    file: UploadFile,
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> ApiResponse:
    if not file.filename:
        raise AppError("no_filename", status_code=400)

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise AppError(
            "unsupported_file_type",
            status_code=422,
            detail=f"File extension '{ext}' is not allowed. Accepted: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
            detail_args={"ext": ext},
        )

    user_dir = _user_dir(current_user.id)
    user_dir.mkdir(parents=True, exist_ok=True)

    file_id = str(uuid.uuid4())
    safe_name = file.filename.replace("/", "_").replace("\\", "_")
    stored_name = f"{file_id}_{safe_name}"
    dest = user_dir / stored_name

    # Read in chunks and enforce size limit
    total_size = 0
    with dest.open("wb") as f:
        while True:
            chunk = await file.read(8192)
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > MAX_FILE_SIZE:
                dest.unlink(missing_ok=True)
                max_mb = MAX_FILE_SIZE // (1024 * 1024)
                raise AppError(
                    "file_too_large",
                    status_code=413,
                    detail=f"File exceeds maximum size of {max_mb} MB",
                    detail_args={"max_mb": max_mb},
                )
            f.write(chunk)

    # Extract text content for preview
    extracted = _extract_content(dest)
    content_preview: str | None = None
    content_length: int | None = None
    if extracted:
        content_preview = extracted[:500]
        content_length = len(extracted)
        content_path = dest.parent / f"{stored_name}.content"
        content_path.write_text(extracted, encoding="utf-8")

    # Update index (locked to prevent concurrent read-modify-write races)
    file_url = f"/uploads/user_{current_user.id}/{stored_name}"
    mime_type = _guess_mime(ext)
    async with _file_lock(current_user.id):
        index = _load_index(current_user.id)
        index[file_id] = {
            "filename": file.filename,
            "stored_name": stored_name,
            "file_url": file_url,
            "size": total_size,
            "content_preview": content_preview,
            "content_length": content_length,
            "mime_type": mime_type,
        }
        _save_index(current_user.id, index)

    return ApiResponse(
        data={
            "file_id": file_id,
            "filename": file.filename,
            "file_url": file_url,
            "size": total_size,
            "content_preview": content_preview,
            "content_length": content_length,
            "mime_type": mime_type,
        }
    )


@router.get("", response_model=ApiResponse)
async def list_files(
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> ApiResponse:
    index = _load_index(current_user.id)
    files = [
        {
            "file_id": fid,
            "filename": meta["filename"],
            "file_url": meta["file_url"],
            "size": meta["size"],
            "content_preview": meta.get("content_preview"),
            "content_length": meta.get("content_length"),
            "mime_type": meta.get("mime_type", "application/octet-stream"),
        }
        for fid, meta in index.items()
    ]
    return ApiResponse(data=files)


@router.get("/{file_id}/content", response_model=ApiResponse)
async def get_file_content(
    file_id: str,
    offset: int = 0,
    limit: int | None = None,
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> ApiResponse:
    index = _load_index(current_user.id)
    meta = index.get(file_id)
    if meta is None:
        raise AppError("file_not_found", status_code=404)

    content_sidecar = _user_dir(current_user.id) / f"{meta['stored_name']}.content"
    if not content_sidecar.resolve().is_relative_to(_user_dir(current_user.id).resolve()):
        raise AppError("file_not_found", status_code=404)
    if not content_sidecar.exists():
        raise AppError("content_not_available", status_code=404, detail="No extracted content available for this file")

    full_text = content_sidecar.read_text(encoding="utf-8")
    total_length = len(full_text)
    sliced = full_text[offset:] if limit is None else full_text[offset:offset + limit]

    return ApiResponse(
        data={
            "content": sliced,
            "total_length": total_length,
            "offset": offset,
            "returned_length": len(sliced),
        }
    )


@router.get("/{file_id}")
async def download_file(
    file_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> FileResponse:
    index = _load_index(current_user.id)
    meta = index.get(file_id)
    if meta is None:
        raise AppError("file_not_found", status_code=404)

    file_path = _user_dir(current_user.id) / str(meta["stored_name"])
    if not file_path.resolve().is_relative_to(_user_dir(current_user.id).resolve()):
        raise AppError("file_not_found", status_code=404)
    if not file_path.exists():
        raise AppError("file_not_found_disk", status_code=404)

    return FileResponse(
        path=str(file_path),
        filename=str(meta["filename"]),
        media_type="application/octet-stream",
    )


@router.delete("/{file_id}", response_model=ApiResponse)
async def delete_file(
    file_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> ApiResponse:
    async with _file_lock(current_user.id):
        index = _load_index(current_user.id)
        meta = index.get(file_id)
        if meta is None:
            raise AppError("file_not_found", status_code=404)

        # Remove from disk
        file_path = _user_dir(current_user.id) / str(meta["stored_name"])
        if not file_path.resolve().is_relative_to(_user_dir(current_user.id).resolve()):
            raise AppError("file_not_found", status_code=404)
        file_path.unlink(missing_ok=True)
        content_sidecar = file_path.parent / f"{meta['stored_name']}.content"
        content_sidecar.unlink(missing_ok=True)

        # Remove from index
        del index[file_id]
        _save_index(current_user.id, index)

    return ApiResponse(data={"deleted": file_id})

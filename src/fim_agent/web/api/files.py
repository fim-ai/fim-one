"""File upload, download, and listing endpoints."""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from fim_agent.web.auth import get_current_user
from fim_agent.web.models import User
from fim_agent.web.schemas.common import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/files", tags=["files"])

UPLOAD_ROOT = Path(os.environ.get("UPLOADS_DIR", "uploads"))
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
ALLOWED_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".json", ".csv",
    ".pdf", ".docx", ".html", ".htm",
    ".xlsx", ".xls", ".pptx",
    # Images (vision model support)
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg",
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}

_MIME_MAP: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
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
    """
    suffix = file_path.suffix.lower()

    # Images have no extractable text content
    if _is_image(suffix):
        return None

    # Plain text family
    if suffix in {".txt", ".md", ".py", ".js", ".html", ".htm"}:
        return file_path.read_text(encoding="utf-8", errors="replace")

    # JSON — parse and pretty-print
    if suffix == ".json":
        try:
            data = json.loads(file_path.read_text(encoding="utf-8", errors="replace"))
            return json.dumps(data, indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            return file_path.read_text(encoding="utf-8", errors="replace")

    # CSV — raw text
    if suffix == ".csv":
        return file_path.read_text(encoding="utf-8", errors="replace")

    # PDF — requires pdfplumber (optional)
    if suffix == ".pdf":
        try:
            import pdfplumber  # type: ignore[import-untyped]
        except ImportError:
            return "[PDF content extraction requires pdfplumber]"
        pages_text: list[str] = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages_text.append(text)
        return "\n".join(pages_text) if pages_text else None

    # Office documents (DOCX, XLSX, XLS, PPTX) — requires markitdown
    if suffix in {".docx", ".xlsx", ".xls", ".pptx"}:
        try:
            from markitdown import MarkItDown  # type: ignore[import-untyped]
        except ImportError:
            return f"[{suffix.upper().lstrip('.')} content extraction requires markitdown]"
        converter = MarkItDown()
        result = converter.convert(str(file_path))
        content = result.text_content or ""
        return content if content.strip() else None

    return None


def _user_dir(user_id: str) -> Path:
    return UPLOAD_ROOT / f"user_{user_id}"


def _index_path(user_id: str) -> Path:
    return _user_dir(user_id) / "index.json"


def _load_index(user_id: str) -> dict[str, dict]:
    path = _index_path(user_id)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _save_index(user_id: str, index: dict[str, dict]) -> None:
    path = _index_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, ensure_ascii=False, indent=2))


@router.post("/upload", response_model=ApiResponse)
async def upload_file(
    file: UploadFile,
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> ApiResponse:
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No filename provided",
        )

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"File extension '{ext}' is not allowed. "
                f"Accepted: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            ),
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
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"File exceeds maximum size of {MAX_FILE_SIZE // (1024 * 1024)} MB",
                )
            f.write(chunk)

    # Extract text content for preview
    extracted = _extract_content(dest)
    content_preview: str | None = None
    if extracted:
        content_preview = extracted[:500]

    # Update index
    index = _load_index(current_user.id)
    file_url = f"/uploads/user_{current_user.id}/{stored_name}"
    mime_type = _guess_mime(ext)
    index[file_id] = {
        "filename": file.filename,
        "stored_name": stored_name,
        "file_url": file_url,
        "size": total_size,
        "content_preview": content_preview,
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
            "mime_type": meta.get("mime_type", "application/octet-stream"),
        }
        for fid, meta in index.items()
    ]
    return ApiResponse(data=files)


@router.get("/{file_id}")
async def download_file(
    file_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> FileResponse:
    index = _load_index(current_user.id)
    meta = index.get(file_id)
    if meta is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )

    file_path = _user_dir(current_user.id) / meta["stored_name"]
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found on disk",
        )

    return FileResponse(
        path=str(file_path),
        filename=meta["filename"],
        media_type="application/octet-stream",
    )


@router.delete("/{file_id}", response_model=ApiResponse)
async def delete_file(
    file_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> ApiResponse:
    index = _load_index(current_user.id)
    meta = index.get(file_id)
    if meta is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )

    # Remove from disk
    file_path = _user_dir(current_user.id) / meta["stored_name"]
    file_path.unlink(missing_ok=True)

    # Remove from index
    del index[file_id]
    _save_index(current_user.id, index)

    return ApiResponse(data={"deleted": file_id})

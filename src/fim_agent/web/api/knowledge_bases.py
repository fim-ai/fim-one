"""Knowledge Base CRUD endpoints with document upload and retrieval."""

from __future__ import annotations

import asyncio
import logging
import math
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fim_agent.db import get_session
from fim_agent.web.auth import get_current_user
from fim_agent.web.exceptions import AppError
from fim_agent.web.deps import get_embedding, get_kb_manager
from fim_agent.web.models import KBDocument, KnowledgeBase, User
from fim_agent.web.schemas.common import ApiResponse, PaginatedResponse
from fim_agent.web.schemas.knowledge_base import (
    ChunkResponse,
    ChunkUpdate,
    DocumentCreate,
    ImportUrlsRequest,
    ImportUrlsResponse,
    KBCreate,
    KBDocumentResponse,
    KBResponse,
    KBRetrieveRequest,
    KBRetrieveResponse,
    KBUpdate,
    UrlImportResult,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge-bases", tags=["knowledge-bases"])

_UPLOADS_DIR = Path("uploads") / "kb"
_SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".md", ".markdown", ".html", ".htm", ".csv", ".txt",
    ".xlsx", ".xls", ".pptx",
}


# ── Helpers ──────────────────────────────────────────────────────


def _kb_to_response(kb: KnowledgeBase) -> KBResponse:
    return KBResponse(
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
        created_at=kb.created_at.isoformat() if kb.created_at else "",
        updated_at=kb.updated_at.isoformat() if kb.updated_at else None,
    )


def _doc_to_response(doc: KBDocument) -> KBDocumentResponse:
    return KBDocumentResponse(
        id=doc.id,
        kb_id=doc.kb_id,
        filename=doc.filename,
        file_size=doc.file_size,
        file_type=doc.file_type,
        chunk_count=doc.chunk_count,
        status=doc.status,
        error_message=doc.error_message,
        created_at=doc.created_at.isoformat() if doc.created_at else "",
    )


async def _get_owned_kb(
    kb_id: str, user_id: str, db: AsyncSession
) -> KnowledgeBase:
    result = await db.execute(
        select(KnowledgeBase).where(
            KnowledgeBase.id == kb_id, KnowledgeBase.user_id == user_id
        )
    )
    kb = result.scalar_one_or_none()
    if kb is None:
        raise AppError("kb_not_found", status_code=404)
    return kb


# ── KB CRUD ──────────────────────────────────────────────────────


@router.post("", response_model=ApiResponse)
async def create_kb(
    body: KBCreate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    kb = KnowledgeBase(
        user_id=current_user.id,
        name=body.name,
        description=body.description,
        chunk_strategy=body.chunk_strategy,
        chunk_size=body.chunk_size,
        chunk_overlap=body.chunk_overlap,
        retrieval_mode=body.retrieval_mode,
        status="active",
    )
    db.add(kb)
    await db.commit()
    result = await db.execute(
        select(KnowledgeBase).where(KnowledgeBase.id == kb.id)
    )
    kb = result.scalar_one()
    return ApiResponse(data=_kb_to_response(kb).model_dump())


@router.get("", response_model=PaginatedResponse)
async def list_kbs(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    base = select(KnowledgeBase).where(KnowledgeBase.user_id == current_user.id)

    count_result = await db.execute(
        select(func.count()).select_from(base.subquery())
    )
    total = count_result.scalar_one()

    result = await db.execute(
        base.order_by(KnowledgeBase.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    kbs = result.scalars().all()

    return PaginatedResponse(
        items=[_kb_to_response(k).model_dump() for k in kbs],
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total else 0,
    )


@router.get("/{kb_id}", response_model=ApiResponse)
async def get_kb(
    kb_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    kb = await _get_owned_kb(kb_id, current_user.id, db)
    return ApiResponse(data=_kb_to_response(kb).model_dump())


@router.put("/{kb_id}", response_model=ApiResponse)
async def update_kb(
    kb_id: str,
    body: KBUpdate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    kb = await _get_owned_kb(kb_id, current_user.id, db)
    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(kb, field, value)
    await db.commit()
    result = await db.execute(
        select(KnowledgeBase).where(KnowledgeBase.id == kb.id)
    )
    kb = result.scalar_one()
    return ApiResponse(data=_kb_to_response(kb).model_dump())


@router.delete("/{kb_id}", response_model=ApiResponse)
async def delete_kb(
    kb_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    # Load with documents for ORM cascade delete
    result = await db.execute(
        select(KnowledgeBase)
        .options(selectinload(KnowledgeBase.documents))
        .where(KnowledgeBase.id == kb_id, KnowledgeBase.user_id == current_user.id)
    )
    kb = result.scalar_one_or_none()
    if kb is None:
        raise AppError("kb_not_found", status_code=404)
    # Delete vectors
    try:
        manager = get_kb_manager()
        await manager.delete_kb(kb_id=kb_id, user_id=current_user.id)
    except Exception:
        logger.warning("Failed to delete vector data for KB %s", kb_id, exc_info=True)
    # Delete uploaded files from disk
    try:
        upload_dir = _UPLOADS_DIR / kb_id
        if upload_dir.exists():
            shutil.rmtree(upload_dir)
    except Exception:
        logger.warning("Failed to delete upload directory for KB %s", kb_id, exc_info=True)
    # Delete DB records (cascade deletes documents)
    await db.delete(kb)
    await db.commit()
    return ApiResponse(data={"deleted": kb_id})


# ── Documents ────────────────────────────────────────────────────


@router.get("/{kb_id}/documents", response_model=PaginatedResponse)
async def list_documents(
    kb_id: str,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    await _get_owned_kb(kb_id, current_user.id, db)  # ownership check

    base = select(KBDocument).where(KBDocument.kb_id == kb_id)

    count_result = await db.execute(
        select(func.count()).select_from(base.subquery())
    )
    total = count_result.scalar_one()

    result = await db.execute(
        base.order_by(KBDocument.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    docs = result.scalars().all()

    return PaginatedResponse(
        items=[_doc_to_response(d).model_dump() for d in docs],
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total else 0,
    )


@router.post("/{kb_id}/documents", response_model=ApiResponse)
async def upload_document(
    kb_id: str,
    file: UploadFile = File(...),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    kb = await _get_owned_kb(kb_id, current_user.id, db)

    # Validate extension
    filename = file.filename or "unknown"
    ext = Path(filename).suffix.lower()
    if ext not in _SUPPORTED_EXTENSIONS:
        raise AppError(
            "unsupported_document_type",
            status_code=422,
            detail=f"Unsupported document type: {ext}",
            detail_args={"ext": ext},
        )

    # Save file
    upload_dir = _UPLOADS_DIR / kb_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / filename
    content = await file.read()
    file_path.write_bytes(content)

    # Create document record
    doc = KBDocument(
        kb_id=kb_id,
        filename=filename,
        file_path=str(file_path),
        file_size=len(content),
        file_type=ext.lstrip("."),
        status="processing",
    )
    db.add(doc)
    await db.commit()
    result = await db.execute(
        select(KBDocument).where(KBDocument.id == doc.id)
    )
    doc = result.scalar_one()

    # Background ingest
    asyncio.create_task(
        _ingest_document(
            doc_id=doc.id,
            kb_id=kb_id,
            user_id=current_user.id,
            file_path=file_path,
            chunk_strategy=kb.chunk_strategy,
            chunk_size=kb.chunk_size,
            chunk_overlap=kb.chunk_overlap,
        )
    )

    return ApiResponse(data=_doc_to_response(doc).model_dump())


async def _ingest_document(
    *,
    doc_id: str,
    kb_id: str,
    user_id: str,
    file_path: Path,
    chunk_strategy: str,
    chunk_size: int,
    chunk_overlap: int,
) -> None:
    """Background task: load → chunk → embed → store, then update DB."""
    from fim_agent.db import create_session

    try:
        manager = get_kb_manager()
        chunk_count, content_hash = await manager.ingest_file(
            file_path,
            kb_id=kb_id,
            user_id=user_id,
            document_id=doc_id,
            chunk_strategy=chunk_strategy,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

        db = create_session()
        try:
            result = await db.execute(
                select(KBDocument).where(KBDocument.id == doc_id)
            )
            doc = result.scalar_one_or_none()
            if doc:
                doc.chunk_count = chunk_count
                doc.content_hash = content_hash
                doc.status = "ready"
                await db.commit()

            # Update KB counters
            result = await db.execute(
                select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
            )
            kb = result.scalar_one_or_none()
            if kb:
                count_result = await db.execute(
                    select(func.count()).where(KBDocument.kb_id == kb_id)
                )
                kb.document_count = count_result.scalar_one()
                sum_result = await db.execute(
                    select(func.coalesce(func.sum(KBDocument.chunk_count), 0)).where(
                        KBDocument.kb_id == kb_id
                    )
                )
                kb.total_chunks = sum_result.scalar_one()
                await db.commit()
        finally:
            await db.close()

        logger.info("Document %s ingested: %d chunks", doc_id, chunk_count)

    except Exception as exc:
        logger.error("Ingest failed for document %s: %s", doc_id, exc, exc_info=True)
        try:
            db = create_session()
            try:
                result = await db.execute(
                    select(KBDocument).where(KBDocument.id == doc_id)
                )
                doc = result.scalar_one_or_none()
                if doc:
                    doc.status = "failed"
                    doc.error_message = str(exc)[:500]
                    await db.commit()
            finally:
                await db.close()
        except Exception:
            logger.error("Failed to update document status", exc_info=True)


@router.delete("/{kb_id}/documents/{doc_id}", response_model=ApiResponse)
async def delete_document(
    kb_id: str,
    doc_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    kb = await _get_owned_kb(kb_id, current_user.id, db)

    result = await db.execute(
        select(KBDocument).where(KBDocument.id == doc_id, KBDocument.kb_id == kb_id)
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise AppError("document_not_found", status_code=404)

    # Delete vectors
    try:
        manager = get_kb_manager()
        await manager.delete_document(
            kb_id=kb_id, user_id=current_user.id, document_id=doc_id
        )
    except Exception:
        logger.warning("Failed to delete vectors for doc %s", doc_id, exc_info=True)

    # Delete uploaded file from disk
    try:
        file_path = Path(doc.file_path)
        file_path.unlink(missing_ok=True)
        # Remove parent directory if now empty
        parent = file_path.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except Exception:
        logger.warning("Failed to delete file for doc %s", doc_id, exc_info=True)

    # Delete DB record and update counters
    await db.delete(doc)
    kb.document_count = max(0, kb.document_count - 1)
    kb.total_chunks = max(0, kb.total_chunks - (doc.chunk_count or 0))
    await db.commit()

    return ApiResponse(data={"deleted": doc_id})


@router.post("/{kb_id}/documents/{doc_id}/retry", response_model=ApiResponse)
async def retry_document(
    kb_id: str,
    doc_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Retry ingestion for a failed document."""
    kb = await _get_owned_kb(kb_id, current_user.id, db)

    result = await db.execute(
        select(KBDocument).where(KBDocument.id == doc_id, KBDocument.kb_id == kb_id)
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise AppError("document_not_found", status_code=404)

    if doc.status != "failed":
        raise AppError(
            "document_not_failed",
            detail=f"Only failed documents can be retried (current status: {doc.status})",
            detail_args={"status": doc.status},
        )

    # Verify the source file still exists on disk
    file_path = Path(doc.file_path)
    if not file_path.exists():
        raise AppError("source_file_missing")

    # Clean up any partial chunks left by the previous attempt
    try:
        manager = get_kb_manager()
        await manager.delete_document(
            kb_id=kb_id, user_id=current_user.id, document_id=doc_id
        )
    except Exception:
        logger.warning("Failed to clean partial chunks for doc %s", doc_id, exc_info=True)

    # Reset document status for re-processing
    doc.status = "processing"
    doc.error_message = None
    doc.chunk_count = 0
    await db.commit()

    # Launch background ingestion (same as upload flow)
    asyncio.create_task(
        _ingest_document(
            doc_id=doc.id,
            kb_id=kb_id,
            user_id=current_user.id,
            file_path=file_path,
            chunk_strategy=kb.chunk_strategy,
            chunk_size=kb.chunk_size,
            chunk_overlap=kb.chunk_overlap,
        )
    )

    return ApiResponse(data={"status": "processing"})


# ── Document Create (Markdown) ───────────────────────────────────


@router.post("/{kb_id}/documents/create", response_model=ApiResponse)
async def create_document(
    kb_id: str,
    body: DocumentCreate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Create a new markdown document by writing content to disk and ingesting."""
    kb = await _get_owned_kb(kb_id, current_user.id, db)

    # Check for filename collision
    upload_dir = _UPLOADS_DIR / kb_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / body.filename
    if file_path.exists():
        raise AppError(
            "document_filename_conflict",
            status_code=409,
            detail=f"File '{body.filename}' already exists in this knowledge base",
            detail_args={"filename": body.filename},
        )

    # Write file to disk
    content_bytes = body.content.encode("utf-8")
    file_path.write_bytes(content_bytes)

    # Create document record
    doc = KBDocument(
        kb_id=kb_id,
        filename=body.filename,
        file_path=str(file_path),
        file_size=len(content_bytes),
        file_type="md",
        status="processing",
    )
    db.add(doc)
    await db.commit()
    result = await db.execute(
        select(KBDocument).where(KBDocument.id == doc.id)
    )
    doc = result.scalar_one()

    # Background ingest (reuse existing pipeline)
    asyncio.create_task(
        _ingest_document(
            doc_id=doc.id,
            kb_id=kb_id,
            user_id=current_user.id,
            file_path=file_path,
            chunk_strategy=kb.chunk_strategy,
            chunk_size=kb.chunk_size,
            chunk_overlap=kb.chunk_overlap,
        )
    )

    return ApiResponse(data=_doc_to_response(doc).model_dump())


# ── URL Import ───────────────────────────────────────────────────


@router.post("/{kb_id}/import-urls", response_model=ApiResponse)
async def import_urls(
    kb_id: str,
    body: ImportUrlsRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Fetch URLs via Jina Reader or direct download, then ingest each as a document."""
    from fim_agent.rag.url_importer import resolve_url, get_jina_api_key

    kb = await _get_owned_kb(kb_id, current_user.id, db)

    try:
        jina_api_key = get_jina_api_key()
    except ValueError as exc:
        raise AppError(
            "jina_api_key_missing",
            status_code=503,
            detail=str(exc),
        ) from exc

    # Deduplicate while preserving order
    seen: set[str] = set()
    urls = [u for u in body.urls if u and not (u in seen or seen.add(u))]  # type: ignore[func-returns-value]

    results: list[UrlImportResult] = []
    upload_dir = _UPLOADS_DIR / kb_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    for url in urls:
        # Resolve URL — direct file download or Jina markdown fetch
        try:
            fetched = await resolve_url(url, jina_api_key)
        except Exception as exc:
            logger.warning("URL fetch failed for %s: %s", url, exc)
            results.append(UrlImportResult(url=url, status="failed", error=str(exc)[:300]))
            continue

        if fetched["mode"] == "file":
            ext: str = fetched["ext"]
            base_name: str = fetched["filename"]
            # Collision-avoidance loop for file downloads
            counter = 0
            while True:
                candidate = upload_dir / (
                    f"{Path(base_name).stem}_{counter}{ext}" if counter else base_name
                )
                if not candidate.exists():
                    break
                counter += 1
            file_path = candidate
            file_bytes: bytes = fetched["file_bytes"]
            file_path.write_bytes(file_bytes)
            file_type = ext.lstrip(".")

            # Create DB record
            doc = KBDocument(
                kb_id=kb_id,
                filename=file_path.name,
                file_path=str(file_path),
                file_size=len(file_bytes),
                file_type=file_type,
                status="processing",
            )
            db.add(doc)
            await db.commit()
            result_row = await db.execute(select(KBDocument).where(KBDocument.id == doc.id))
            doc = result_row.scalar_one()

        else:
            # mode == "markdown"
            content: str = fetched["content"]
            if not content.strip():
                results.append(UrlImportResult(url=url, status="failed", error="Empty content returned"))
                continue

            # Build a safe filename from the title
            title: str = fetched["title"]
            safe_title = "".join(c if c.isalnum() or c in " -_." else "_" for c in title)[:80].strip() or "document"
            filename = f"{safe_title}.md"
            # Avoid collisions
            file_path = upload_dir / filename
            stem, counter = safe_title, 1
            while file_path.exists():
                counter += 1
                filename = f"{stem}_{counter}.md"
                file_path = upload_dir / filename

            # Prepend source URL as metadata comment
            md_content = f"<!-- source: {url} -->\n\n# {title}\n\n{content}"
            content_bytes = md_content.encode("utf-8")
            file_path.write_bytes(content_bytes)

            # Create DB record
            doc = KBDocument(
                kb_id=kb_id,
                filename=filename,
                file_path=str(file_path),
                file_size=len(content_bytes),
                file_type="md",
                status="processing",
            )
            db.add(doc)
            await db.commit()
            result_row = await db.execute(select(KBDocument).where(KBDocument.id == doc.id))
            doc = result_row.scalar_one()

        # Background ingest (identical to file upload — handles PDF/DOCX/md via loader_for_extension)
        asyncio.create_task(
            _ingest_document(
                doc_id=doc.id,
                kb_id=kb_id,
                user_id=current_user.id,
                file_path=file_path,
                chunk_strategy=kb.chunk_strategy,
                chunk_size=kb.chunk_size,
                chunk_overlap=kb.chunk_overlap,
            )
        )

        results.append(UrlImportResult(url=url, status="success", doc_id=doc.id))
        logger.info("URL %s queued for ingest as doc %s", url, doc.id)

    response = ImportUrlsResponse(results=results)
    return ApiResponse(data=response.model_dump())


# ── Chunk CRUD ────────────────────────────────────────────────────


async def _get_owned_doc(
    kb_id: str, doc_id: str, user_id: str, db: AsyncSession
) -> tuple[KnowledgeBase, KBDocument]:
    """Fetch KB and document with ownership and status checks."""
    kb = await _get_owned_kb(kb_id, user_id, db)
    result = await db.execute(
        select(KBDocument).where(
            KBDocument.id == doc_id, KBDocument.kb_id == kb_id
        )
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise AppError("document_not_found", status_code=404)
    return kb, doc


@router.get("/{kb_id}/documents/{doc_id}/chunks", response_model=PaginatedResponse)
async def list_chunks(
    kb_id: str,
    doc_id: str,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    query: str = Query("", description="Filter chunks by text content"),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    """List paginated chunks for a document."""
    _kb, doc = await _get_owned_doc(kb_id, doc_id, current_user.id, db)

    if doc.status != "ready":
        raise AppError(
            "document_not_ready",
            detail=f"Document is not ready (status: {doc.status})",
            detail_args={"status": doc.status},
        )

    manager = get_kb_manager()
    chunks, total = await manager.get_chunks_by_document(
        kb_id=kb_id, user_id=current_user.id, document_id=doc_id,
        page=page, size=size, query=query,
    )

    items = [
        ChunkResponse(
            id=c["id"],
            text=c["text"],
            chunk_index=c["chunk_index"],
            metadata=c.get("metadata"),
            content_hash=c["content_hash"],
        ).model_dump()
        for c in chunks
    ]

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total else 0,
    )


@router.get("/{kb_id}/chunks/{chunk_id}", response_model=ApiResponse)
async def get_chunk(
    kb_id: str,
    chunk_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Get a single chunk by ID."""
    await _get_owned_kb(kb_id, current_user.id, db)

    manager = get_kb_manager()
    chunk = await manager.get_chunk(
        kb_id=kb_id, user_id=current_user.id, chunk_id=chunk_id,
    )
    if chunk is None:
        raise AppError("chunk_not_found", status_code=404)

    resp = ChunkResponse(
        id=chunk["id"],
        text=chunk["text"],
        chunk_index=chunk["chunk_index"],
        metadata=chunk.get("metadata"),
        content_hash=chunk["content_hash"],
    )
    return ApiResponse(data=resp.model_dump())


@router.put("/{kb_id}/chunks/{chunk_id}", response_model=ApiResponse)
async def update_chunk(
    kb_id: str,
    chunk_id: str,
    body: ChunkUpdate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Update a chunk's text (re-embeds synchronously)."""
    await _get_owned_kb(kb_id, current_user.id, db)

    # Verify chunk exists and check document status
    manager = get_kb_manager()
    existing = await manager.get_chunk(
        kb_id=kb_id, user_id=current_user.id, chunk_id=chunk_id,
    )
    if existing is None:
        raise AppError("chunk_not_found", status_code=404)

    # Ensure the parent document is in "ready" status
    doc_id = existing.get("document_id", "")
    if doc_id:
        result = await db.execute(
            select(KBDocument).where(KBDocument.id == doc_id)
        )
        doc = result.scalar_one_or_none()
        if doc and doc.status != "ready":
            raise AppError(
                "document_not_ready",
                detail=f"Document is not ready (status: {doc.status})",
                detail_args={"status": doc.status},
            )

    updated = await manager.update_chunk_text(
        kb_id=kb_id, user_id=current_user.id,
        chunk_id=chunk_id, new_text=body.text,
    )
    if not updated:
        raise AppError("chunk_update_failed", status_code=500)

    # Return updated chunk
    chunk = await manager.get_chunk(
        kb_id=kb_id, user_id=current_user.id, chunk_id=chunk_id,
    )
    resp = ChunkResponse(
        id=chunk["id"],
        text=chunk["text"],
        chunk_index=chunk["chunk_index"],
        metadata=chunk.get("metadata"),
        content_hash=chunk["content_hash"],
    )
    return ApiResponse(data=resp.model_dump())


@router.delete("/{kb_id}/chunks/{chunk_id}", response_model=ApiResponse)
async def delete_chunk(
    kb_id: str,
    chunk_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Delete a single chunk and decrement counters."""
    kb = await _get_owned_kb(kb_id, current_user.id, db)

    # Verify chunk exists and get its document_id
    manager = get_kb_manager()
    existing = await manager.get_chunk(
        kb_id=kb_id, user_id=current_user.id, chunk_id=chunk_id,
    )
    if existing is None:
        raise AppError("chunk_not_found", status_code=404)

    # Ensure the parent document is in "ready" status
    doc_id = existing.get("document_id", "")
    if doc_id:
        result = await db.execute(
            select(KBDocument).where(KBDocument.id == doc_id)
        )
        doc = result.scalar_one_or_none()
        if doc and doc.status != "ready":
            raise AppError(
                "document_not_ready",
                detail=f"Document is not ready (status: {doc.status})",
                detail_args={"status": doc.status},
            )

    deleted = await manager.delete_chunk(
        kb_id=kb_id, user_id=current_user.id, chunk_id=chunk_id,
    )
    if not deleted:
        raise AppError("chunk_delete_failed", status_code=500)

    # Decrement counters on KBDocument and KnowledgeBase
    if doc_id:
        result = await db.execute(
            select(KBDocument).where(KBDocument.id == doc_id)
        )
        doc = result.scalar_one_or_none()
        if doc:
            doc.chunk_count = max(0, doc.chunk_count - 1)

    kb.total_chunks = max(0, kb.total_chunks - 1)
    await db.commit()

    return ApiResponse(data={"deleted": chunk_id})


# ── Retrieval ────────────────────────────────────────────────────


@router.post("/{kb_id}/retrieve", response_model=ApiResponse)
async def retrieve_from_kb(
    kb_id: str,
    body: KBRetrieveRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    kb = await _get_owned_kb(kb_id, current_user.id, db)

    manager = get_kb_manager()
    documents = await manager.retrieve(
        body.query,
        kb_id=kb_id,
        user_id=current_user.id,
        top_k=body.top_k,
        mode=kb.retrieval_mode,
    )

    results = [
        KBRetrieveResponse(
            content=doc.content,
            metadata=doc.metadata,
            score=doc.score or 0.0,
        ).model_dump()
        for doc in documents
    ]

    return ApiResponse(data=results)

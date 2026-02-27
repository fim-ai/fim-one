"""Knowledge Base CRUD endpoints with document upload and retrieval."""

from __future__ import annotations

import asyncio
import logging
import math
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_agent.db import get_session
from fim_agent.web.auth import get_current_user
from fim_agent.web.deps import get_embedding, get_kb_manager
from fim_agent.web.models import KBDocument, KnowledgeBase, User
from fim_agent.web.schemas.common import ApiResponse, PaginatedResponse
from fim_agent.web.schemas.knowledge_base import (
    KBCreate,
    KBDocumentResponse,
    KBResponse,
    KBRetrieveRequest,
    KBRetrieveResponse,
    KBUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge-bases", tags=["knowledge-bases"])

_UPLOADS_DIR = Path("uploads") / "kb"
_SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".md", ".markdown", ".html", ".htm", ".csv", ".txt"}


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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )
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
    await db.refresh(kb)
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
    await db.refresh(kb)
    return ApiResponse(data=_kb_to_response(kb).model_dump())


@router.delete("/{kb_id}", response_model=ApiResponse)
async def delete_kb(
    kb_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    kb = await _get_owned_kb(kb_id, current_user.id, db)
    # Delete vectors
    try:
        manager = get_kb_manager()
        await manager.delete_kb(kb_id=kb_id, user_id=current_user.id)
    except Exception:
        logger.warning("Failed to delete vector data for KB %s", kb_id, exc_info=True)
    # Delete DB records (cascade deletes documents)
    await db.delete(kb)
    await db.commit()
    return ApiResponse(data={"deleted": kb_id})


# ── Documents ────────────────────────────────────────────────────


@router.get("/{kb_id}/documents", response_model=ApiResponse)
async def list_documents(
    kb_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    await _get_owned_kb(kb_id, current_user.id, db)  # ownership check
    result = await db.execute(
        select(KBDocument)
        .where(KBDocument.kb_id == kb_id)
        .order_by(KBDocument.created_at.desc())
    )
    docs = result.scalars().all()
    return ApiResponse(data=[_doc_to_response(d).model_dump() for d in docs])


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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type: {ext}",
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
    await db.refresh(doc)

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
    from fim_agent.db import get_session as session_factory

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

        async with session_factory() as db:
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

        logger.info("Document %s ingested: %d chunks", doc_id, chunk_count)

    except Exception as exc:
        logger.error("Ingest failed for document %s: %s", doc_id, exc, exc_info=True)
        try:
            async with session_factory() as db:
                result = await db.execute(
                    select(KBDocument).where(KBDocument.id == doc_id)
                )
                doc = result.scalar_one_or_none()
                if doc:
                    doc.status = "failed"
                    doc.error_message = str(exc)[:500]
                    await db.commit()
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    # Delete vectors
    try:
        manager = get_kb_manager()
        await manager.delete_document(
            kb_id=kb_id, user_id=current_user.id, document_id=doc_id
        )
    except Exception:
        logger.warning("Failed to delete vectors for doc %s", doc_id, exc_info=True)

    # Delete DB record and update counters
    await db.delete(doc)
    kb.document_count = max(0, kb.document_count - 1)
    kb.total_chunks = max(0, kb.total_chunks - (doc.chunk_count or 0))
    await db.commit()

    return ApiResponse(data={"deleted": doc_id})


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

"""Admin API endpoints for content moderation (sensitive words)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.auth import get_current_admin
from fim_one.web.exceptions import AppError
from fim_one.web.models import SensitiveWord, User

from fim_one.web.api.admin_utils import write_audit

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Schemas — Sensitive Words
# ---------------------------------------------------------------------------


class SensitiveWordCreate(BaseModel):
    word: str = Field(..., max_length=100)
    category: str = Field("general", max_length=50)


class SensitiveWordBatchImport(BaseModel):
    words: list[str]
    category: str = Field("general", max_length=50)


class SensitiveWordInfo(BaseModel):
    id: str
    word: str
    category: str
    is_active: bool
    created_by_id: str | None
    created_at: str
    updated_at: str


class SensitiveWordToggle(BaseModel):
    is_active: bool


class SensitiveWordMatch(BaseModel):
    word: str
    category: str


class SensitiveWordCheckRequest(BaseModel):
    text: str


class SensitiveWordCheckResponse(BaseModel):
    matched: list[SensitiveWordMatch]
    clean: bool


class BatchImportResponse(BaseModel):
    added: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _word_to_info(w: SensitiveWord) -> SensitiveWordInfo:
    return SensitiveWordInfo(
        id=w.id,
        word=w.word,
        category=w.category,
        is_active=w.is_active,
        created_by_id=w.created_by_id,
        created_at=w.created_at.isoformat() if w.created_at else "",
        updated_at=w.updated_at.isoformat() if w.updated_at else "",
    )


# ---------------------------------------------------------------------------
# Sensitive Word Endpoints
# ---------------------------------------------------------------------------


@router.get("/sensitive-words")
async def list_sensitive_words(
    category: str | None = Query(None),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[SensitiveWordInfo]:
    """List all sensitive words, ordered by category then word."""
    stmt = select(SensitiveWord)
    if category is not None:
        stmt = stmt.where(SensitiveWord.category == category)
    stmt = stmt.order_by(SensitiveWord.category.asc(), SensitiveWord.word.asc())
    result = await db.execute(stmt)
    words = result.scalars().all()
    return [_word_to_info(w) for w in words]


@router.post("/sensitive-words")
async def create_sensitive_word(
    body: SensitiveWordCreate,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> SensitiveWordInfo:
    """Add a new sensitive word. Rejects case-insensitive duplicates."""
    # Check for duplicate (case-insensitive)
    existing = await db.execute(
        select(SensitiveWord).where(func.lower(SensitiveWord.word) == body.word.lower())
    )
    if existing.scalar_one_or_none() is not None:
        raise AppError(
            "sensitive_word_duplicate",
            status_code=409,
            detail=f"Sensitive word already exists: {body.word}",
        )

    word = SensitiveWord(
        word=body.word,
        category=body.category,
        created_by_id=current_user.id,
    )
    db.add(word)
    await db.commit()
    await db.refresh(word)
    await write_audit(
        db, current_user, "sensitive_word.create",
        target_type="sensitive_word", target_id=word.id, target_label=word.word,
    )
    return _word_to_info(word)


@router.post("/sensitive-words/batch")
async def batch_import_sensitive_words(
    body: SensitiveWordBatchImport,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> BatchImportResponse:
    """Batch import sensitive words. Skips case-insensitive duplicates."""
    # Fetch all existing words (lowered) for fast duplicate check
    result = await db.execute(select(func.lower(SensitiveWord.word)))
    existing_lower = {row[0] for row in result.all()}

    added = 0
    seen: set[str] = set()
    for raw_word in body.words:
        w = raw_word.strip()
        if not w:
            continue
        lower_w = w.lower()
        if lower_w in existing_lower or lower_w in seen:
            continue
        seen.add(lower_w)
        db.add(SensitiveWord(
            word=w,
            category=body.category,
            created_by_id=current_user.id,
        ))
        added += 1

    if added > 0:
        await db.commit()
    await write_audit(
        db, current_user, "sensitive_word.batch_import",
        detail=f"added {added} words (category={body.category})",
    )
    return BatchImportResponse(added=added)


@router.delete("/sensitive-words/{word_id}", status_code=204)
async def delete_sensitive_word(
    word_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """Delete a sensitive word."""
    result = await db.execute(
        select(SensitiveWord).where(SensitiveWord.id == word_id)
    )
    word = result.scalar_one_or_none()
    if word is None:
        raise AppError("sensitive_word_not_found", status_code=404)

    label = word.word
    await db.delete(word)
    await db.commit()
    await write_audit(
        db, current_user, "sensitive_word.delete",
        target_type="sensitive_word", target_id=word_id, target_label=label,
    )


@router.put("/sensitive-words/{word_id}/toggle")
async def toggle_sensitive_word(
    word_id: str,
    body: SensitiveWordToggle,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> SensitiveWordInfo:
    """Toggle active status of a sensitive word."""
    result = await db.execute(
        select(SensitiveWord).where(SensitiveWord.id == word_id)
    )
    word = result.scalar_one_or_none()
    if word is None:
        raise AppError("sensitive_word_not_found", status_code=404)

    word.is_active = body.is_active
    await db.commit()
    await db.refresh(word)
    await write_audit(
        db, current_user, "sensitive_word.toggle",
        target_type="sensitive_word", target_id=word_id, target_label=word.word,
        detail=f"is_active={body.is_active}",
    )
    return _word_to_info(word)


@router.post("/sensitive-words/check")
async def check_sensitive_words(
    body: SensitiveWordCheckRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> SensitiveWordCheckResponse:
    """Check text against the active sensitive word list (case-insensitive)."""
    result = await db.execute(
        select(SensitiveWord).where(SensitiveWord.is_active == True)  # noqa: E712
    )
    active_words = result.scalars().all()

    text_lower = body.text.lower()
    matched: list[SensitiveWordMatch] = []
    for w in active_words:
        if w.word.lower() in text_lower:
            matched.append(SensitiveWordMatch(
                word=w.word,
                category=w.category,
            ))

    return SensitiveWordCheckResponse(
        matched=matched,
        clean=len(matched) == 0,
    )

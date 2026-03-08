"""Admin API endpoints for prompt template management and content moderation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_agent.db import get_session
from fim_agent.web.auth import get_current_admin
from fim_agent.web.exceptions import AppError
from fim_agent.web.models import PromptTemplate, SensitiveWord, User

from fim_agent.web.api.admin_utils import write_audit

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Schemas — Prompt Templates
# ---------------------------------------------------------------------------


class PromptTemplateCreate(BaseModel):
    name: str = Field(..., max_length=100)
    description: str | None = Field(None, max_length=500)
    content: str
    category: str = Field("general", max_length=50)


class PromptTemplateUpdate(BaseModel):
    name: str | None = Field(None, max_length=100)
    description: str | None = Field(None, max_length=500)
    content: str | None = None
    category: str | None = Field(None, max_length=50)
    is_active: bool | None = None


class PromptTemplateInfo(BaseModel):
    id: str
    name: str
    description: str | None
    content: str
    category: str
    is_active: bool
    created_by_id: str | None
    use_count: int
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Schemas — Sensitive Words
# ---------------------------------------------------------------------------


class SensitiveWordCreate(BaseModel):
    word: str = Field(..., max_length=100)
    category: str = Field("general", max_length=50)
    severity: str = Field("warn", max_length=20)


class SensitiveWordBatchImport(BaseModel):
    words: list[str]
    category: str = Field("general", max_length=50)
    severity: str = Field("warn", max_length=20)


class SensitiveWordInfo(BaseModel):
    id: str
    word: str
    category: str
    severity: str
    is_active: bool
    created_by_id: str | None
    created_at: str
    updated_at: str


class SensitiveWordMatch(BaseModel):
    word: str
    category: str
    severity: str


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


def _template_to_info(t: PromptTemplate) -> PromptTemplateInfo:
    return PromptTemplateInfo(
        id=t.id,
        name=t.name,
        description=t.description,
        content=t.content,
        category=t.category,
        is_active=t.is_active,
        created_by_id=t.created_by_id,
        use_count=t.use_count,
        created_at=t.created_at.isoformat() if t.created_at else "",
        updated_at=t.updated_at.isoformat() if t.updated_at else "",
    )


def _word_to_info(w: SensitiveWord) -> SensitiveWordInfo:
    return SensitiveWordInfo(
        id=w.id,
        word=w.word,
        category=w.category,
        severity=w.severity,
        is_active=w.is_active,
        created_by_id=w.created_by_id,
        created_at=w.created_at.isoformat() if w.created_at else "",
        updated_at=w.updated_at.isoformat() if w.updated_at else "",
    )


# ---------------------------------------------------------------------------
# Prompt Template Endpoints
# ---------------------------------------------------------------------------


@router.get("/prompt-templates")
async def list_prompt_templates(
    category: str | None = Query(None),
    is_active: bool | None = Query(None),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[PromptTemplateInfo]:
    """List all prompt templates, ordered by use_count desc then name asc."""
    stmt = select(PromptTemplate)
    if category is not None:
        stmt = stmt.where(PromptTemplate.category == category)
    if is_active is not None:
        stmt = stmt.where(PromptTemplate.is_active == is_active)
    stmt = stmt.order_by(PromptTemplate.use_count.desc(), PromptTemplate.name.asc())
    result = await db.execute(stmt)
    templates = result.scalars().all()
    return [_template_to_info(t) for t in templates]


@router.post("/prompt-templates")
async def create_prompt_template(
    body: PromptTemplateCreate,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PromptTemplateInfo:
    """Create a new prompt template."""
    template = PromptTemplate(
        name=body.name,
        description=body.description,
        content=body.content,
        category=body.category,
        created_by_id=current_user.id,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    await write_audit(
        db, current_user, "prompt_template.create",
        target_type="prompt_template", target_id=template.id, target_label=template.name,
    )
    return _template_to_info(template)


@router.put("/prompt-templates/{template_id}")
async def update_prompt_template(
    template_id: str,
    body: PromptTemplateUpdate,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PromptTemplateInfo:
    """Update an existing prompt template."""
    result = await db.execute(
        select(PromptTemplate).where(PromptTemplate.id == template_id)
    )
    template = result.scalar_one_or_none()
    if template is None:
        raise AppError("prompt_template_not_found", status_code=404)

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(template, field, value)

    await db.commit()
    await db.refresh(template)
    await write_audit(
        db, current_user, "prompt_template.update",
        target_type="prompt_template", target_id=template.id, target_label=template.name,
    )
    return _template_to_info(template)


@router.delete("/prompt-templates/{template_id}", status_code=204)
async def delete_prompt_template(
    template_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
    """Delete a prompt template."""
    result = await db.execute(
        select(PromptTemplate).where(PromptTemplate.id == template_id)
    )
    template = result.scalar_one_or_none()
    if template is None:
        raise AppError("prompt_template_not_found", status_code=404)

    name = template.name
    await db.delete(template)
    await db.commit()
    await write_audit(
        db, current_user, "prompt_template.delete",
        target_type="prompt_template", target_id=template_id, target_label=name,
    )


# ---------------------------------------------------------------------------
# Sensitive Word Endpoints
# ---------------------------------------------------------------------------


@router.get("/sensitive-words")
async def list_sensitive_words(
    category: str | None = Query(None),
    severity: str | None = Query(None),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[SensitiveWordInfo]:
    """List all sensitive words, ordered by category then word."""
    stmt = select(SensitiveWord)
    if category is not None:
        stmt = stmt.where(SensitiveWord.category == category)
    if severity is not None:
        stmt = stmt.where(SensitiveWord.severity == severity)
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
        severity=body.severity,
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
            severity=body.severity,
            created_by_id=current_user.id,
        ))
        added += 1

    if added > 0:
        await db.commit()
    await write_audit(
        db, current_user, "sensitive_word.batch_import",
        detail=f"added {added} words (category={body.category}, severity={body.severity})",
    )
    return BatchImportResponse(added=added)


@router.delete("/sensitive-words/{word_id}", status_code=204)
async def delete_sensitive_word(
    word_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
):
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
                severity=w.severity,
            ))

    return SensitiveWordCheckResponse(
        matched=matched,
        clean=len(matched) == 0,
    )

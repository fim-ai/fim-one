"""Admin API endpoints for API key management."""

from __future__ import annotations

import hashlib
import math
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.auth import get_current_admin
from fim_one.web.exceptions import AppError
from fim_one.web.models import ApiKey, User

from .admin_utils import write_audit

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ApiKeyInfo(BaseModel):
    id: str
    name: str
    key_prefix: str
    scopes: str | None = None
    is_active: bool
    user_id: str | None = None
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    total_requests: int = 0
    created_at: str


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    user_id: str | None = None
    scopes: str | None = None
    expires_at: datetime | None = None


class ApiKeyCreateResponse(BaseModel):
    """Returned only at creation time -- includes the full key."""

    id: str
    name: str
    key: str
    key_prefix: str
    scopes: str | None = None
    user_id: str | None = None
    expires_at: datetime | None = None
    created_at: str


class ApiKeyToggleRequest(BaseModel):
    is_active: bool


class PaginatedApiKeyResponse(BaseModel):
    items: list[ApiKeyInfo]
    total: int
    page: int
    size: int
    pages: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_api_key() -> str:
    """Generate a random API key: ``fim_`` + 44 URL-safe alphanumeric chars."""
    return "fim_" + secrets.token_urlsafe(33)[:44]


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/api-keys", response_model=PaginatedApiKeyResponse)
async def list_api_keys(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user_id: str | None = Query(None),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedApiKeyResponse:
    """Return a paginated list of API keys. Requires admin privileges."""
    query = select(ApiKey)
    count_query = select(func.count()).select_from(ApiKey)

    if user_id is not None:
        query = query.where(ApiKey.user_id == user_id)
        count_query = count_query.where(ApiKey.user_id == user_id)

    # Total count
    total = (await db.execute(count_query)).scalar() or 0

    # Paginated rows (newest first)
    query = query.order_by(ApiKey.created_at.desc())
    query = query.offset((page - 1) * size).limit(size)
    result = await db.execute(query)
    rows = result.scalars().all()

    items = [
        ApiKeyInfo(
            id=row.id,
            name=row.name,
            key_prefix=row.key_prefix,
            scopes=row.scopes,
            is_active=row.is_active,
            user_id=row.user_id,
            expires_at=row.expires_at,
            last_used_at=row.last_used_at,
            total_requests=row.total_requests,
            created_at=row.created_at.isoformat() if row.created_at else "",
        )
        for row in rows
    ]

    return PaginatedApiKeyResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total > 0 else 1,
    )


@router.post("/api-keys", response_model=ApiKeyCreateResponse, status_code=201)
async def create_api_key(
    body: ApiKeyCreateRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiKeyCreateResponse:
    """Create a new API key. The full key is returned only once."""
    raw_key = _generate_api_key()
    key_prefix = raw_key[:8]
    key_hash = _hash_key(raw_key)

    api_key = ApiKey(
        name=body.name,
        user_id=body.user_id,
        key_prefix=key_prefix,
        key_hash=key_hash,
        scopes=body.scopes,
        expires_at=body.expires_at,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    await write_audit(
        db,
        admin=current_user,
        action="api_key.create",
        target_type="api_key",
        target_id=api_key.id,
        target_label=api_key.name,
        detail=f"prefix={key_prefix}, scopes={body.scopes or 'all'}",
    )

    return ApiKeyCreateResponse(
        id=api_key.id,
        name=api_key.name,
        key=raw_key,
        key_prefix=key_prefix,
        scopes=api_key.scopes,
        user_id=api_key.user_id,
        expires_at=api_key.expires_at,
        created_at=api_key.created_at.isoformat() if api_key.created_at else "",
    )


@router.patch("/api-keys/{key_id}/active")
async def toggle_api_key_active(
    key_id: str,
    body: ApiKeyToggleRequest,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiKeyInfo:
    """Enable or disable an API key."""
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise AppError("api_key_not_found", status_code=404)

    api_key.is_active = body.is_active
    await db.commit()
    await db.refresh(api_key)

    action = "api_key.enable" if body.is_active else "api_key.disable"
    await write_audit(
        db,
        admin=current_user,
        action=action,
        target_type="api_key",
        target_id=api_key.id,
        target_label=api_key.name,
    )

    return ApiKeyInfo(
        id=api_key.id,
        name=api_key.name,
        key_prefix=api_key.key_prefix,
        scopes=api_key.scopes,
        is_active=api_key.is_active,
        user_id=api_key.user_id,
        expires_at=api_key.expires_at,
        last_used_at=api_key.last_used_at,
        total_requests=api_key.total_requests,
        created_at=api_key.created_at.isoformat() if api_key.created_at else "",
    )


@router.delete("/api-keys/{key_id}", status_code=204)
async def delete_api_key(
    key_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """Revoke and permanently delete an API key. Requires admin privileges."""
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise AppError("api_key_not_found", status_code=404)

    key_name = api_key.name
    key_id_val = api_key.id

    await db.delete(api_key)
    await db.commit()

    await write_audit(
        db,
        admin=current_user,
        action="api_key.delete",
        target_type="api_key",
        target_id=key_id_val,
        target_label=key_name,
    )

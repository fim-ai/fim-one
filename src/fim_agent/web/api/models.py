"""Model configuration CRUD endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fim_agent.db import get_session
from fim_agent.web.exceptions import AppError
from fim_agent.web.auth import get_current_user
from fim_agent.web.models import ModelConfig, User
from fim_agent.web.schemas.common import ApiResponse
from fim_agent.web.schemas.model_config import (
    ModelConfigCreate,
    ModelConfigResponse,
    ModelConfigUpdate,
)

router = APIRouter(prefix="/api/models", tags=["models"])


def _config_to_response(cfg: ModelConfig) -> ModelConfigResponse:
    return ModelConfigResponse(
        id=cfg.id,
        name=cfg.name,
        provider=cfg.provider,
        model_name=cfg.model_name,
        base_url=cfg.base_url,
        category=cfg.category,
        temperature=cfg.temperature,
        max_output_tokens=getattr(cfg, "max_output_tokens", None),
        context_size=getattr(cfg, "context_size", None),
        role=getattr(cfg, "role", None),
        is_default=cfg.is_default,
        is_active=cfg.is_active,
        created_at=cfg.created_at.isoformat() if cfg.created_at else "",
        updated_at=cfg.updated_at.isoformat() if cfg.updated_at else None,
    )


async def _unset_role(
    db: AsyncSession,
    role: str,
    exclude_id: str | None = None,
) -> None:
    """Ensure only one system config has a given role."""
    stmt = select(ModelConfig).where(
        ModelConfig.role == role,
        ModelConfig.user_id.is_(None),
    )
    if exclude_id:
        stmt = stmt.where(ModelConfig.id != exclude_id)
    result = await db.execute(stmt)
    for cfg in result.scalars().all():
        cfg.role = None


async def _unset_defaults(
    db: AsyncSession,
    user_id: str,
    category: str,
    *,
    exclude_id: str | None = None,
) -> None:
    stmt = (
        update(ModelConfig)
        .where(
            ModelConfig.user_id == user_id,
            ModelConfig.category == category,
            ModelConfig.is_default.is_(True),
        )
        .values(is_default=False)
    )
    if exclude_id:
        stmt = stmt.where(ModelConfig.id != exclude_id)
    await db.execute(stmt)


@router.post("", response_model=ApiResponse)
async def create_model_config(
    body: ModelConfigCreate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    if body.is_default:
        await _unset_defaults(db, current_user.id, body.category)

    # System-level role configs use user_id=None (singleton per role)
    config_user_id: str | None = current_user.id
    if body.role in ("general", "fast"):
        await _unset_role(db, body.role)
        config_user_id = None

    cfg = ModelConfig(
        user_id=config_user_id,
        name=body.name,
        provider=body.provider,
        model_name=body.model_name,
        base_url=body.base_url,
        api_key=body.api_key,
        category=body.category,
        temperature=body.temperature,
        max_output_tokens=body.max_output_tokens,
        context_size=body.context_size,
        is_default=body.is_default,
        role=body.role,
    )
    db.add(cfg)
    await db.commit()
    result = await db.execute(
        select(ModelConfig).where(ModelConfig.id == cfg.id)
    )
    cfg = result.scalar_one()
    return ApiResponse(data=_config_to_response(cfg).model_dump())


@router.get("", response_model=ApiResponse)
async def list_model_configs(
    category: str | None = Query(None),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    base = select(ModelConfig).where(
        or_(
            ModelConfig.user_id == current_user.id,
            ModelConfig.user_id.is_(None),
        )
    )
    if category is not None:
        base = base.where(ModelConfig.category == category)

    result = await db.execute(base.order_by(ModelConfig.created_at.desc()))
    configs = result.scalars().all()
    return ApiResponse(
        data=[_config_to_response(c).model_dump() for c in configs]
    )


@router.get("/{model_id}", response_model=ApiResponse)
async def get_model_config(
    model_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    result = await db.execute(
        select(ModelConfig).where(
            ModelConfig.id == model_id,
            or_(
                ModelConfig.user_id == current_user.id,
                ModelConfig.user_id.is_(None),
            ),
        )
    )
    cfg = result.scalar_one_or_none()
    if cfg is None:
        raise AppError("model_config_not_found", status_code=404)
    return ApiResponse(data=_config_to_response(cfg).model_dump())


@router.put("/{model_id}", response_model=ApiResponse)
async def update_model_config(
    model_id: str,
    body: ModelConfigUpdate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    result = await db.execute(
        select(ModelConfig).where(
            ModelConfig.id == model_id,
            ModelConfig.user_id == current_user.id,
        )
    )
    cfg = result.scalar_one_or_none()
    if cfg is None:
        raise AppError("model_config_not_found", status_code=404)

    update_data = body.model_dump(exclude_unset=True)

    # Handle default toggling before applying fields
    if update_data.get("is_default") is True:
        category = update_data.get("category", cfg.category)
        await _unset_defaults(
            db, current_user.id, category, exclude_id=cfg.id
        )

    # Handle role assignment: ensure only one system config holds a given role
    new_role = update_data.get("role")
    if new_role is not None:
        await _unset_role(db, new_role, exclude_id=cfg.id)

    for field, value in update_data.items():
        setattr(cfg, field, value)

    await db.commit()
    result = await db.execute(
        select(ModelConfig).where(ModelConfig.id == cfg.id)
    )
    cfg = result.scalar_one()
    return ApiResponse(data=_config_to_response(cfg).model_dump())


@router.delete("/{model_id}", response_model=ApiResponse)
async def delete_model_config(
    model_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    result = await db.execute(
        select(ModelConfig).where(ModelConfig.id == model_id)
    )
    cfg = result.scalar_one_or_none()
    if cfg is None:
        raise AppError("model_config_not_found", status_code=404)

    if cfg.user_id is None:
        raise AppError("cannot_delete_system_model", status_code=400)

    if cfg.user_id != current_user.id:
        raise AppError("model_config_not_found", status_code=404)

    await db.delete(cfg)
    await db.commit()
    return ApiResponse(data={"deleted": model_id})

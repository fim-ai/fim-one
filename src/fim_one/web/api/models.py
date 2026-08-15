"""Model configuration read endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.exceptions import AppError
from fim_one.web.auth import get_current_user
from fim_one.db.models import ModelConfig, User
from fim_one.web.schemas.common import ApiResponse
from fim_one.web.schemas.model_config import ModelConfigResponse

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
        json_mode_enabled=getattr(cfg, "json_mode_enabled", True),
        supports_vision=getattr(cfg, "supports_vision", False),
        created_at=cfg.created_at.isoformat() if cfg.created_at else "",
        updated_at=cfg.updated_at.isoformat() if cfg.updated_at else None,
    )


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


@router.get("/effective/vision", response_model=ApiResponse)
async def get_effective_vision_support(
    agent_id: str | None = Query(None),
    conversation_id: str | None = Query(None),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Report whether the model a chat turn would use can read images.

    The composer calls this to warn before an image is attached to a
    text-only model: chat gates uploaded images on exactly this value, so
    ``false`` means the picture would reach the model as a filename only.
    Resolution mirrors the chat path (agent config > active model group >
    system default), which is why it delegates instead of re-deriving.
    """
    from fim_one.web.api.chat import (
        _resolve_agent_config,
        _resolve_model_supports_vision,
    )

    agent_cfg = await _resolve_agent_config(
        agent_id, conversation_id, user_id=current_user.id
    )
    supports_vision = await _resolve_model_supports_vision(agent_cfg, db)
    return ApiResponse(data={"supports_vision": supports_vision})


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

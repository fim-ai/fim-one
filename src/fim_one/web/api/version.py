"""Public version endpoint — no authentication required."""

from __future__ import annotations

import os
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one import __version__
from fim_one.db import get_session
from fim_one.web.auth import get_current_user
from fim_one.web.deps import (
    _main_model,
    _fast_model,
    _reasoning_model,
    _main_max_output,
    _fast_max_output,
    _reasoning_max_output,
    _reasoning_context_size,
)
from fim_one.web.models.model_provider import ModelGroup, ModelProviderModel
from fim_one.web.models.user import User

router = APIRouter(prefix="/api", tags=["system"])

# Captured once at module load time (≈ server startup).
_SERVER_START_TIME = datetime.now(UTC).isoformat()


@router.get("/version")
async def get_version() -> dict[str, str]:
    """Return application version metadata.

    This is a public endpoint — no authentication required.
    """
    return {
        "version": __version__,
        "build_time": _SERVER_START_TIME,
        "app_name": "FIM One",
    }


def _is_group_model_usable(model: ModelProviderModel | None) -> bool:
    """Check if a group slot's model is active and its provider is active."""
    if model is None:
        return False
    if not model.is_active:
        return False
    provider = model.provider
    if provider is None or not provider.is_active:
        return False
    return True


@router.get("/active-models")
async def get_active_models(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Return the 3 currently effective models (general, fast, reasoning).

    Resolution order:
    1. Active ModelGroup in DB (if any) — use each slot's model, falling back
       to ENV for null/inactive slots or null fields.
    2. ENV variables directly.
    """
    # ENV fallback values
    env_general_context = int(
        os.environ.get("LLM_CONTEXT_SIZE", "128000")
    )
    env_fast_context = int(
        os.environ.get("FAST_LLM_CONTEXT_SIZE", "")
        or os.environ.get("LLM_CONTEXT_SIZE", "128000")
    )
    env_reasoning_context = _reasoning_context_size()

    # Defaults from ENV
    general = {
        "role": "general",
        "model_name": _main_model(),
        "context_size": env_general_context,
        "max_output_tokens": _main_max_output(),
    }
    fast = {
        "role": "fast",
        "model_name": _fast_model(),
        "context_size": env_fast_context,
        "max_output_tokens": _fast_max_output(),
    }
    reasoning = {
        "role": "reasoning",
        "model_name": _reasoning_model(),
        "context_size": env_reasoning_context,
        "max_output_tokens": _reasoning_max_output(),
    }

    source = "env"
    group_name: str | None = None

    # Check for an active model group
    stmt = select(ModelGroup).where(
        ModelGroup.is_active == True  # noqa: E712
    ).limit(1)
    result = await db.execute(stmt)
    group = result.scalar_one_or_none()

    if group is not None:
        source = "group"
        group_name = group.name

        # General slot
        gm = group.general_model
        if gm is not None and _is_group_model_usable(gm):
            general = {
                "role": "general",
                "model_name": gm.model_name,
                "context_size": gm.context_size or env_general_context,
                "max_output_tokens": gm.max_output_tokens or _main_max_output(),
            }

        # Fast slot
        fm = group.fast_model
        if fm is not None and _is_group_model_usable(fm):
            fast = {
                "role": "fast",
                "model_name": fm.model_name,
                "context_size": fm.context_size or env_fast_context,
                "max_output_tokens": fm.max_output_tokens or _fast_max_output(),
            }

        # Reasoning slot
        rm = group.reasoning_model
        if rm is not None and _is_group_model_usable(rm):
            reasoning = {
                "role": "reasoning",
                "model_name": rm.model_name,
                "context_size": rm.context_size or env_reasoning_context,
                "max_output_tokens": rm.max_output_tokens or _reasoning_max_output(),
            }

    return {
        "models": [general, fast, reasoning],
        "source": source,
        "group_name": group_name,
    }

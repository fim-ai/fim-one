"""Who may run an agent whose inline model settings name their own endpoint.

An agent's ``model_config_json`` may carry a ``base_url`` (with the
``api_key`` that goes with it): every turn of a conversation with that
agent, the user's messages and files included, is sent to that address.
Such an agent is used by its owner only, unless an administrator turns on
the ``agents.shared_custom_model_endpoints`` system setting; with the
setting off it cannot be published either.  Selecting a model
configuration a user created with its own ``base_url`` counts the same.
Agents that select a model an administrator configured are not affected.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: ``system_settings`` key; ``"true"`` lets published agents keep their own endpoint.
SETTING_SHARED_CUSTOM_MODEL_ENDPOINTS = "agents.shared_custom_model_endpoints"


_CONFIG_ID_KEYS = ("model_config_id", "fast_model_config_id")


def _config_ids(model_config_json: Any) -> list[str]:
    if not isinstance(model_config_json, dict):
        return []
    return [
        value
        for key in _CONFIG_ID_KEYS
        if isinstance(value := model_config_json.get(key), str) and value
    ]


async def user_endpoint_config_ids(db: AsyncSession, configs: Iterable[Any]) -> set[str]:
    """Ids, among those *configs* select, of user-owned model configs with a ``base_url``.

    A model configuration a user created (``user_id`` set) that names its
    own ``base_url`` is a custom endpoint, the same as inline settings.
    """
    ids = {cid for cfg in configs for cid in _config_ids(cfg)}
    if not ids:
        return set()
    from sqlalchemy import select

    from fim_one.db.models.model_config import ModelConfig

    rows = (
        await db.execute(
            select(ModelConfig.id, ModelConfig.base_url).where(
                ModelConfig.id.in_(ids), ModelConfig.user_id.is_not(None)
            )
        )
    ).all()
    return {rid for rid, url in rows if isinstance(url, str) and url.strip()}


def has_custom_model_endpoint(
    model_config_json: Any, user_endpoint_ids: Collection[str] = frozenset()
) -> bool:
    """Whether an agent's model settings name an endpoint of their own.

    Either inline (``base_url``), or by selecting a model configuration
    listed in *user_endpoint_ids* (see :func:`user_endpoint_config_ids`).
    """
    if not isinstance(model_config_json, dict):
        return False
    base_url = model_config_json.get("base_url")
    if isinstance(base_url, str) and base_url.strip():
        return True
    return any(cid in user_endpoint_ids for cid in _config_ids(model_config_json))


async def names_custom_model_endpoint(db: AsyncSession, model_config_json: Any) -> bool:
    """:func:`has_custom_model_endpoint` for one agent, looking its model configs up."""
    return has_custom_model_endpoint(
        model_config_json, await user_endpoint_config_ids(db, [model_config_json])
    )


async def shared_custom_endpoints_allowed(db: AsyncSession) -> bool:
    """The administrator's setting; off unless it is the string ``"true"``."""
    from sqlalchemy import select

    from fim_one.db.models import SystemSetting

    raw = (
        await db.execute(
            select(SystemSetting.value).where(
                SystemSetting.key == SETTING_SHARED_CUSTOM_MODEL_ENDPOINTS
            )
        )
    ).scalar_one_or_none()
    return (raw or "").strip().lower() == "true"

"""Module feature flags — admin-controlled soft-shelving of whole modules.

The platform grew Skills and Workflows as full product surfaces, but a
fresh install should start simple: those modules are **off by default**
and an admin turns them on from Admin → Settings → Modules when needed.
This mirrors the billing flag's "switch-only state" principle — flipping
a flag is a pure visibility change with **no** data side-effects. Nothing
is deleted when a module is turned off; existing rows simply become
unreachable until it is turned back on.

Keys live in the ``system_settings`` key/value store, same as the other
runtime knobs (billing, registration mode, maintenance mode).

Public API
----------
- :func:`is_feature_enabled` — read one flag (default ``False``).
- :func:`are_features_enabled` — read all module flags as a dict (for the
  public ``/api/version`` bootstrap so the frontend can hide nav on mount).
- :func:`require_skills_enabled` / :func:`require_workflows_enabled` —
  FastAPI dependencies that raise ``HTTPException(404)`` when off, so a
  shelved module's endpoints behave as if they don't exist.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.db.models import SystemSetting

#: Stable system_settings keys for the soft-shelvable modules.
SETTING_FEATURE_SKILLS = "feature.skills"
SETTING_FEATURE_WORKFLOWS = "feature.workflows"

#: All module flags, in nav order. Default is OFF for every module.
MODULE_FLAGS: tuple[str, ...] = (
    SETTING_FEATURE_SKILLS,
    SETTING_FEATURE_WORKFLOWS,
)


async def is_feature_enabled(db: AsyncSession, key: str) -> bool:
    """Return ``True`` only when the flag is the literal string ``"true"``.

    Absent / empty / any other value → ``False``. Fresh installs and
    pre-flag deployments therefore start with the module shelved, which is
    the intended default: the product boots simple and an admin opts in.
    """
    result = await db.execute(
        select(SystemSetting.value).where(SystemSetting.key == key)
    )
    raw = result.scalar_one_or_none()
    if raw is None:
        return False
    return raw.strip().lower() == "true"


async def are_features_enabled(db: AsyncSession) -> dict[str, bool]:
    """Read every module flag at once — shaped for the frontend bootstrap.

    Returns short keys (``"skills"``, ``"workflows"``) so the response is
    a stable public contract independent of the ``feature.*`` storage key.
    """
    return {
        "skills": await is_feature_enabled(db, SETTING_FEATURE_SKILLS),
        "workflows": await is_feature_enabled(db, SETTING_FEATURE_WORKFLOWS),
    }


async def require_skills_enabled(
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """FastAPI dependency: 404 when the Skills module is shelved.

    404 (not 403) so a disabled module's endpoints read as nonexistent —
    consistent with the module being hidden from the UI entirely.
    """
    if not await is_feature_enabled(db, SETTING_FEATURE_SKILLS):
        raise HTTPException(status_code=404, detail="module_disabled")


async def require_workflows_enabled(
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """FastAPI dependency: 404 when the Workflows module is shelved."""
    if not await is_feature_enabled(db, SETTING_FEATURE_WORKFLOWS):
        raise HTTPException(status_code=404, detail="module_disabled")


__all__ = [
    "MODULE_FLAGS",
    "SETTING_FEATURE_SKILLS",
    "SETTING_FEATURE_WORKFLOWS",
    "are_features_enabled",
    "is_feature_enabled",
    "require_skills_enabled",
    "require_workflows_enabled",
]

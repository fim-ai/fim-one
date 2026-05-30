"""Helper for resolving the default plan id at user-registration time.

Post-Stripe-MVP every newly registered user must be bound to at least
the default plan (Free, by convention) so the quota resolver can
report a finite tier without falling through to the defensive
system-wide ``default_token_quota``.

The pointer of record is ``system_settings.default_plan_id``. The
legacy ``WHERE slug='free'`` lookup is retained as a transition-safety
fallback so installs that haven't yet run the activation flow (or the
``add_default_plan_pointer`` migration) still register users
correctly.

This helper is intentionally tiny and side-effect-free: it returns the
id (or ``None`` if neither the pointer nor a Free row is available)
and never raises so a registration never fails because of a transient
billing-table issue.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db.models import BillingPlan, SystemSetting

logger = logging.getLogger(__name__)

_FREE_PLAN_SLUG = "free"
_SETTING_DEFAULT_PLAN_ID = "default_plan_id"


async def get_default_plan_id(db: AsyncSession) -> int | None:
    """Return the configured default plan id, or ``None`` if unset.

    Lookup order:

    1. ``system_settings.default_plan_id`` — the canonical pointer set
       by either the dedicated migration or the activation flow.
    2. ``billing_plans WHERE slug='free'`` — transition-safety fallback
       for installs that pre-date the pointer.
    3. ``None`` — no default configured; registration will leave
       ``users.plan_id`` NULL and the quota chain falls through to the
       system-wide ``default_token_quota`` setting.

    Failures at every step are logged at WARNING and surfaced as
    ``None`` so the registration code path falls through gracefully —
    the backfill migration (and the activation flow) cover any rows we
    miss here.
    """
    try:
        # Tier 1: explicit pointer.
        ptr_result = await db.execute(
            select(SystemSetting.value).where(
                SystemSetting.key == _SETTING_DEFAULT_PLAN_ID
            )
        )
        raw = ptr_result.scalar_one_or_none()
        if raw is not None:
            raw_stripped = raw.strip()
            if raw_stripped:
                try:
                    return int(raw_stripped)
                except ValueError:
                    logger.warning(
                        "system_settings.default_plan_id is not an integer: %r",
                        raw,
                    )

        # Tier 2: legacy slug lookup.
        result = await db.execute(
            select(BillingPlan.id).where(BillingPlan.slug == _FREE_PLAN_SLUG)
        )
        plan_id = result.scalar_one_or_none()
        if plan_id is None:
            logger.warning(
                "Default plan not configured (no system_settings.default_plan_id "
                "and slug=%s missing); new user will be created without plan "
                "binding.",
                _FREE_PLAN_SLUG,
            )
            return None
        return int(plan_id)
    except Exception:  # noqa: BLE001 — registration must not fail here
        logger.exception(
            "Failed to look up default plan id; falling through to NULL "
            "plan_id."
        )
        return None


# Backward-compatibility shim. The old name was used in auth/oauth and
# tests; we keep it as a thin alias so a stray external caller doesn't
# regress, but new code should import :func:`get_default_plan_id`.
async def get_free_plan_id(db: AsyncSession) -> int | None:
    """Deprecated alias for :func:`get_default_plan_id`."""
    return await get_default_plan_id(db)


__all__ = ["get_default_plan_id", "get_free_plan_id"]

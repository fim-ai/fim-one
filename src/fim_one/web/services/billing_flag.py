"""Billing access model — instance posture + Stripe pipeline gate.

An instance is always in exactly one posture (``access_model``):

- ``off`` — no Stripe, no catalogue. Quota is ``default_token_quota``
  (``0`` = unlimited). Software default.
- ``freemium`` — Stripe on. New users bind to the default plan
  (seeded as slug ``free``). Canceled paid users demote to that plan.
- ``paid_only`` — Stripe on. No default plan. Users without a paid
  plan are unentitled (chat returns 402) until they subscribe.

``billing_enabled`` is derived (``access_model != off``) and kept in
sync so existing endpoint gates do not change. Installs that only
have the legacy flag map ``true`` → ``freemium``.

Activation still follows "front-loaded data, switch-only state":
first switch into a paid posture seeds the catalogue; later off/on
of the same posture is a flag flip.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.config import settings
from fim_one.db.models import BillingPlan, SystemSetting, User

logger = logging.getLogger(__name__)

#: Stable name for the system_settings row that gates the billing pipeline.
SETTING_BILLING_ENABLED = "billing_enabled"

#: Stable name for the pointer that replaces ``WHERE slug='free'`` lookups.
SETTING_DEFAULT_PLAN_ID = "default_plan_id"

#: Instance posture. Always exactly one of the three values below.
SETTING_ACCESS_MODEL = "access_model"

ACCESS_MODEL_OFF = "off"
ACCESS_MODEL_FREEMIUM = "freemium"
ACCESS_MODEL_PAID_ONLY = "paid_only"
ACCESS_MODELS: frozenset[str] = frozenset(
    {ACCESS_MODEL_OFF, ACCESS_MODEL_FREEMIUM, ACCESS_MODEL_PAID_ONLY}
)


async def get_access_model(db: AsyncSession) -> str:
    """Return the instance posture, mapping the legacy flag if needed.

    Preference: explicit ``access_model`` if it is one of the three
    known values. Otherwise ``billing_enabled=true`` maps to
    ``freemium`` (the only posture the v0.8.6 pipeline knew) and
    everything else to ``off``.
    """
    raw = await _get_setting(db, SETTING_ACCESS_MODEL)
    if raw is not None and raw in ACCESS_MODELS:
        return raw
    if await _legacy_billing_flag_on(db):
        return ACCESS_MODEL_FREEMIUM
    return ACCESS_MODEL_OFF


async def _legacy_billing_flag_on(db: AsyncSession) -> bool:
    result = await db.execute(
        select(SystemSetting.value).where(
            SystemSetting.key == SETTING_BILLING_ENABLED
        )
    )
    raw = result.scalar_one_or_none()
    if raw is None:
        return False
    return raw.strip().lower() == "true"


async def is_billing_enabled(db: AsyncSession) -> bool:
    """Return ``True`` when the instance is in a Stripe-using posture.

    True for ``freemium`` and ``paid_only``. Fresh installs default to
    ``off`` so private boxes never surface payment UX they didn't
    configure.
    """
    return await get_access_model(db) != ACCESS_MODEL_OFF


async def require_billing_enabled(
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """FastAPI dependency: 503 when billing is gated off.

    Apply to every billing-related router (user-facing, admin, webhook)
    so the whole pipeline goes silent in lock-step. This keeps the gate
    centralised — sprinkling ``if not flag: raise`` into each handler
    is exactly the kind of drift this dependency exists to prevent.
    """
    if not await is_billing_enabled(db):
        raise HTTPException(
            status_code=503,
            detail="Billing is not enabled on this instance",
        )


async def _set_setting(db: AsyncSession, key: str, value: str) -> None:
    """Upsert a row in ``system_settings``."""
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == key)
    )
    row = result.scalar_one_or_none()
    if row is None:
        db.add(SystemSetting(key=key, value=value))
    else:
        row.value = value


async def _get_setting(
    db: AsyncSession, key: str
) -> str | None:
    result = await db.execute(
        select(SystemSetting.value).where(SystemSetting.key == key)
    )
    raw = result.scalar_one_or_none()
    if raw is None:
        return None
    raw = raw.strip()
    return raw if raw else None


# ---------------------------------------------------------------------------
# Activation / deactivation
# ---------------------------------------------------------------------------


# Seed catalogue. Re-running activation against an install that already
# has these slugs is a no-op. Paid templates ship with a null Stripe
# Price — the operator pastes theirs from their own Dashboard. Never
# seed a live ``price_…`` from a FIM account.
#
# The included-tier quota is copied from ``default_token_quota`` at
# activation (including ``0`` = unlimited). The seed value below is
# only used when that setting is missing or unparseable.
_INCLUDED_FALLBACK_QUOTA = 0

_SEED_INCLUDED_PLAN: dict[str, Any] = {
    "slug": "free",
    "name": "Free",
    "stripe_price_id": None,
    "monthly_token_quota": _INCLUDED_FALLBACK_QUOTA,
    "description": "Basic features",
    "sort_order": 0,
    "is_active": True,
}

_SEED_PAID_TEMPLATE: dict[str, Any] = {
    "slug": "pro",
    "name": "Pro",
    "stripe_price_id": None,
    "monthly_token_quota": 5_000_000,
    "description": "Priority support",
    "sort_order": 1,
    "is_active": True,
}

_DEFAULT_TOKEN_QUOTA_KEY = "default_token_quota"


async def _resolve_free_seed_quota(db: AsyncSession) -> int:
    """Resolve Free's seed quota from ``default_token_quota`` if set.

    Activation respects the operator's existing legacy quota knob — if a
    pre-billing install already configured ``default_token_quota``, we
    seed Free with that exact value rather than inserting the hardcoded
    fallback and then immediately overwriting it.

    Copies the operator's existing knob, including ``0`` (unlimited).
    Does not rewrite ``0`` to a positive fallback. Unset / empty /
    non-numeric values fall back to :data:`_INCLUDED_FALLBACK_QUOTA`.
    """
    raw = await _get_setting(db, _DEFAULT_TOKEN_QUOTA_KEY)
    if raw is None:
        return _INCLUDED_FALLBACK_QUOTA
    try:
        n = int(raw)
    except ValueError:
        return _INCLUDED_FALLBACK_QUOTA
    return n if n >= 0 else _INCLUDED_FALLBACK_QUOTA


async def _resolve_default_pointer_target(
    db: AsyncSession, free_plan_id: int
) -> int | None:
    """Decide whether the ``default_plan_id`` pointer needs to be (re)set.

    Returns the id to write when the pointer is missing OR points at a
    row that no longer exists / has been soft-deleted (``is_active=False``).
    Returns ``None`` when the existing pointer is still healthy.

    This is the self-heal path that protects against the rare drift
    where Free was hard-deleted out-of-band and recreated with a new id —
    activation alone wouldn't notice without this check, so new
    registrations would get assigned to the dangling pointer.
    """
    raw = await _get_setting(db, SETTING_DEFAULT_PLAN_ID)
    if raw is None:
        return free_plan_id
    try:
        ptr_id = int(raw)
    except ValueError:
        logger.warning(
            "default_plan_id=%r is not an integer; resetting to free=%s",
            raw,
            free_plan_id,
        )
        return free_plan_id
    pointed = await db.get(BillingPlan, ptr_id)
    if pointed is None or not pointed.is_active:
        logger.warning(
            "default_plan_id=%s points at missing/inactive plan; "
            "self-healing to free=%s",
            ptr_id,
            free_plan_id,
        )
        return free_plan_id
    return None


def _stripe_env_configured() -> tuple[bool, str | None]:
    """Verify Stripe credentials are set in the runtime config.

    Returns a ``(ok, missing_key)`` tuple. ``missing_key`` names the
    first missing variable so the API surface can render a precise
    error message without leaking secret values.
    """
    try:
        secret = settings.STRIPE_SECRET_KEY
        webhook = settings.STRIPE_WEBHOOK_SECRET
    except Exception:  # noqa: BLE001 — bad config surfaces here
        return False, "STRIPE_SECRET_KEY"
    if secret is None:
        return False, "STRIPE_SECRET_KEY"
    if webhook is None:
        return False, "STRIPE_WEBHOOK_SECRET"
    return True, None


async def _seed_plan_if_absent(
    db: AsyncSession, spec: dict[str, Any], *, quota: int | None = None
) -> bool:
    """Insert *spec* when no row with that slug exists. Returns True if inserted."""
    existing = await db.execute(
        select(BillingPlan).where(BillingPlan.slug == spec["slug"])
    )
    if existing.scalar_one_or_none() is not None:
        return False
    db.add(
        BillingPlan(
            slug=spec["slug"],
            name=spec["name"],
            stripe_price_id=spec["stripe_price_id"],
            monthly_token_quota=(
                quota if quota is not None else spec["monthly_token_quota"]
            ),
            description=spec["description"],
            features_json={},
            sort_order=spec["sort_order"],
            is_active=spec["is_active"],
        )
    )
    return True


async def activate_billing(
    db: AsyncSession,
    *,
    access_model: str = ACCESS_MODEL_FREEMIUM,
) -> dict[str, Any]:
    """Enter a Stripe-using posture, seeding the catalogue as needed.

    ``access_model`` must be ``freemium`` or ``paid_only``. Re-running
    on an already-seeded install reports zero seeded / backfilled.

    ``freemium``:
      seed included + paid template; set ``default_plan_id``; backfill
      NULL ``users.plan_id``.
    ``paid_only``:
      seed paid template only; do not set or self-heal the pointer;
      do not backfill. Existing included-tier bindings stay put.
    """
    if access_model not in (ACCESS_MODEL_FREEMIUM, ACCESS_MODEL_PAID_ONLY):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid access_model for activation: {access_model}",
        )

    ok, _missing = _stripe_env_configured()
    if not ok:
        raise HTTPException(
            status_code=400,
            detail=(
                "Configure STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET in "
                ".env first"
            ),
        )

    plans_seeded = 0
    free_plan_id: int | None = None
    users_backfilled = 0

    if access_model == ACCESS_MODEL_FREEMIUM:
        included_quota = await _resolve_free_seed_quota(db)
        if await _seed_plan_if_absent(
            db, _SEED_INCLUDED_PLAN, quota=included_quota
        ):
            plans_seeded += 1
        if await _seed_plan_if_absent(db, _SEED_PAID_TEMPLATE):
            plans_seeded += 1
        if plans_seeded:
            await db.flush()

        free_row = await db.execute(
            select(BillingPlan.id).where(BillingPlan.slug == "free")
        )
        free_plan_id = free_row.scalar_one_or_none()

        if free_plan_id is not None:
            target = await _resolve_default_pointer_target(db, free_plan_id)
            if target is not None:
                await _set_setting(db, SETTING_DEFAULT_PLAN_ID, str(target))

        if free_plan_id is not None:
            from sqlalchemy import text as _text

            count_row = await db.execute(
                _text("SELECT COUNT(*) FROM users WHERE plan_id IS NULL")
            )
            users_backfilled = int(count_row.scalar_one() or 0)
            if users_backfilled:
                await db.execute(
                    update(User)
                    .where(User.plan_id.is_(None))
                    .values(plan_id=free_plan_id)
                )
    else:
        # paid_only: catalogue needs a purchasable template, nothing else.
        if await _seed_plan_if_absent(db, _SEED_PAID_TEMPLATE):
            plans_seeded += 1
            await db.flush()

    await _set_setting(db, SETTING_ACCESS_MODEL, access_model)
    await _set_setting(db, SETTING_BILLING_ENABLED, "true")

    await db.commit()

    logger.info(
        "Billing activation: access_model=%s plans_seeded=%s "
        "users_backfilled=%s default_plan_id=%s",
        access_model,
        plans_seeded,
        users_backfilled,
        free_plan_id,
    )

    return {
        "plans_seeded": plans_seeded,
        "users_backfilled": users_backfilled,
        "default_plan_id": free_plan_id,
        "billing_enabled": True,
        "access_model": access_model,
    }


async def deactivate_billing(db: AsyncSession) -> dict[str, Any]:
    """Pure flag flip: turn billing off without touching any data.

    Plans, subscriptions, user.plan_id bindings — all remain intact. A
    later re-activation finds the catalogue already seeded and runs as
    a no-op.
    """
    await _set_setting(db, SETTING_ACCESS_MODEL, ACCESS_MODEL_OFF)
    await _set_setting(db, SETTING_BILLING_ENABLED, "false")
    await db.commit()
    return {
        "billing_enabled": False,
        "access_model": ACCESS_MODEL_OFF,
    }


async def set_access_model(db: AsyncSession, access_model: str) -> dict[str, Any]:
    """Switch posture. Tightening / widening is the caller's to confirm.

    ``off`` deactivates. ``freemium`` / ``paid_only`` activate with
    that seed policy. Same-value calls still run activation so a
    dangling pointer can self-heal on freemium.
    """
    if access_model not in ACCESS_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid access_model: {access_model}",
        )
    if access_model == ACCESS_MODEL_OFF:
        return await deactivate_billing(db)
    return await activate_billing(db, access_model=access_model)


__all__ = [
    "ACCESS_MODEL_FREEMIUM",
    "ACCESS_MODEL_OFF",
    "ACCESS_MODEL_PAID_ONLY",
    "ACCESS_MODELS",
    "SETTING_ACCESS_MODEL",
    "SETTING_BILLING_ENABLED",
    "SETTING_DEFAULT_PLAN_ID",
    "activate_billing",
    "deactivate_billing",
    "get_access_model",
    "is_billing_enabled",
    "require_billing_enabled",
    "set_access_model",
]

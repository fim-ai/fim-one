"""Plan-aware quota lookup.

Centralises the precedence chain for resolving a user's effective monthly
token budget so the chat layer (mid-stream guard), the billing layer
(checkout / display), and the user-facing Usage card all agree on a
single number.

Resolution chain — top wins (Scheme A: override-first + 3-state):

1. ``user.token_quota == 0``  → ``None`` (unlimited; admin VIP gift)
2. ``user.token_quota >  0``  → ``int(user.token_quota)`` (admin hard cap,
   overrides plan; intentional so an admin can throttle a paying user
   without revoking their plan).
3. ``user.token_quota IS NULL`` → ``user.plan.monthly_token_quota``
   when ``access_model`` is ``freemium`` or ``paid_only``.
   **Skipped when ``access_model`` is ``off``.**
   ``paid_only`` with no plan returns ``0`` (unentitled — not unlimited)
   and does **not** fall through to ``default_token_quota``.
4. *(defensive fallback)* the system-wide ``default_token_quota`` admin
   setting. Used by ``off`` (the instance-wide cap) and as a last
   resort for freemium users with no plan binding.
5. ``None`` (unlimited) — last-resort when *everything* above is unset.

The semantic 3-state for ``users.token_quota`` (preserve, do not change):

- ``NULL``  → "no override, follow plan/default"
- ``0``     → "unlimited (VIP gift)"
- ``N > 0`` → "hard cap at N, overrides plan"

The ``None`` return is also load-bearing: chat.py's ``_get_quota_status``
returns ``(0, 0)`` for "no cap" and skips enforcement.  Returning
``None`` here is what propagates that contract upward.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db.models import BillingPlan, SystemSetting, User
from fim_one.web.services.billing_flag import (
    ACCESS_MODEL_OFF,
    ACCESS_MODEL_PAID_ONLY,
    get_access_model,
)

logger = logging.getLogger(__name__)


_SETTING_DEFAULT_TOKEN_QUOTA = "default_token_quota"


async def _read_default_token_quota(db: AsyncSession) -> int | None:
    """Read the ``default_token_quota`` row from ``system_settings``.

    Returns the integer value, or ``None`` when the row is missing /
    empty / non-numeric. ``None`` here means "no system default
    configured" — the caller should treat that as "unlimited".
    """
    result = await db.execute(
        select(SystemSetting.value).where(
            SystemSetting.key == _SETTING_DEFAULT_TOKEN_QUOTA
        )
    )
    raw = result.scalar_one_or_none()
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "default_token_quota system setting is not an integer: %r", raw
        )
        return None
    if n <= 0:
        # Treat 0 / negative as "unlimited / disabled" to match the
        # convention used by chat.py (``user_quota <= 0`` means skip).
        return None
    return n


async def get_user_quota(user: User, db: AsyncSession) -> int | None:
    """Return the effective ``monthly_token_quota`` for *user*.

    The function never raises for "no plan" / "no quota": those map to
    ``None`` (unlimited), matching chat.py's existing semantic where
    ``user_quota <= 0`` means "skip enforcement".

    Precedence — see module docstring for the canonical chain.

    Args:
        user: The authenticated user. Read-only here; this function
            never mutates the row.
        db: An async session used to fetch :class:`BillingPlan` (the
            ``User.plan`` relationship is ``lazy="joined"`` but we
            explicitly query so the helper stays robust to detached
            instances) and the system_settings fallback row.

    Returns:
        The effective monthly token quota, or ``None`` for "unlimited".
    """
    # Tier 1 + 2: explicit admin override on the user row wins.
    # 0 means "VIP, unlimited"; >0 means "hard cap, overrides plan".
    if user.token_quota is not None:
        if user.token_quota == 0:
            return None
        return int(user.token_quota)

    # Tier 3: follow the user's billing plan when a Stripe posture is
    # on. ``off`` skips this so private deployments rely on the
    # instance-wide default, never on leftover catalogue rows.
    model = await get_access_model(db)
    if model != ACCESS_MODEL_OFF and user.plan_id is not None:
        plan = await db.get(BillingPlan, user.plan_id)
        if plan is not None and plan.monthly_token_quota is not None:
            return int(plan.monthly_token_quota)

    # paid_only + no plan = unentitled. Return 0 (no tokens), never
    # fall through to default_token_quota — a leftover 0 there would
    # leak as unlimited.
    if model == ACCESS_MODEL_PAID_ONLY:
        return 0

    # Tier 4: instance-wide default (``off``, or freemium with no plan).
    default = await _read_default_token_quota(db)
    if default is not None:
        return default

    # Tier 5: nothing configured anywhere → unlimited.
    return None


async def get_user_quota_by_id(user_id: str, db: AsyncSession) -> int | None:
    """Like :func:`get_user_quota` but takes a user id.

    Used by chat.py's ``_get_quota_status`` which only has the id in
    scope. Performs a single ``SELECT`` joining ``BillingPlan`` so the
    plan-bound quota and the legacy override come back in one round-trip.
    The system-default row is fetched separately only when both upper
    tiers are unset.
    """
    result = await db.execute(
        select(User.token_quota, User.plan_id, BillingPlan.monthly_token_quota)
        .select_from(User)
        .outerjoin(BillingPlan, User.plan_id == BillingPlan.id)
        .where(User.id == user_id)
    )
    row = result.one_or_none()
    if row is None:
        return None

    legacy_quota, plan_id, plan_quota = row

    # Tier 1 + 2: admin override wins.
    if legacy_quota is not None:
        if legacy_quota == 0:
            return None
        return int(legacy_quota)

    model = await get_access_model(db)

    # Tier 3: plan tier — gated on a Stripe posture.
    if (
        model != ACCESS_MODEL_OFF
        and plan_id is not None
        and plan_quota is not None
    ):
        return int(plan_quota)

    if model == ACCESS_MODEL_PAID_ONLY:
        return 0

    # Tier 4: system default.
    default = await _read_default_token_quota(db)
    if default is not None:
        return default

    # Tier 5: unlimited.
    return None


async def is_user_unentitled(user_id: str, db: AsyncSession) -> bool:
    """Return True when ``paid_only`` and the user has no plan or VIP gift.

    ``users.token_quota == 0`` (VIP unlimited) is never unentitled.
    A leftover included-tier ``plan_id`` after switching freemium →
    paid_only still counts as entitled (existing users keep access).
    """
    model = await get_access_model(db)
    if model != ACCESS_MODEL_PAID_ONLY:
        return False
    result = await db.execute(
        select(User.token_quota, User.plan_id).where(User.id == user_id)
    )
    row = result.one_or_none()
    if row is None:
        return True
    token_quota, plan_id = row
    if token_quota is not None:
        return False
    return plan_id is None


__all__ = ["get_user_quota", "get_user_quota_by_id", "is_user_unentitled"]

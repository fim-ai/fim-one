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

The ``None`` return is also load-bearing: a cap of ``0`` means "no cap" to
``core.assembly.quota`` and enforcement is skipped.  Returning ``None``
here is what propagates that contract upward.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.core.usage.meter import UsageScope, open_meter
from fim_one.db.models import BillingPlan, SystemSetting, User
from fim_one.web.auth import get_current_user
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

    For callers that only have the id in scope. Performs a single ``SELECT`` joining ``BillingPlan`` so the
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


async def get_quota_checked_user(
    user: User = Depends(get_current_user),  # noqa: B008
) -> User:
    """``get_current_user`` that also refuses an over-quota or unentitled user.

    For endpoints that start model work outside chat; chat runs the same
    check before each turn. An endpoint that calls a model itself takes
    :func:`get_metered_user`, which also records what the call consumes.
    """
    from fim_one.core.assembly.principal import provisional_principal
    from fim_one.core.assembly.quota import check_quota

    await check_quota(provisional_principal(user.id))
    return user


# What each model-backed helper endpoint is called in the usage ledger, by
# the name of its route function.
_ENDPOINT_ENTRIES: dict[str, str] = {
    "ai_create_agent": "ai_assist.agent_create",
    "ai_refine_agent": "ai_assist.agent_refine",
    "ai_create_connector": "ai_assist.connector_create",
    "ai_generate_actions": "ai_assist.connector_actions",
    "ai_refine_action": "ai_assist.connector_action_refine",
    "ai_annotate": "ai_assist.db_annotate",
    "db_ai_chat": "db_chat",
    "kb_ai_chat": "kb_chat",
}
# The path parameter that names what the endpoint works on.
_RESOURCE_PARAMS = ("agent_id", "connector_id", "kb_id")


def endpoint_usage_scope(request: Request, user_id: str) -> UsageScope:
    """Whose usage a helper endpoint's model calls are: the caller's own.

    Recorded as a use of the endpoint (there is no conversation or run),
    under the entry name of its route and the resource it works on.
    """
    route = request.scope.get("route")
    name = getattr(route, "name", None) or "unknown"
    params = request.path_params
    resource_id = next((str(params[key]) for key in _RESOURCE_PARAMS if params.get(key)), None)
    return UsageScope(
        user_id=user_id,
        org_id=None,
        source_type="endpoint",
        source_id=resource_id,
        entry=_ENDPOINT_ENTRIES.get(name, f"endpoint.{name}"),
        agent_id=str(params["agent_id"]) if params.get("agent_id") else None,
    )


async def get_metered_user(
    request: Request,
    user: User = Depends(get_quota_checked_user),  # noqa: B008
) -> AsyncIterator[User]:
    """:func:`get_quota_checked_user`, with the request's model calls recorded.

    For the endpoints that call a model outside chat (AI-assisted builders,
    KB and DB chat). The usage counts against the caller's quota like a
    chat turn's does, and is written to the usage ledger when the request
    ends, whether it succeeded or failed. Work the request leaves running
    in the background (annotating a large schema) reports to the same
    scope and is written as it arrives.
    """
    opened = open_meter(endpoint_usage_scope(request, user.id))
    try:
        yield user
    finally:
        await opened.close()


__all__ = [
    "endpoint_usage_scope",
    "get_metered_user",
    "get_quota_checked_user",
    "get_user_quota",
    "get_user_quota_by_id",
    "is_user_unentitled",
]

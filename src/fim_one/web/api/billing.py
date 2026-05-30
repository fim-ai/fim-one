"""User-facing Stripe billing endpoints.

Routes:
- ``GET  /api/billing/plans`` — list active plans (with live Stripe prices,
  cached 5 minutes); flags the user's current plan.
- ``GET  /api/billing/subscription`` — return the user's active subscription
  envelope (or ``None`` when on the free tier with no Stripe row).
- ``POST /api/billing/checkout`` — create a Stripe Checkout Session for the
  requested plan; returns the redirect URL.
- ``POST /api/billing/portal`` — create a Stripe Billing Portal Session and
  returns the redirect URL.

Every endpoint short-circuits to ``503`` when billing is disabled, so the
service stays bootable without Stripe credentials.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.auth import get_current_user
from fim_one.web.config import settings
from fim_one.db.models import BillingPlan, Subscription, User
from fim_one.web.schemas.billing import (
    CheckoutRequest,
    PlanInfo,
    PlansResponse,
    RedirectResponse,
    SubscriptionInfo,
)
from fim_one.web.services.billing_flag import require_billing_enabled
from fim_one.web.services.stripe_client import billing_enabled, get_stripe
from fim_one.web.services.stripe_pricing import (
    fetch_price_details as _fetch_price_details,
    format_price as _format_price,
    reset_price_cache as _reset_price_cache,
)

logger = logging.getLogger(__name__)

# Every route in this module is gated by both the runtime Stripe
# credential check (``billing_enabled()`` from ``stripe_client``) and
# the admin-controlled feature flag (``require_billing_enabled``). The
# router-level dependency makes the latter uniform — adding a new
# endpoint to this prefix automatically gets the gate.
router = APIRouter(
    prefix="/api/billing",
    tags=["billing"],
    dependencies=[Depends(require_billing_enabled)],
)

def _ensure_billing_enabled() -> None:
    """Raise 503 when Stripe credentials are not configured."""
    if not billing_enabled():
        raise HTTPException(
            status_code=503,
            detail="Billing is not configured",
        )


# ---------------------------------------------------------------------------
# GET /api/billing/plans
# ---------------------------------------------------------------------------


@router.get("/plans", response_model=PlansResponse)
async def list_plans(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> PlansResponse:
    """Return active plans with live price details and a ``current`` flag."""
    _ensure_billing_enabled()

    rows = (
        await db.execute(
            select(BillingPlan)
            .where(BillingPlan.is_active.is_(True))
            .order_by(BillingPlan.sort_order)
        )
    ).scalars().all()

    plans: list[PlanInfo] = []
    for plan in rows:
        if plan.stripe_price_id is None:
            price_display = "Free"
        else:
            details = _fetch_price_details(plan.stripe_price_id)
            price_display = _format_price(
                details.get("amount_cents"),
                details.get("currency"),
                details.get("interval"),
            )

        features_raw = plan.features_json or {}
        if isinstance(features_raw, dict):
            features = list(features_raw.get("items", []))
        elif isinstance(features_raw, list):
            features = list(features_raw)
        else:
            features = []

        plans.append(
            PlanInfo(
                slug=plan.slug,
                name=plan.name,
                monthly_token_quota=plan.monthly_token_quota,
                stripe_price_id=plan.stripe_price_id,
                price_display=price_display,
                description=plan.description,
                features=features,
                sort_order=plan.sort_order,
                current=(user.plan_id == plan.id),
            )
        )

    return PlansResponse(plans=plans)


# ---------------------------------------------------------------------------
# GET /api/billing/subscription
# ---------------------------------------------------------------------------


@router.get("/subscription", response_model=SubscriptionInfo | None)
async def get_subscription(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> SubscriptionInfo | None:
    """Return the user's current Subscription envelope, or ``None``."""
    _ensure_billing_enabled()

    sub_row = (
        await db.execute(
            select(Subscription).where(Subscription.user_id == user.id)
        )
    ).scalar_one_or_none()
    if sub_row is None:
        return None

    plan = await db.get(BillingPlan, sub_row.plan_id)
    plan_slug = plan.slug if plan is not None else "unknown"
    return SubscriptionInfo(
        plan_slug=plan_slug,
        status=sub_row.status,
        cancel_at_period_end=sub_row.cancel_at_period_end,
        current_period_start=sub_row.current_period_start,
        current_period_end=sub_row.current_period_end,
        canceled_at=sub_row.canceled_at,
        stripe_subscription_id=sub_row.stripe_subscription_id,
    )


# ---------------------------------------------------------------------------
# POST /api/billing/checkout
# ---------------------------------------------------------------------------


@router.post("/checkout", response_model=RedirectResponse)
async def create_checkout(
    body: CheckoutRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Create a Stripe Checkout Session for ``body.plan_slug``."""
    _ensure_billing_enabled()

    plan = (
        await db.execute(
            select(BillingPlan).where(BillingPlan.slug == body.plan_slug)
        )
    ).scalar_one_or_none()
    if plan is None or not plan.is_active:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown or inactive plan: {body.plan_slug}",
        )
    if not plan.stripe_price_id:
        raise HTTPException(
            status_code=400,
            detail="Plan is not purchasable",
        )

    stripe = get_stripe()

    # Lazily create the Stripe Customer the first time a user hits
    # checkout. We persist immediately so the next call (or a webhook
    # arriving before the user finishes the redirect) can find them.
    if not user.stripe_customer_id:
        customer = stripe.Customer.create(
            email=user.email,
            metadata={"user_id": user.id},
        )
        user.stripe_customer_id = customer.id
        await db.commit()
        await db.refresh(user)

    # ``STRIPE_BILLING_RETURN_URL`` may already carry a query string
    # (e.g. ``…/settings?tab=billing``), so use the right separator.
    return_url = settings.STRIPE_BILLING_RETURN_URL
    sep = "&" if "?" in return_url else "?"
    session = stripe.checkout.Session.create(
        customer=user.stripe_customer_id,
        mode="subscription",
        line_items=[{"price": plan.stripe_price_id, "quantity": 1}],
        # Include the plan slug so the success page can greet the user
        # with the plan they just subscribed to without waiting for the
        # webhook + /api/billing/subscription round-trip.
        success_url=f"{return_url}{sep}status=success&plan={plan.slug}",
        cancel_url=f"{return_url}{sep}status=cancel",
        client_reference_id=str(user.id),
        metadata={"user_id": user.id, "plan_id": plan.id},
    )
    url = getattr(session, "url", None)
    if not url:
        # Stripe should always return a URL; if it doesn't we have no
        # way to redirect the user — surface a 502 so the frontend
        # toasts a system error.
        raise HTTPException(
            status_code=502,
            detail="Stripe did not return a checkout URL",
        )
    return RedirectResponse(url=url)


# ---------------------------------------------------------------------------
# POST /api/billing/portal
# ---------------------------------------------------------------------------


@router.post("/portal", response_model=RedirectResponse)
async def create_portal(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),  # noqa: ARG001 — keeps signature uniform with checkout
) -> RedirectResponse:
    """Create a Stripe Billing Portal Session for the current user."""
    _ensure_billing_enabled()

    if not user.stripe_customer_id:
        raise HTTPException(
            status_code=400,
            detail="No Stripe customer record. Subscribe first.",
        )

    stripe = get_stripe()
    session = stripe.billing_portal.Session.create(
        customer=user.stripe_customer_id,
        return_url=settings.STRIPE_BILLING_RETURN_URL,
    )
    url = getattr(session, "url", None)
    if not url:
        raise HTTPException(
            status_code=502,
            detail="Stripe did not return a portal URL",
        )
    return RedirectResponse(url=url)


__all__ = ["router"]

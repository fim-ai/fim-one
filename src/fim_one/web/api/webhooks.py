"""Stripe webhook ingestion.

Single endpoint at ``POST /api/webhooks/stripe`` that:

1. Verifies the ``Stripe-Signature`` header against the webhook secret.
2. Idempotency-checks the event id against ``stripe_webhook_events``.
3. Dispatches to a per-event-type handler (5 events in v1).
4. Records success or the truncated error message.

Unknown event types are intentionally swallowed (200) so that Stripe
doesn't keep retrying events we don't care about.

The handlers are written defensively: missing fields on the Stripe payload
log + skip rather than 500, because a webhook 500 triggers a retry storm.
The idempotency record is what protects us from re-applying state.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from stripe import SignatureVerificationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.config import settings
from fim_one.db.models import (
    BillingPlan,
    StripeWebhookEvent,
    Subscription,
    User,
)
from fim_one.web.services.billing_flag import require_billing_enabled

logger = logging.getLogger(__name__)

# The webhook endpoint is gated on the same admin flag as the
# user-facing billing routes. Stripe will retry 503 responses for ~3
# days, so flipping the flag back on later still recovers the events
# without manual replay.
router = APIRouter(
    prefix="/api/webhooks",
    tags=["webhooks"],
    dependencies=[Depends(require_billing_enabled)],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Timezone-aware UTC now — kept as a helper for tests to monkeypatch."""
    return datetime.now(UTC)


def _construct_event(payload: bytes, sig: str, secret: str) -> Any:
    """Typed wrapper around ``stripe.Webhook.construct_event``.

    The upstream SDK signature is untyped (mypy ``no-untyped-call``);
    indirecting through ``getattr`` keeps the rest of the module
    strict-clean without an explicit ``# type: ignore``.
    """
    constructor: Any = getattr(stripe.Webhook, "construct_event")
    return constructor(payload, sig, secret)


def _from_stripe_ts(value: Any) -> datetime | None:
    """Convert a Stripe Unix-seconds timestamp into a tz-aware datetime."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError):
        return None


def _event_obj(event: Any) -> dict[str, Any]:
    """Return ``event.data.object`` as a plain ``dict``.

    The Stripe SDK exposes nested objects via attribute access, but tests
    pass plain dicts — this normalises both shapes.
    """
    data = getattr(event, "data", None) or (event.get("data") if isinstance(event, dict) else {})
    obj = getattr(data, "object", None) or (data.get("object") if isinstance(data, dict) else {})
    return dict(obj) if obj else {}


# ---------------------------------------------------------------------------
# Per-event handlers
# ---------------------------------------------------------------------------


async def _handle_checkout_completed(
    event: Any, db: AsyncSession
) -> None:
    """``checkout.session.completed`` — first subscription created.

    Provisions or refreshes the local Subscription row, attaches the
    Stripe customer to the user, and points ``User.plan_id`` at the
    purchased plan.
    """
    obj = _event_obj(event)
    user_id = obj.get("client_reference_id")
    customer_id = obj.get("customer")
    subscription_id = obj.get("subscription")

    if not user_id or not subscription_id:
        logger.warning(
            "checkout.session.completed missing user/subscription id; skipping"
        )
        return

    user = await db.get(User, user_id)
    if user is None:
        logger.warning("checkout.session.completed for unknown user %s", user_id)
        return

    if customer_id and not user.stripe_customer_id:
        user.stripe_customer_id = str(customer_id)

    # Pull the live subscription so we get authoritative period bounds
    # and the resolved price id (Checkout doesn't expand them inline).
    sub = stripe.Subscription.retrieve(subscription_id)
    items = (sub.get("items") or {}).get("data") or []
    price_id = items[0]["price"]["id"] if items else ""

    plan_row = (
        await db.execute(
            select(BillingPlan).where(BillingPlan.stripe_price_id == price_id)
        )
    ).scalar_one_or_none()

    if plan_row is None:
        logger.warning(
            "checkout.session.completed price_id=%s does not match any local plan",
            price_id,
        )
        return

    period_start = _from_stripe_ts(sub.get("current_period_start")) or _utcnow()
    period_end = _from_stripe_ts(sub.get("current_period_end")) or _utcnow()

    sub_row = (
        await db.execute(
            select(Subscription).where(
                Subscription.stripe_subscription_id == subscription_id
            )
        )
    ).scalar_one_or_none()
    if sub_row is None:
        sub_row = Subscription(
            user_id=user.id,
            plan_id=plan_row.id,
            stripe_subscription_id=str(subscription_id),
            stripe_price_id=price_id,
            status=str(sub.get("status") or "active"),
            current_period_start=period_start,
            current_period_end=period_end,
            cancel_at_period_end=bool(sub.get("cancel_at_period_end")),
            canceled_at=_from_stripe_ts(sub.get("canceled_at")),
            updated_at=_utcnow(),
        )
        db.add(sub_row)
    else:
        sub_row.plan_id = plan_row.id
        sub_row.stripe_price_id = price_id
        sub_row.status = str(sub.get("status") or sub_row.status)
        sub_row.current_period_start = period_start
        sub_row.current_period_end = period_end
        sub_row.cancel_at_period_end = bool(sub.get("cancel_at_period_end"))
        sub_row.canceled_at = _from_stripe_ts(sub.get("canceled_at"))
        sub_row.updated_at = _utcnow()

    user.plan_id = plan_row.id
    user.quota_reset_at = period_end


async def _handle_subscription_updated(
    event: Any, db: AsyncSession
) -> None:
    """``customer.subscription.updated`` — sync state on the local row.

    Detects plan changes (price id swap) and updates ``User.plan_id``
    accordingly. ``cancel_at_period_end`` flips here when the user opts
    out via the Customer Portal — but we leave the user's plan_id alone
    until period_end (handled by the lifecycle sweep).
    """
    obj = _event_obj(event)
    subscription_id = obj.get("id")
    if not subscription_id:
        return

    sub_row = (
        await db.execute(
            select(Subscription).where(
                Subscription.stripe_subscription_id == subscription_id
            )
        )
    ).scalar_one_or_none()
    if sub_row is None:
        # Edge case: webhook for a sub we never recorded (e.g. created
        # outside our checkout flow). Ignore — reconcile script can mop up.
        logger.warning(
            "customer.subscription.updated for unknown sub %s — skipping",
            subscription_id,
        )
        return

    sub_row.status = str(obj.get("status") or sub_row.status)
    period_start = _from_stripe_ts(obj.get("current_period_start"))
    period_end = _from_stripe_ts(obj.get("current_period_end"))
    if period_start is not None:
        sub_row.current_period_start = period_start
    if period_end is not None:
        sub_row.current_period_end = period_end
    sub_row.cancel_at_period_end = bool(obj.get("cancel_at_period_end"))
    sub_row.canceled_at = _from_stripe_ts(obj.get("canceled_at"))
    sub_row.updated_at = _utcnow()

    items = (obj.get("items") or {}).get("data") or []
    new_price_id = items[0]["price"]["id"] if items else ""
    if new_price_id and new_price_id != sub_row.stripe_price_id:
        sub_row.stripe_price_id = new_price_id
        plan_row = (
            await db.execute(
                select(BillingPlan).where(
                    BillingPlan.stripe_price_id == new_price_id
                )
            )
        ).scalar_one_or_none()
        if plan_row is not None:
            sub_row.plan_id = plan_row.id
            user = await db.get(User, sub_row.user_id)
            if user is not None:
                user.plan_id = plan_row.id
                if period_end is not None:
                    user.quota_reset_at = period_end


async def _handle_subscription_deleted(
    event: Any, db: AsyncSession
) -> None:
    """``customer.subscription.deleted`` — mark canceled, do not demote yet.

    The user paid for the period; we keep their entitlements until
    ``current_period_end`` and the lifecycle sweep flips them to free.
    """
    obj = _event_obj(event)
    subscription_id = obj.get("id")
    if not subscription_id:
        return

    sub_row = (
        await db.execute(
            select(Subscription).where(
                Subscription.stripe_subscription_id == subscription_id
            )
        )
    ).scalar_one_or_none()
    if sub_row is None:
        return

    sub_row.status = "canceled"
    sub_row.canceled_at = _from_stripe_ts(obj.get("canceled_at")) or _utcnow()
    period_end = _from_stripe_ts(obj.get("current_period_end"))
    if period_end is not None:
        sub_row.current_period_end = period_end
    sub_row.updated_at = _utcnow()
    # IMPORTANT: deliberately do NOT mutate user.plan_id here. The
    # lifecycle job ``downgrade_expired_canceled_subscriptions`` is the
    # single owner of that flip, and it waits for current_period_end.


async def _handle_invoice_paid(event: Any, db: AsyncSession) -> None:
    """``invoice.payment_succeeded`` — reset usage on cycle renewal.

    Only ``billing_reason == 'subscription_cycle'`` resets the counter.
    The first invoice (``subscription_create``) does NOT reset, since
    the user starts the period at zero already, and resetting on create
    risks double-reset races with the checkout handler.
    """
    obj = _event_obj(event)
    billing_reason = obj.get("billing_reason")
    subscription_id = obj.get("subscription")
    if not subscription_id:
        return

    sub_row = (
        await db.execute(
            select(Subscription).where(
                Subscription.stripe_subscription_id == subscription_id
            )
        )
    ).scalar_one_or_none()
    if sub_row is None:
        return

    if billing_reason == "subscription_cycle":
        # Pull the live subscription for the authoritative new period end.
        # The renewal invoice payload does NOT reliably carry it: the
        # top-level ``period_end`` is the invoice's usage window (not the
        # subscription cycle) and the line-item ``period.end`` is absent
        # on some API versions. Relying on the payload left
        # ``current_period_end`` frozen at the prior cycle on renewal —
        # the date never advanced even though Stripe charged and the
        # cycle rolled. Mirror the checkout handler and ask Stripe
        # directly, falling back to the payload only if the fetch fails.
        new_period_end: datetime | None = None
        try:
            sub = stripe.Subscription.retrieve(str(subscription_id))
            new_period_end = _from_stripe_ts(sub.get("current_period_end"))
        except Exception:
            logger.exception(
                "invoice.payment_succeeded: could not retrieve subscription "
                "%s for period bounds; falling back to invoice payload",
                subscription_id,
            )
        if new_period_end is None:
            new_period_end = _from_stripe_ts(
                obj.get("period_end")
                or obj.get("lines", {})
                .get("data", [{}])[0]
                .get("period", {})
                .get("end")
            )

        user = await db.get(User, sub_row.user_id)
        if user is not None:
            user.tokens_used_this_period = 0
            if new_period_end is not None:
                user.quota_reset_at = new_period_end
                sub_row.current_period_end = new_period_end
                sub_row.updated_at = _utcnow()


async def _handle_invoice_failed(event: Any, db: AsyncSession) -> None:
    """``invoice.payment_failed`` — flag the subscription as past_due.

    Frontend reads ``Subscription.status`` to render the banner that
    asks the user to update their card via the Portal.
    """
    obj = _event_obj(event)
    subscription_id = obj.get("subscription")
    if not subscription_id:
        return

    sub_row = (
        await db.execute(
            select(Subscription).where(
                Subscription.stripe_subscription_id == subscription_id
            )
        )
    ).scalar_one_or_none()
    if sub_row is None:
        return

    sub_row.status = "past_due"
    sub_row.updated_at = _utcnow()


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


_HANDLERS: dict[str, Any] = {
    "checkout.session.completed": _handle_checkout_completed,
    "customer.subscription.updated": _handle_subscription_updated,
    "customer.subscription.deleted": _handle_subscription_deleted,
    "invoice.payment_succeeded": _handle_invoice_paid,
    "invoice.payment_failed": _handle_invoice_failed,
}


async def dispatch(event: Any, db: AsyncSession) -> None:
    """Route ``event`` to the right handler.

    Unknown event types log + return cleanly so we 200 to Stripe (the
    idempotency record still gets stamped, suppressing future retries).
    """
    event_type = (
        getattr(event, "type", None)
        or (event.get("type") if isinstance(event, dict) else None)
    )
    handler = _HANDLERS.get(str(event_type) if event_type else "")
    if handler is None:
        logger.info("Ignoring unhandled Stripe event type: %s", event_type)
        return
    await handler(event, db)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Verify, dispatch, and persist a Stripe webhook event."""
    secret = settings.STRIPE_WEBHOOK_SECRET
    if secret is None:
        raise HTTPException(
            status_code=503,
            detail="Billing is not configured",
        )

    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    if not sig:
        raise HTTPException(status_code=400, detail="Missing stripe-signature")

    try:
        event = _construct_event(payload, sig, secret.get_secret_value())
    except (ValueError, SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_id = (
        getattr(event, "id", None)
        or (event.get("id") if isinstance(event, dict) else None)
    )
    event_type = (
        getattr(event, "type", None)
        or (event.get("type") if isinstance(event, dict) else None)
    )
    if not event_id:
        raise HTTPException(status_code=400, detail="Missing event id")

    existing = await db.get(StripeWebhookEvent, str(event_id))
    if existing is not None and existing.processed_at is not None:
        return {"received": True, "duplicate": True}

    record = existing or StripeWebhookEvent(
        stripe_event_id=str(event_id),
        event_type=str(event_type or "unknown"),
    )
    if existing is None:
        db.add(record)

    try:
        await dispatch(event, db)
        record.processed_at = _utcnow()
        record.error = None
        await db.commit()
    except Exception as exc:
        record.error = str(exc)[:500]
        # Persist the error stamp even when the work itself failed so an
        # operator can replay manually later — but rollback the
        # half-applied state changes.
        await db.rollback()
        await db.merge(record)
        await db.commit()
        logger.exception("Stripe webhook %s failed", event_id)
        raise

    return {"received": True}


__all__ = ["dispatch", "router"]

"""Stripe billing ORM models.

Three tables back the v1 MVP billing layer:

- :class:`BillingPlan` — catalogue of purchasable plans (Free / Pro / ...).
  Loaded at startup; admins maintain rows via the admin UI. The
  ``stripe_price_id`` column maps a local plan to the SoT Price object in
  the Stripe Dashboard.
- :class:`Subscription` — one row per active Stripe subscription on a
  user. Mirrors Stripe's lifecycle fields so we can answer "is this user
  active right now" without a round-trip to Stripe on every request.
- :class:`StripeWebhookEvent` — idempotency ledger keyed by Stripe's
  ``event.id``. Required because Stripe retries webhooks aggressively and
  we must never double-process a billing event.

All ``server_default`` values mirror the Alembic migration in
``i9d1e3f5g678_stripe_billing`` exactly. Booleans use ``sa.text("TRUE")`` /
``sa.text("FALSE")`` (PG rejects ``"0"`` / ``"1"``).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fim_one.db.base import Base

if TYPE_CHECKING:
    from .user import User


class BillingPlan(Base):
    """A purchasable plan tier (Free / Pro / ...)."""

    __tablename__ = "billing_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    stripe_price_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    monthly_token_quota: Mapped[int] = mapped_column(BigInteger, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    features_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.text("TRUE")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("(CURRENT_TIMESTAMP)"),
    )


class Subscription(Base):
    """A user's Stripe subscription state.

    One subscription per user (v1 MVP only models personal plans). The
    ``status`` column tracks Stripe's subscription lifecycle —
    ``active`` / ``past_due`` / ``canceled`` / ``incomplete`` /
    ``trialing`` — but enforcement reads from
    :attr:`current_period_end` so a canceled subscription keeps its
    paid-for window before the lifecycle job downgrades the user.
    """

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("billing_plans.id"), nullable=False, index=True
    )
    stripe_subscription_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    stripe_price_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    current_period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    current_period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.text("FALSE")
    )
    canceled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("(CURRENT_TIMESTAMP)"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    user: Mapped[User] = relationship(
        "User", back_populates="subscription", lazy="raise"
    )
    plan: Mapped[BillingPlan] = relationship("BillingPlan", lazy="joined")


class StripeWebhookEvent(Base):
    """Idempotency ledger for Stripe webhook deliveries.

    Stripe retries webhook deliveries aggressively (up to 3 days). Without a
    durable record of which ``event.id`` values have been processed, retries
    would double-apply state changes (e.g. crediting the same invoice twice).

    The webhook handler MUST insert a row keyed by ``stripe_event_id`` before
    dispatching the event, and set :attr:`processed_at` only on success.
    Failures persist :attr:`error` so ops can replay manually if needed.
    """

    __tablename__ = "stripe_webhook_events"

    stripe_event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("(CURRENT_TIMESTAMP)"),
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

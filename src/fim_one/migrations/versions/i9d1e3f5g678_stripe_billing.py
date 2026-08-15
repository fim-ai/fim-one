"""Stripe billing data model: users billing fields + 3 new tables.

Revision ID: i9d1e3f5g678
Revises: h8c0d2e4f567
Create Date: 2026-05-07 14:50:00.000000

Adds the v1 MVP Stripe billing groundwork:

- ``users``: ``stripe_customer_id``, ``tokens_used_this_period``,
  ``quota_reset_at``, ``plan_id`` (FK → ``billing_plans.id``)
- ``billing_plans``: catalogue of purchasable plans (Free / Pro)
- ``subscriptions``: per-user Stripe subscription state
- ``stripe_webhook_events``: idempotency record for Stripe webhooks

Seeds two plans: ``free`` (no Stripe price) and ``pro`` (test-mode price id).
The Pro ``stripe_price_id`` here points at the **test mode** Price; production
deploys must replace it with the live ``price_xxx`` after creating the live
Product/Price in the Stripe Dashboard.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import index_exists, table_exists, table_has_column

# revision identifiers, used by Alembic.
revision: str = "i9d1e3f5g678"
down_revision: Union[str, None] = "h8c0d2e4f567"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_default_clause(dialect_name: str) -> sa.sql.elements.TextClause:
    """Return a dialect-aware ``server_default`` for an empty JSON object.

    SQLite stores JSON as TEXT and accepts a quoted string literal, while
    PostgreSQL's ``json``/``jsonb`` requires a cast.  Mirrors the dual-track
    pattern used elsewhere in the migration suite.
    """
    if dialect_name == "postgresql":
        return sa.text("'{}'::json")
    return sa.text("'{}'")


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # ── billing_plans ─────────────────────────────────────────────────────
    if not table_exists(bind, "billing_plans"):
        op.create_table(
            "billing_plans",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("slug", sa.String(32), nullable=False),
            sa.Column("name", sa.String(64), nullable=False),
            sa.Column("stripe_price_id", sa.String(64), nullable=True),
            sa.Column("monthly_token_quota", sa.BigInteger(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column(
                "features_json",
                sa.JSON(),
                nullable=False,
                server_default=_json_default_clause(dialect),
            ),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("TRUE"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
            ),
            sa.UniqueConstraint("slug", name="uq_billing_plans_slug"),
            sa.UniqueConstraint("stripe_price_id", name="uq_billing_plans_stripe_price_id"),
        )

    if not index_exists(bind, "billing_plans", "ix_billing_plans_is_active"):
        op.create_index("ix_billing_plans_is_active", "billing_plans", ["is_active"])

    # ── subscriptions ────────────────────────────────────────────────────
    if not table_exists(bind, "subscriptions"):
        op.create_table(
            "subscriptions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "plan_id",
                sa.Integer(),
                sa.ForeignKey("billing_plans.id"),
                nullable=False,
            ),
            sa.Column("stripe_subscription_id", sa.String(64), nullable=False),
            sa.Column("stripe_price_id", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "cancel_at_period_end",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("FALSE"),
            ),
            sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "stripe_subscription_id",
                name="uq_subscriptions_stripe_subscription_id",
            ),
        )

    if not index_exists(bind, "subscriptions", "ix_subscriptions_user_id"):
        op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])
    if not index_exists(bind, "subscriptions", "ix_subscriptions_plan_id"):
        op.create_index("ix_subscriptions_plan_id", "subscriptions", ["plan_id"])
    if not index_exists(bind, "subscriptions", "ix_subscriptions_status"):
        op.create_index("ix_subscriptions_status", "subscriptions", ["status"])

    # ── stripe_webhook_events ────────────────────────────────────────────
    if not table_exists(bind, "stripe_webhook_events"):
        op.create_table(
            "stripe_webhook_events",
            sa.Column("stripe_event_id", sa.String(64), primary_key=True),
            sa.Column("event_type", sa.String(64), nullable=False),
            sa.Column(
                "received_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
            ),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
        )

    if not index_exists(bind, "stripe_webhook_events", "ix_stripe_webhook_events_event_type"):
        op.create_index(
            "ix_stripe_webhook_events_event_type",
            "stripe_webhook_events",
            ["event_type"],
        )

    # ── users billing fields ─────────────────────────────────────────────
    # SQLite cannot ALTER COLUMN, so use batch_alter_table for everything.
    if table_exists(bind, "users"):
        with op.batch_alter_table("users") as batch_op:
            if not table_has_column(bind, "users", "stripe_customer_id"):
                batch_op.add_column(
                    sa.Column("stripe_customer_id", sa.String(64), nullable=True)
                )
            if not table_has_column(bind, "users", "tokens_used_this_period"):
                batch_op.add_column(
                    sa.Column(
                        "tokens_used_this_period",
                        sa.BigInteger(),
                        nullable=False,
                        server_default="0",
                    )
                )
            if not table_has_column(bind, "users", "quota_reset_at"):
                batch_op.add_column(
                    sa.Column("quota_reset_at", sa.DateTime(timezone=True), nullable=True)
                )
            if not table_has_column(bind, "users", "plan_id"):
                batch_op.add_column(sa.Column("plan_id", sa.Integer(), nullable=True))

        if not index_exists(bind, "users", "ix_users_stripe_customer_id"):
            op.create_index(
                "ix_users_stripe_customer_id",
                "users",
                ["stripe_customer_id"],
                unique=True,
            )
        if not index_exists(bind, "users", "ix_users_plan_id"):
            op.create_index("ix_users_plan_id", "users", ["plan_id"])

    # ── Seed default plans ───────────────────────────────────────────────
    # Skip seeding if rows already exist — keeps the migration idempotent
    # for prod boxes that ran a prior partial upgrade.
    existing = bind.execute(
        sa.text("SELECT COUNT(*) FROM billing_plans WHERE slug IN ('free', 'pro')")
    ).scalar()
    if not existing:
        billing_plans_table = sa.table(
            "billing_plans",
            sa.column("slug", sa.String),
            sa.column("name", sa.String),
            sa.column("stripe_price_id", sa.String),
            sa.column("monthly_token_quota", sa.BigInteger),
            sa.column("description", sa.Text),
            sa.column("sort_order", sa.Integer),
            sa.column("is_active", sa.Boolean),
        )
        op.bulk_insert(
            billing_plans_table,
            [
                {
                    "slug": "free",
                    "name": "Free",
                    "stripe_price_id": None,
                    "monthly_token_quota": 0,
                    # Token count intentionally omitted — the UI reads
                    # it from ``monthly_token_quota`` and any literal
                    # here would drift the moment an admin edited
                    # ``default_token_quota``.
                    "description": "Basic features",
                    "sort_order": 0,
                    "is_active": True,
                },
                {
                    "slug": "pro",
                    "name": "Pro",
                    "stripe_price_id": None,
                    "monthly_token_quota": 5_000_000,
                    "description": "Priority support",
                    "sort_order": 1,
                    "is_active": True,
                },
            ],
        )


def downgrade() -> None:
    bind = op.get_bind()

    if table_exists(bind, "users"):
        if index_exists(bind, "users", "ix_users_plan_id"):
            op.drop_index("ix_users_plan_id", table_name="users")
        if index_exists(bind, "users", "ix_users_stripe_customer_id"):
            op.drop_index("ix_users_stripe_customer_id", table_name="users")

        with op.batch_alter_table("users") as batch_op:
            if table_has_column(bind, "users", "plan_id"):
                batch_op.drop_column("plan_id")
            if table_has_column(bind, "users", "quota_reset_at"):
                batch_op.drop_column("quota_reset_at")
            if table_has_column(bind, "users", "tokens_used_this_period"):
                batch_op.drop_column("tokens_used_this_period")
            if table_has_column(bind, "users", "stripe_customer_id"):
                batch_op.drop_column("stripe_customer_id")

    if table_exists(bind, "stripe_webhook_events"):
        op.drop_table("stripe_webhook_events")
    if table_exists(bind, "subscriptions"):
        op.drop_table("subscriptions")
    if table_exists(bind, "billing_plans"):
        op.drop_table("billing_plans")

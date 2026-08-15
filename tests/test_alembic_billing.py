"""Tests for the Stripe billing Alembic migration (``i9d1e3f5g678``).

Validates that ``upgrade()`` and ``downgrade()`` both run cleanly against
a fresh in-memory SQLite database, that all four schema artefacts
(``users`` columns + 3 new tables) are present after upgrade, and that
the seed rows are inserted exactly once (idempotent on replay).

The migration uses ``op.batch_alter_table`` for SQLite ALTER, plus the
``table_exists`` / ``index_exists`` helpers that gate every DDL call.
"""

from __future__ import annotations

import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

# We import the migration module by file path so this test does not
# depend on the (long, monolithic) project Alembic graph.
MIGRATION_MODULE = "fim_one.migrations.versions.i9d1e3f5g678_stripe_billing"


@pytest.fixture()
def synchronous_engine() -> sa.Engine:
    """Sync SQLite engine — Alembic operations are sync, not async.

    We cannot reuse the async fixture from ``test_billing_models.py``
    because ``op.batch_alter_table`` and friends require a sync
    Connection bound to ``alembic.operations.Operations``.
    """
    return create_engine("sqlite:///:memory:", future=True)


def _bootstrap_users_table(engine: sa.Engine) -> None:
    """Create a minimal ``users`` table so the migration's ALTER works.

    The real ``users`` table has ~25 columns; we only need the PK plus a
    UNIQUE-able column so SQLite accepts the table during batch ALTER.
    """
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE users ("
                "  id VARCHAR(36) PRIMARY KEY,"
                "  email VARCHAR(255) NOT NULL,"
                "  is_admin BOOLEAN NOT NULL DEFAULT 0,"
                "  is_active BOOLEAN NOT NULL DEFAULT 1"
                ")"
            )
        )


def _run_upgrade(engine: sa.Engine) -> None:
    module = importlib.import_module(MIGRATION_MODULE)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            module.upgrade()
        conn.commit()


def _run_downgrade(engine: sa.Engine) -> None:
    module = importlib.import_module(MIGRATION_MODULE)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            module.downgrade()
        conn.commit()


# ---------------------------------------------------------------------------
# upgrade()
# ---------------------------------------------------------------------------


class TestUpgrade:
    """Forward migration creates all schema objects and seeds plans."""

    def test_creates_three_new_tables(self, synchronous_engine: sa.Engine) -> None:
        _bootstrap_users_table(synchronous_engine)
        _run_upgrade(synchronous_engine)

        tables = set(inspect(synchronous_engine).get_table_names())
        assert "billing_plans" in tables
        assert "subscriptions" in tables
        assert "stripe_webhook_events" in tables

    def test_adds_users_billing_columns(
        self, synchronous_engine: sa.Engine
    ) -> None:
        _bootstrap_users_table(synchronous_engine)
        _run_upgrade(synchronous_engine)

        cols = {c["name"] for c in inspect(synchronous_engine).get_columns("users")}
        assert "stripe_customer_id" in cols
        assert "tokens_used_this_period" in cols
        assert "quota_reset_at" in cols
        assert "plan_id" in cols

    def test_seeds_two_default_plans(
        self, synchronous_engine: sa.Engine
    ) -> None:
        _bootstrap_users_table(synchronous_engine)
        _run_upgrade(synchronous_engine)

        with synchronous_engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT slug, name, monthly_token_quota, stripe_price_id "
                    "FROM billing_plans ORDER BY sort_order"
                )
            ).fetchall()

        assert len(rows) == 2
        assert rows[0][0] == "free"
        assert rows[0][2] == 0
        assert rows[0][3] is None
        assert rows[1][0] == "pro"
        assert rows[1][2] == 5_000_000
        assert rows[1][3] is None

    def test_idempotent_on_replay(
        self, synchronous_engine: sa.Engine
    ) -> None:
        """Running upgrade() twice must not double-seed or raise.

        Migrations get replayed in production whenever the alembic_version
        row is missing (e.g. first deploy of an old SQLite db). Every DDL
        call in our migration is guarded by ``table_exists`` /
        ``table_has_column`` / ``index_exists``.
        """
        _bootstrap_users_table(synchronous_engine)
        _run_upgrade(synchronous_engine)
        _run_upgrade(synchronous_engine)  # should be a no-op

        with synchronous_engine.connect() as conn:
            count = conn.execute(
                sa.text("SELECT COUNT(*) FROM billing_plans")
            ).scalar()
        assert count == 2

    def test_indexes_created(self, synchronous_engine: sa.Engine) -> None:
        _bootstrap_users_table(synchronous_engine)
        _run_upgrade(synchronous_engine)

        insp = inspect(synchronous_engine)
        sub_idx = {idx["name"] for idx in insp.get_indexes("subscriptions")}
        assert "ix_subscriptions_user_id" in sub_idx
        assert "ix_subscriptions_status" in sub_idx

        evt_idx = {idx["name"] for idx in insp.get_indexes("stripe_webhook_events")}
        assert "ix_stripe_webhook_events_event_type" in evt_idx


# ---------------------------------------------------------------------------
# downgrade()
# ---------------------------------------------------------------------------


class TestDowngrade:
    """Reverse migration drops everything cleanly."""

    def test_downgrade_removes_all_artefacts(
        self, synchronous_engine: sa.Engine
    ) -> None:
        _bootstrap_users_table(synchronous_engine)
        _run_upgrade(synchronous_engine)
        _run_downgrade(synchronous_engine)

        tables = set(inspect(synchronous_engine).get_table_names())
        assert "billing_plans" not in tables
        assert "subscriptions" not in tables
        assert "stripe_webhook_events" not in tables

        cols = {c["name"] for c in inspect(synchronous_engine).get_columns("users")}
        assert "stripe_customer_id" not in cols
        assert "tokens_used_this_period" not in cols
        assert "quota_reset_at" not in cols
        assert "plan_id" not in cols
        # Original users columns survive.
        assert "id" in cols
        assert "email" in cols

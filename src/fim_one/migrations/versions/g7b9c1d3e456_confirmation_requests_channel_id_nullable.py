"""Make confirmation_requests.channel_id nullable for inline-mode requests.

Phase 1 follow-up: when ``mode='inline'`` the request is surfaced in the
frontend (SSE) rather than pushed to a messaging channel, so
``channel_id`` has no natural value.  The previous NOT NULL constraint
forced the gate hook to fabricate a dummy channel; this migration
relaxes it.

Revision ID: g7b9c1d3e456
Revises: f6a8b0c2d345
Create Date: 2026-04-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "g7b9c1d3e456"
down_revision = "f6a8b0c2d345"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    from fim_one.migrations.helpers import table_exists, table_has_column

    if not table_exists(bind, "confirmation_requests"):
        return
    if not table_has_column(bind, "confirmation_requests", "channel_id"):
        return

    # SQLite cannot ALTER COLUMN directly — fall through to batch mode.
    with op.batch_alter_table("confirmation_requests") as batch_op:
        batch_op.alter_column(
            "channel_id",
            existing_type=sa.String(36),
            nullable=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    from fim_one.migrations.helpers import table_exists, table_has_column

    if not table_exists(bind, "confirmation_requests"):
        return
    if not table_has_column(bind, "confirmation_requests", "channel_id"):
        return

    # Backfill any NULLs before re-imposing NOT NULL.  Orphan-safe: the
    # rollback can only succeed if all inline requests have been purged
    # or assigned a channel.  We leave that to ops.
    with op.batch_alter_table("confirmation_requests") as batch_op:
        batch_op.alter_column(
            "channel_id",
            existing_type=sa.String(36),
            nullable=False,
        )

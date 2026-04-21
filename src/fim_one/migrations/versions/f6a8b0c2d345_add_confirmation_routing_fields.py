"""Add per-agent confirmation routing fields and per-request mode/approver.

Phase 1 of the human-in-the-loop approval redesign.

Adds to ``agents``:
  - ``confirmation_mode``             auto | inline_only | channel_only
  - ``confirmation_approver_scope``   initiator | agent_owner | org_members
  - ``require_confirmation_for_all``  Boolean
  - ``approval_channel_id``           FK -> channels.id (SET NULL on delete)

Adds to ``confirmation_requests``:
  - ``mode``              inline | channel   (backfill -> 'channel')
  - ``approver_user_id``  FK -> users.id (SET NULL on delete)

Revision ID: f6a8b0c2d345
Revises: e5f7g9h1i234
Create Date: 2026-04-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f6a8b0c2d345"
down_revision = "e5f7g9h1i234"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    from fim_one.migrations.helpers import table_exists, table_has_column

    # --- agents -------------------------------------------------------------
    if table_exists(bind, "agents"):
        if not table_has_column(bind, "agents", "confirmation_mode"):
            op.add_column(
                "agents",
                sa.Column(
                    "confirmation_mode",
                    sa.String(20),
                    nullable=False,
                    server_default=sa.text("'auto'"),
                ),
            )
        if not table_has_column(bind, "agents", "confirmation_approver_scope"):
            op.add_column(
                "agents",
                sa.Column(
                    "confirmation_approver_scope",
                    sa.String(20),
                    nullable=False,
                    server_default=sa.text("'initiator'"),
                ),
            )
        if not table_has_column(bind, "agents", "require_confirmation_for_all"):
            op.add_column(
                "agents",
                sa.Column(
                    "require_confirmation_for_all",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("FALSE"),
                ),
            )
        if not table_has_column(bind, "agents", "approval_channel_id"):
            # FK constraint declared on the ORM model; add_column here only
            # creates the column. SQLite rejects inline FK on ALTER TABLE
            # (NotImplementedError in alembic's sqlite dialect).
            op.add_column(
                "agents",
                sa.Column("approval_channel_id", sa.String(36), nullable=True),
            )
            if bind.dialect.name == "postgresql":
                op.create_foreign_key(
                    "fk_agents_approval_channel_id_channels",
                    "agents",
                    "channels",
                    ["approval_channel_id"],
                    ["id"],
                    ondelete="SET NULL",
                )

    # --- confirmation_requests ---------------------------------------------
    if table_exists(bind, "confirmation_requests"):
        if not table_has_column(bind, "confirmation_requests", "mode"):
            op.add_column(
                "confirmation_requests",
                sa.Column(
                    "mode",
                    sa.String(20),
                    nullable=False,
                    server_default=sa.text("'channel'"),
                ),
            )
            # Belt-and-suspenders: explicitly backfill any rows that may have
            # slipped through (all existing rows predate inline mode and are
            # Feishu channel pushes).
            op.execute(
                "UPDATE confirmation_requests SET mode = 'channel' WHERE mode IS NULL"
            )
        if not table_has_column(
            bind, "confirmation_requests", "approver_user_id"
        ):
            op.add_column(
                "confirmation_requests",
                sa.Column("approver_user_id", sa.String(36), nullable=True),
            )
            if bind.dialect.name == "postgresql":
                op.create_foreign_key(
                    "fk_confirmation_requests_approver_user_id_users",
                    "confirmation_requests",
                    "users",
                    ["approver_user_id"],
                    ["id"],
                    ondelete="SET NULL",
                )


def downgrade() -> None:
    bind = op.get_bind()
    from fim_one.migrations.helpers import table_exists, table_has_column

    if table_exists(bind, "confirmation_requests"):
        if table_has_column(bind, "confirmation_requests", "approver_user_id"):
            with op.batch_alter_table("confirmation_requests") as batch_op:
                batch_op.drop_column("approver_user_id")
        if table_has_column(bind, "confirmation_requests", "mode"):
            with op.batch_alter_table("confirmation_requests") as batch_op:
                batch_op.drop_column("mode")

    if table_exists(bind, "agents"):
        if table_has_column(bind, "agents", "approval_channel_id"):
            with op.batch_alter_table("agents") as batch_op:
                batch_op.drop_column("approval_channel_id")
        if table_has_column(bind, "agents", "require_confirmation_for_all"):
            with op.batch_alter_table("agents") as batch_op:
                batch_op.drop_column("require_confirmation_for_all")
        if table_has_column(bind, "agents", "confirmation_approver_scope"):
            with op.batch_alter_table("agents") as batch_op:
                batch_op.drop_column("confirmation_approver_scope")
        if table_has_column(bind, "agents", "confirmation_mode"):
            with op.batch_alter_table("agents") as batch_op:
                batch_op.drop_column("confirmation_mode")

"""add personal settings columns and notification_preferences table

Revision ID: a5b7c9d1e234
Revises: q7r8s9t0u123
Create Date: 2026-03-15
"""

from __future__ import annotations

from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import table_exists, table_has_column

revision: str = "a5b7c9d1e234"
down_revision: Union[str, None] = "q7r8s9t0u123"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # -- Add new columns to users table ------------------------------------
    new_user_columns: list[tuple[str, sa.Column[Any]]] = [
        ("timezone", sa.Column("timezone", sa.String(50), nullable=True)),
        ("default_agent_id", sa.Column("default_agent_id", sa.String(36), nullable=True)),
        ("default_exec_mode", sa.Column("default_exec_mode", sa.String(10), nullable=True)),
        ("default_reasoning", sa.Column("default_reasoning", sa.Boolean, nullable=True)),
        ("totp_secret", sa.Column("totp_secret", sa.String(255), nullable=True)),
        (
            "totp_enabled",
            sa.Column(
                "totp_enabled",
                sa.Boolean,
                nullable=False,
                server_default=sa.text("FALSE"),
            ),
        ),
        ("totp_backup_codes", sa.Column("totp_backup_codes", sa.Text, nullable=True)),
    ]

    for col_name, col_def in new_user_columns:
        if not table_has_column(bind, "users", col_name):
            with op.batch_alter_table("users") as batch_op:
                batch_op.add_column(col_def)

    # -- Create notification_preferences table -----------------------------
    if not table_exists(bind, "notification_preferences"):
        op.create_table(
            "notification_preferences",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("event_type", sa.String(50), nullable=False),
            sa.Column("channel", sa.String(20), nullable=False),
            sa.Column(
                "enabled",
                sa.Boolean,
                nullable=False,
                server_default=sa.text("TRUE"),
            ),
            sa.Column("config", sa.Text, nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=True,
            ),
        )


def downgrade() -> None:
    op.drop_table("notification_preferences")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("totp_backup_codes")
        batch_op.drop_column("totp_enabled")
        batch_op.drop_column("totp_secret")
        batch_op.drop_column("default_reasoning")
        batch_op.drop_column("default_exec_mode")
        batch_op.drop_column("default_agent_id")
        batch_op.drop_column("timezone")

"""add missing columns to users and agents tables

Columns that existed in ORM models but had no migration —
previously auto-created by metadata.create_all() on SQLite.

Revision ID: k1m3o5q7s890
Revises: j0l2n4p6r789
Create Date: 2026-03-10 16:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "k1m3o5q7s890"
down_revision: Union[str, None] = "j0l2n4p6r789"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- users table: 7 missing columns ---
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")))
        batch_op.add_column(sa.Column("tokens_invalidated_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("token_quota", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("oauth_provider", sa.String(20), nullable=True))
        batch_op.add_column(sa.Column("oauth_id", sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("email", sa.String(255), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("avatar", sa.String(255), nullable=True))

        # password_hash was NOT NULL in initial migration but model is nullable (OAuth users)
        batch_op.alter_column("password_hash", existing_type=sa.String(255), nullable=True)

    # unique constraint for OAuth binding lookup
    op.create_unique_constraint("uq_user_oauth", "users", ["oauth_provider", "oauth_id"])

    # --- agents table: 2 missing columns ---
    with op.batch_alter_table("agents") as batch_op:
        batch_op.add_column(sa.Column("icon", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("connector_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.drop_column("connector_ids")
        batch_op.drop_column("icon")

    op.drop_constraint("uq_user_oauth", "users", type_="unique")

    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("password_hash", existing_type=sa.String(255), nullable=False)
        batch_op.drop_column("avatar")
        batch_op.drop_column("email")
        batch_op.drop_column("oauth_id")
        batch_op.drop_column("oauth_provider")
        batch_op.drop_column("token_quota")
        batch_op.drop_column("tokens_invalidated_at")
        batch_op.drop_column("is_active")

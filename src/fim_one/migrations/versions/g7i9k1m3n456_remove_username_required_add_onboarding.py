"""remove username required, add onboarding_completed

Revision ID: g7i9k1m3n456
Revises: f6h8j0k2l345
Create Date: 2026-03-07 12:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from fim_one.migrations.helpers import table_has_column


# revision identifiers, used by Alembic.
revision: str = "g7i9k1m3n456"
down_revision: Union[str, None] = "f6h8j0k2l345"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_is_nullable(bind: sa.Connection | sa.Engine, table: str, column: str) -> bool:
    """Return True if *column* on *table* is already nullable."""
    for col in sa.inspect(bind).get_columns(table):
        if col["name"] == column:
            return bool(col.get("nullable", False))
    return False


def upgrade() -> None:
    bind = op.get_bind()
    # Make username nullable (skip if already nullable)
    if not _column_is_nullable(bind, "users", "username"):
        with op.batch_alter_table("users") as batch_op:
            batch_op.alter_column("username", existing_type=sa.String(50), nullable=True)
    # Add onboarding_completed column
    if not table_has_column(bind, "users", "onboarding_completed"):
        op.add_column(
            "users",
            sa.Column("onboarding_completed", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        )


def downgrade() -> None:
    op.drop_column("users", "onboarding_completed")
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("username", existing_type=sa.String(50), nullable=False)

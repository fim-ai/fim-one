"""add global agent and clone traceability fields

Revision ID: i9k1m3o5q678
Revises: h8j0l2n4p567
Create Date: 2026-03-10 10:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from fim_one.migrations.helpers import table_has_column


# revision identifiers, used by Alembic.
revision: str = "i9k1m3o5q678"
down_revision: Union[str, None] = "44221ea46a6e"
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

    # --- agents table ---
    # Make user_id nullable (skip if already nullable)
    if not _column_is_nullable(bind, "agents", "user_id"):
        with op.batch_alter_table("agents") as batch_op:
            batch_op.alter_column("user_id", existing_type=sa.String(36), nullable=True)

    if not table_has_column(bind, "agents", "is_global"):
        op.add_column("agents", sa.Column("is_global", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")))
    if not table_has_column(bind, "agents", "cloned_from_agent_id"):
        op.add_column("agents", sa.Column("cloned_from_agent_id", sa.String(36), nullable=True))
    if not table_has_column(bind, "agents", "cloned_from_user_id"):
        op.add_column("agents", sa.Column("cloned_from_user_id", sa.String(36), nullable=True))

    # mcp_servers.cloned_from_* columns are created in
    # a1d2e3f4g567_create_missing_tables (table didn't exist before).


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.drop_column("cloned_from_user_id")
        batch_op.drop_column("cloned_from_agent_id")
        batch_op.drop_column("is_global")
        batch_op.alter_column("user_id", existing_type=sa.String(36), nullable=False)

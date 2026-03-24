"""add forked_from to mcp_servers and skills

Revision ID: a1b2c3d4f890
Revises: p4q5r6s7t890
Create Date: 2026-03-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import table_has_column

revision: str = "a1b2c3d4f890"
down_revision: Union[str, None] = "p4q5r6s7t890"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    if not table_has_column(bind, "mcp_servers", "forked_from"):
        with op.batch_alter_table("mcp_servers") as batch_op:
            batch_op.add_column(
                sa.Column("forked_from", sa.String(36), nullable=True)
            )

    if not table_has_column(bind, "skills", "forked_from"):
        with op.batch_alter_table("skills") as batch_op:
            batch_op.add_column(
                sa.Column("forked_from", sa.String(36), nullable=True)
            )


def downgrade() -> None:
    with op.batch_alter_table("skills") as batch_op:
        batch_op.drop_column("forked_from")

    with op.batch_alter_table("mcp_servers") as batch_op:
        batch_op.drop_column("forked_from")

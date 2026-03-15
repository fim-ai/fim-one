"""Drop agent sub-agent fields (discoverable, sub_agent_ids, allow_as_sub_agent).

These fields are replaced by a Skills-based orchestration approach.

Revision ID: r8s9t0u1v234
Revises: a5b7c9d1e234
Create Date: 2026-03-16
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import table_exists, table_has_column

revision: str = "r8s9t0u1v234"
down_revision: Union[str, None] = "a5b7c9d1e234"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    if not table_exists(bind, "agents"):
        return

    cols_to_drop = []
    for col in ("allow_as_sub_agent", "discoverable", "sub_agent_ids"):
        if table_has_column(bind, "agents", col):
            cols_to_drop.append(col)

    if cols_to_drop:
        with op.batch_alter_table("agents") as batch_op:
            for col in cols_to_drop:
                batch_op.drop_column(col)


def downgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("discoverable", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
    )
    op.add_column(
        "agents",
        sa.Column("sub_agent_ids", sa.JSON(), nullable=True),
    )
    op.add_column(
        "agents",
        sa.Column("allow_as_sub_agent", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
    )

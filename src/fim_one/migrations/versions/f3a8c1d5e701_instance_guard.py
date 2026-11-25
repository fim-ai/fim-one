"""One row that serializes creation of the first account.

``instance_guard`` holds a single row. Creating the first administrator
updates that row, so a second request waits and then sees the user the
first one committed. Safe to run again. Downgrade drops the table.

Revision ID: f3a8c1d5e701
Revises: c2p5a1b2c3d4
Create Date: 2026-11-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import table_exists

revision: str = "f3a8c1d5e701"
down_revision: str | None = "c2p5a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "instance_guard"


def upgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, _TABLE):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("nonce", sa.Integer(), nullable=False, server_default="0"),
        )
    op.execute(
        sa.text(
            "INSERT INTO instance_guard (id, nonce) "
            "SELECT 1, 0 WHERE NOT EXISTS (SELECT 1 FROM instance_guard WHERE id = 1)"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if table_exists(bind, _TABLE):
        op.drop_table(_TABLE)

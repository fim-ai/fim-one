"""Users: a session generation that invalidation increments.

Access and refresh tokens carry the value. Signing out, changing a password,
or an administrator forcing sign-out increments it, so a token issued in the
same second no longer matches. Existing rows start at 0, which is what tokens
issued before this column existed are treated as. Safe to run again.
Downgrade drops the column; those sessions are no longer distinguished by it.

Revision ID: g4c0e2f7a813
Revises: f3a8c1d5e701
Create Date: 2026-11-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import table_exists, table_has_column

revision: str = "g4c0e2f7a813"
down_revision: str | None = "f3a8c1d5e701"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "users"
_COLUMN = "session_generation"


def upgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, _TABLE) or table_has_column(bind, _TABLE, _COLUMN):
        return
    op.add_column(
        _TABLE,
        sa.Column(_COLUMN, sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, _TABLE) or not table_has_column(bind, _TABLE, _COLUMN):
        return
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column(_COLUMN)

"""Address claim: suspended workflow access and the account's notice

The first emailed code accepted for an unverified address switches off
what the account had set up before. Two nullable columns let that be
undone instead of losing what was there:

- ``workflows.access_suspended_at``: while set, the workflow's completion
  webhook is not called and its trigger key is refused; the URL and the
  key hash are kept. The owner clears it to turn both back on.
- ``users.security_notice``: what was switched off and how to turn it back
  on, shown to the account holder until dismissed; it also keeps the
  personal system instructions and default agent so they can be restored.

Safe to run again. Downgrade drops both columns; workflows that were
suspended then call their webhook and accept their key again.

Revision ID: c2p5a1b2c3d4
Revises: e4b1a2c3d4e5
Create Date: 2026-11-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import table_exists, table_has_column

revision: str = "c2p5a1b2c3d4"
down_revision: str | None = "e4b1a2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    ("workflows", sa.Column("access_suspended_at", sa.DateTime(timezone=True), nullable=True)),
    ("users", sa.Column("security_notice", sa.JSON(), nullable=True)),
)


def upgrade() -> None:
    bind = op.get_bind()
    for table, column in _COLUMNS:
        if table_exists(bind, table) and not table_has_column(bind, table, column.name):
            op.add_column(table, column.copy())


def downgrade() -> None:
    bind = op.get_bind()
    for table, column in _COLUMNS:
        if table_exists(bind, table) and table_has_column(bind, table, column.name):
            with op.batch_alter_table(table) as batch:
                batch.drop_column(column.name)

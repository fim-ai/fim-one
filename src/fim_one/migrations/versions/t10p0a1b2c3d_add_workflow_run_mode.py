"""Record which entry point started a workflow run

``workflow_runs.mode`` names the entry point that created the run:
``interactive``, ``batch``, ``trigger``, ``schedule``, ``tool`` or
``test_node``. Single-node test executions now record their token usage as a
``test_node`` run so it counts toward the runner's quota; run history and run
statistics leave those rows out.

Existing rows stay NULL, which every reader treats as an ordinary run.

Revision ID: t10p0a1b2c3d
Revises: k2m4o6q8s012
Create Date: 2026-11-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import table_exists, table_has_column

revision: str = "t10p0a1b2c3d"
down_revision: str | None = "k2m4o6q8s012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "workflow_runs"
_COLUMN = "mode"


def upgrade() -> None:
    bind = op.get_bind()
    if table_exists(bind, _TABLE) and not table_has_column(bind, _TABLE, _COLUMN):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(20), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if table_exists(bind, _TABLE) and table_has_column(bind, _TABLE, _COLUMN):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)

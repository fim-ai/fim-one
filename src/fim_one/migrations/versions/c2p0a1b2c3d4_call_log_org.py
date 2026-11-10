"""Call log: which organization's resource was called

``connector_call_logs.org_id`` is the organization the call was made
through: the organization of the agent or workflow that used the resource
(``CallContext.binding.org_id``). The resource, the agent and the workflow
can all be deleted while the record stays, so the organization is written
on the record itself; it is what lets an organization's administrators be
shown the calls made through their organization's resources.

A plain id, not a foreign key, like every other id on the table. Rows
written before the column existed leave it empty. Indexed, because the
readers ask by organization.

Downgrade drops the index and the column.

Revision ID: c2p0a1b2c3d4
Revises: t10p5a1b2c3d4
Create Date: 2026-11-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import index_exists, table_exists, table_has_column

revision: str = "c2p0a1b2c3d4"
down_revision: str | None = "t10p5a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "connector_call_logs"
_COLUMN = "org_id"
_INDEX = "ix_connector_call_logs_org_id"


def upgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, _TABLE):
        return
    if not table_has_column(bind, _TABLE, _COLUMN):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(36), nullable=True))
    if not index_exists(bind, _TABLE, _INDEX):
        op.create_index(_INDEX, _TABLE, [_COLUMN])


def downgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, _TABLE):
        return
    if index_exists(bind, _TABLE, _INDEX):
        op.drop_index(_INDEX, table_name=_TABLE)
    if table_has_column(bind, _TABLE, _COLUMN):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)

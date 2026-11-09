"""Confirmation requests: who may decide is written on the row

``confirmation_requests`` recorded the question and the answer but not who
was allowed to answer. That was worked out at decision time from the
agent's current settings, so a request with no agent could not be decided
at all, and workflow approvals kept the same facts in a second table
(``workflow_approvals``) with rules of their own.

New columns, all nullable, none a foreign key:

- ``eligible_scope`` / ``eligible_user_id``: who may decide, fixed when the
  request is created (``initiator``, ``agent_owner``, ``org_members`` or
  ``assignee``). Rows written earlier leave both empty and are decided by
  the earlier rule.
- ``decided_by_user_id`` / ``decision_note``: who decided, and their note.
- ``expires_at``: when the request stops being answerable.
- ``conversation_id``, ``workflow_run_id`` (indexed), ``node_id``: where the
  request came from.
- ``title`` / ``description``: what an approver is shown for a workflow
  approval.

Additive: code from before this revision keeps working on the new shape,
so rolling the code back does not need a downgrade. ``workflow_approvals``
is left as it is; nothing writes to it any more.

Revision ID: c1p3a1b2c3d4
Revises: c1p2a1b2c3d4
Create Date: 2026-11-10
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import index_exists, table_exists, table_has_column

revision: str = "c1p3a1b2c3d4"
down_revision: str | None = "c1p2a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "confirmation_requests"
_RUN_INDEX = "ix_confirmation_requests_workflow_run_id"


def _new_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("eligible_scope", sa.String(20), nullable=True),
        sa.Column("eligible_user_id", sa.String(36), nullable=True),
        sa.Column("decided_by_user_id", sa.String(36), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conversation_id", sa.String(36), nullable=True),
        sa.Column("workflow_run_id", sa.String(36), nullable=True),
        sa.Column("node_id", sa.String(100), nullable=True),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, _TABLE):
        return

    for column in _new_columns():
        if not table_has_column(bind, _TABLE, column.name):
            op.add_column(_TABLE, column)

    if not index_exists(bind, _TABLE, _RUN_INDEX):
        op.create_index(_RUN_INDEX, _TABLE, ["workflow_run_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, _TABLE):
        return

    if index_exists(bind, _TABLE, _RUN_INDEX):
        op.drop_index(_RUN_INDEX, table_name=_TABLE)

    present = [c.name for c in reversed(_new_columns()) if table_has_column(bind, _TABLE, c.name)]
    if not present:
        return
    if bind.dialect.name == "postgresql":
        for name in present:
            op.drop_column(_TABLE, name)
        return
    # SQLite rebuilds the table to drop a column: one pass for all of them.
    with op.batch_alter_table(_TABLE) as batch:
        for name in present:
            batch.drop_column(name)

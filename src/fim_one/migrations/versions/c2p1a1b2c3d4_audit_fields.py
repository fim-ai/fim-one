"""Audit log: who of what kind, in which organization, from where, how it ended

``audit_logs`` held only platform administrators' actions: an operator id
and name, an action, a target and free text. It becomes the general event
table, so every row can say more. All new columns are nullable (or carry a
default) and none is a foreign key: a record outlives what it names.

- ``actor_kind`` (``user`` / ``admin`` / ``system`` / ``anonymous`` /
  ``external``), ``auth_method`` and ``credential_ref`` (an API key's id or
  a trigger key's prefix, never the key): who acted and how they were
  authenticated. Rows written before are all administrators' and leave
  these empty.
- ``org_id``: whose organization the thing acted on belongs to.
- ``outcome``: ``success`` (the default, and what every earlier row was),
  ``denied`` or ``failed``.
- ``changes``: JSON, the fields that changed with their old and new value;
  a secret field says only that it changed.
- ``ip_address``, ``user_agent``, ``request_id``: where the request came
  from.
- ``conversation_id``, ``workflow_run_id``, ``agent_id``: what the event
  happened in.
- ``admin_username`` widens from 50 to 255 characters: it holds a username
  or an email, and an email longer than 50 made the insert fail on
  PostgreSQL.
- indexes on ``created_at``, ``(org_id, created_at)`` and
  ``(target_type, target_id)``.

``login_history`` gains ``method``: ``password``, ``email_code`` or
``oauth:<provider>``. Earlier rows leave it empty.

Downgrade drops the new columns and indexes and narrows ``admin_username``
back, cutting any longer value to 50 characters first.

Revision ID: c2p1a1b2c3d4
Revises: c2p0a1b2c3d4
Create Date: 2026-11-11
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import index_exists, table_exists, table_has_column

revision: str = "c2p1a1b2c3d4"
down_revision: str | None = "c2p0a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AUDIT = "audit_logs"
_LOGINS = "login_history"
_LABEL = "admin_username"
_INDEXES: dict[str, list[str]] = {
    "ix_audit_logs_created_at": ["created_at"],
    "ix_audit_logs_org_id_created_at": ["org_id", "created_at"],
    "ix_audit_logs_target": ["target_type", "target_id"],
}


def _new_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("actor_kind", sa.String(20), nullable=True),
        sa.Column("auth_method", sa.String(20), nullable=True),
        sa.Column("credential_ref", sa.String(64), nullable=True),
        sa.Column("org_id", sa.String(36), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=False, server_default="success"),
        sa.Column("changes", sa.JSON(), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("request_id", sa.String(36), nullable=True),
        sa.Column("conversation_id", sa.String(36), nullable=True),
        sa.Column("workflow_run_id", sa.String(36), nullable=True),
        sa.Column("agent_id", sa.String(36), nullable=True),
    ]


def _label_length(bind: sa.Connection) -> int | None:
    for column in sa.inspect(bind).get_columns(_AUDIT):
        if column["name"] == _LABEL:
            return getattr(column["type"], "length", None)
    return None


def _resize_label(bind: sa.Connection, *, old: int, new: int) -> None:
    if _label_length(bind) == new:
        return
    # SQLite cannot alter a column in place: batch mode rebuilds the table.
    with op.batch_alter_table(_AUDIT) as batch:
        batch.alter_column(
            _LABEL,
            existing_type=sa.String(old),
            type_=sa.String(new),
            existing_nullable=False,
        )


def upgrade() -> None:
    bind = op.get_bind()

    if table_exists(bind, _AUDIT):
        for column in _new_columns():
            if not table_has_column(bind, _AUDIT, column.name):
                op.add_column(_AUDIT, column)
        _resize_label(bind, old=50, new=255)
        for name, columns in _INDEXES.items():
            if not index_exists(bind, _AUDIT, name):
                op.create_index(name, _AUDIT, columns)

    if table_exists(bind, _LOGINS) and not table_has_column(bind, _LOGINS, "method"):
        op.add_column(_LOGINS, sa.Column("method", sa.String(40), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()

    if table_exists(bind, _LOGINS) and table_has_column(bind, _LOGINS, "method"):
        with op.batch_alter_table(_LOGINS) as batch:
            batch.drop_column("method")

    if not table_exists(bind, _AUDIT):
        return
    for name in _INDEXES:
        if index_exists(bind, _AUDIT, name):
            op.drop_index(name, table_name=_AUDIT)
    present = [c.name for c in _new_columns() if table_has_column(bind, _AUDIT, c.name)]
    narrow = _label_length(bind) != 50
    if narrow:
        # A longer label cannot go back into 50 characters as it is.
        op.execute(
            sa.text(f"UPDATE {_AUDIT} SET {_LABEL} = SUBSTR({_LABEL}, 1, 50)")
        )
    if present or narrow:
        with op.batch_alter_table(_AUDIT) as batch:
            for name in present:
                batch.drop_column(name)
            if narrow:
                batch.alter_column(
                    _LABEL,
                    existing_type=sa.String(255),
                    type_=sa.String(50),
                    existing_nullable=False,
                )

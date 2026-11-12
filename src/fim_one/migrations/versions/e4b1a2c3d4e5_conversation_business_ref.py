"""Conversations: the host system's business reference

Adds ``conversations.business_ref``, a nullable ``VARCHAR(200)`` holding
the id of the business object a conversation is about in the system that
embeds FIM One (a contract number, a ticket id), and the index
``ix_conversations_user_id_business_ref`` on ``(user_id, business_ref)``
so "this user's conversations for reference X" is an index lookup.

Existing rows keep ``NULL``. Safe to run again: each step checks first.

On PostgreSQL the index is built without ``CONCURRENTLY``: writes to
``conversations`` are blocked for as long as the build takes.

Downgrade drops the index and the column; the references stored are lost.

Revision ID: e4b1a2c3d4e5
Revises: c2p4a1b2c3d4
Create Date: 2026-11-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import index_exists, table_exists, table_has_column

revision: str = "e4b1a2c3d4e5"
down_revision: str | None = "c2p4a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "conversations"
_COLUMN = "business_ref"
_INDEX = "ix_conversations_user_id_business_ref"


def upgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, _TABLE):
        return
    if not table_has_column(bind, _TABLE, _COLUMN):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(200), nullable=True))
    if not index_exists(bind, _TABLE, _INDEX):
        op.create_index(_INDEX, _TABLE, ["user_id", _COLUMN])


def downgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, _TABLE):
        return
    if index_exists(bind, _TABLE, _INDEX):
        op.drop_index(_INDEX, table_name=_TABLE)
    if table_has_column(bind, _TABLE, _COLUMN):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)

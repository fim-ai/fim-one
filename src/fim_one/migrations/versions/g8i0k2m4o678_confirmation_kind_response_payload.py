"""confirmation_requests: kind discriminator + response_payload + nullable org_id

The ``confirmation_requests`` table gains a second request kind:
``user_question`` (the ``ask_user_question`` tool pauses the ReAct loop
and waits for structured answers, reusing the inline-confirmation
pipeline).  Three schema changes:

* ``kind``             — discriminator: ``confirmation`` (default, the
                         existing approve/reject gate) or ``user_question``.
* ``response_payload`` — JSON answers written by the answer endpoint;
                         NULL for plain confirmations.
* ``org_id`` nullable  — question requests can originate from agent-less
                         playground chats that have no organisation; the
                         previous NOT NULL + FK pair made such rows
                         impossible to insert with PRAGMA foreign_keys=ON.

Revision ID: g8i0k2m4o678
Revises: f7h9j1l3n567
Create Date: 2026-08-10
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import table_exists, table_has_column

revision: str = "g8i0k2m4o678"
down_revision: Union[str, None] = "f7h9j1l3n567"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "confirmation_requests"


def upgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, _TABLE):
        # Fresh databases get the full schema from later create_all-style
        # migrations; nothing to patch here.
        return

    if not table_has_column(bind, _TABLE, "kind"):
        op.add_column(
            _TABLE,
            sa.Column(
                "kind",
                sa.String(30),
                nullable=False,
                server_default="confirmation",
            ),
        )

    if not table_has_column(bind, _TABLE, "response_payload"):
        op.add_column(
            _TABLE,
            sa.Column("response_payload", sa.JSON(), nullable=True),
        )

    # Relax org_id to nullable (idempotent: skip when already nullable).
    org_col = next(
        (
            c
            for c in sa.inspect(bind).get_columns(_TABLE)
            if c["name"] == "org_id"
        ),
        None,
    )
    if org_col is not None and not org_col["nullable"]:
        with op.batch_alter_table(_TABLE) as batch:
            batch.alter_column(
                "org_id",
                existing_type=sa.String(36),
                nullable=True,
            )


def downgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, _TABLE):
        return
    # NOT restoring org_id NOT NULL — existing NULL rows would make the
    # downgrade fail; the relaxed constraint is harmless on old code.
    if table_has_column(bind, _TABLE, "response_payload"):
        op.drop_column(_TABLE, "response_payload")
    if table_has_column(bind, _TABLE, "kind"):
        op.drop_column(_TABLE, "kind")

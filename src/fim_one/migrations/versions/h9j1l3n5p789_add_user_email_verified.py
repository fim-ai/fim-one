"""users: add email_verified, gating OAuth auto-bind on a proven address

``_handle_login`` binds an OAuth identity to an existing local account when
the provider reports a *verified* address matching ``users.email``. Until now
the check looked only at what the provider claimed about the incoming login,
never at how the stored address got there. An OAuth login carrying an
unverified address was refused a match — but still created a new account with
that address in ``users.email``, where a later verified login from a different
provider would happily match it. That let an attacker park a victim's address
on an account they control and capture the victim's next OAuth sign-in.

The column records whether an address was ever proven to belong to the
account holder, and the auto-bind lookup now requires it on both sides.

Existing rows are grandfathered to TRUE. Their real status is unknowable
after the fact, and defaulting them to FALSE would stop every current user
from binding a second provider. The backfill runs only when the column is
first created, so an account legitimately stored as unverified is never
flipped by a re-run.

Revision ID: h9j1l3n5p789
Revises: g8i0k2m4o678
Create Date: 2026-08-22
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import table_exists, table_has_column

revision: str = "h9j1l3n5p789"
down_revision: Union[str, None] = "g8i0k2m4o678"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "users"
_COLUMN = "email_verified"


def upgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, _TABLE):
        return
    if table_has_column(bind, _TABLE, _COLUMN):
        return

    op.add_column(
        _TABLE,
        sa.Column(
            _COLUMN,
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
    )
    # Grandfather every pre-existing account. Scoped to the create branch
    # above: on a re-run this must not overwrite a genuine FALSE.
    op.execute(sa.text(f"UPDATE {_TABLE} SET {_COLUMN} = TRUE"))


def downgrade() -> None:
    bind = op.get_bind()
    if table_exists(bind, _TABLE) and table_has_column(bind, _TABLE, _COLUMN):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)

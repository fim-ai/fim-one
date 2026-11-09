"""Store workflow trigger keys as a SHA-256 hash and a display prefix

``workflows.api_key`` held the trigger key in plain text. This migration adds
``api_key_hash`` (hex SHA-256, unique) and ``api_key_prefix`` (the first
characters, for display), hashes every existing key into them, and drops the
plain-text column. Existing keys keep working: the trigger endpoint hashes
the presented key and looks it up by hash.

Downgrade restores an empty ``api_key`` column. Plain-text keys cannot be
recovered from their hashes, so owners must generate new keys after a
downgrade.

Revision ID: t10p1b2c3d4e
Revises: t10p0a1b2c3d
Create Date: 2026-11-04
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import index_exists, table_exists, table_has_column

revision: str = "t10p1b2c3d4e"
down_revision: str | None = "t10p0a1b2c3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "workflows"
_HASH_INDEX = "ix_workflows_api_key_hash"
_OLD_INDEX = "ix_workflows_api_key"
PREFIX_LEN = 10


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def upgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, _TABLE):
        return

    if not table_has_column(bind, _TABLE, "api_key_hash"):
        op.add_column(_TABLE, sa.Column("api_key_hash", sa.String(64), nullable=True))
    if not table_has_column(bind, _TABLE, "api_key_prefix"):
        op.add_column(_TABLE, sa.Column("api_key_prefix", sa.String(16), nullable=True))
    if not index_exists(bind, _TABLE, _HASH_INDEX):
        op.create_index(_HASH_INDEX, _TABLE, ["api_key_hash"], unique=True)

    if not table_has_column(bind, _TABLE, "api_key"):
        return

    # Hash in Python: identical on SQLite and PostgreSQL, and the row count
    # is the number of workflows with a trigger key (small).
    rows = bind.execute(
        sa.text(
            "SELECT id, api_key FROM workflows "
            "WHERE api_key IS NOT NULL AND api_key_hash IS NULL"
        )
    ).fetchall()
    for wf_id, raw_key in rows:
        bind.execute(
            sa.text(
                "UPDATE workflows SET api_key_hash = :h, api_key_prefix = :p "
                "WHERE id = :id"
            ),
            {"h": hash_key(raw_key), "p": raw_key[:PREFIX_LEN], "id": wf_id},
        )

    if index_exists(bind, _TABLE, _OLD_INDEX):
        op.drop_index(_OLD_INDEX, table_name=_TABLE)
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column("api_key")


def downgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, _TABLE):
        return

    if not table_has_column(bind, _TABLE, "api_key"):
        op.add_column(_TABLE, sa.Column("api_key", sa.String(64), nullable=True))
    if not index_exists(bind, _TABLE, _OLD_INDEX):
        op.create_index(_OLD_INDEX, _TABLE, ["api_key"], unique=True)

    if index_exists(bind, _TABLE, _HASH_INDEX):
        op.drop_index(_HASH_INDEX, table_name=_TABLE)
    with op.batch_alter_table(_TABLE) as batch:
        if table_has_column(bind, _TABLE, "api_key_hash"):
            batch.drop_column("api_key_hash")
        if table_has_column(bind, _TABLE, "api_key_prefix"):
            batch.drop_column("api_key_prefix")

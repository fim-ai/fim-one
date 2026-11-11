"""Call log: every record has the time it was made

Migration ``b2d4e6f8a901`` created ``connector_call_logs.created_at``
nullable and without a default. The model declares it like every other
table's (``TimestampMixin``: ``server_default=now()``, not null), so the
ORM leaves the column out of the insert and expects the database to fill
it. On a database built by the migrations nothing did, on SQLite and on
PostgreSQL alike: every call record has had an empty ``created_at``. The
readers ask by time (statistics by period, today's calls, the retention
sweep), so none of them saw any call, and no record was ever swept.

- The column gets the default the model declares (``now()``) and becomes
  not null, as the model says. A database built with ``create_all``
  already has both and is left as it is.
- Records without a time get the best one there is: the start of the
  workflow run they were made in, else the start of their conversation
  (a call comes after either; the record lands near when it was made and
  is swept no later than it should be), else the time of this migration.

Safe to run again: both steps only touch what is still missing.

Downgrade makes the column nullable again and drops the default. The
times filled in stay.

Revision ID: c2p4a1b2c3d4
Revises: c2p3a1b2c3d4
Create Date: 2026-11-12
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import table_exists

revision: str = "c2p4a1b2c3d4"
down_revision: str | None = "c2p3a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "connector_call_logs"
_COLUMN = "created_at"


def _column(bind: sa.Connection) -> dict[str, Any]:
    for column in sa.inspect(bind).get_columns(_TABLE):
        if column["name"] == _COLUMN:
            return dict(column)
    raise RuntimeError(f"{_TABLE}.{_COLUMN} is missing")


def _alter(bind: sa.Connection, existing: dict[str, Any], **changes: Any) -> None:
    """Change the column's default and nullability (a table rebuild on SQLite)."""
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(_TABLE) as batch:
            batch.alter_column(_COLUMN, existing_type=existing["type"], **changes)
        return
    op.alter_column(_TABLE, _COLUMN, existing_type=existing["type"], **changes)


def _backfill(bind: sa.Connection) -> None:
    sources = []
    if table_exists(bind, "workflow_runs"):
        sources.append(
            f"(SELECT r.created_at FROM workflow_runs r WHERE r.id = {_TABLE}.workflow_run_id)"
        )
    if table_exists(bind, "conversations"):
        sources.append(
            f"(SELECT c.created_at FROM conversations c WHERE c.id = {_TABLE}.conversation_id)"
        )
    sources.append("CURRENT_TIMESTAMP")
    bind.execute(
        sa.text(
            f"UPDATE {_TABLE} SET {_COLUMN} = COALESCE({', '.join(sources)}) "
            f"WHERE {_COLUMN} IS NULL"
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, _TABLE):
        return
    _backfill(bind)
    column = _column(bind)
    if column["nullable"] or column.get("default") is None:
        _alter(bind, column, server_default=sa.func.now(), nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, _TABLE):
        return
    column = _column(bind)
    if not column["nullable"] or column.get("default") is not None:
        _alter(
            bind,
            column,
            server_default=None,
            existing_server_default=column.get("default"),
            nullable=True,
        )

"""Call log: room for MCP calls, where a call came from, no foreign keys

``connector_call_logs`` held connector calls only: ``connector_id`` was a
required foreign key, so an MCP tool call had no row to go in and left no
record from any entry point.

- ``connector_id`` becomes nullable (an MCP row has none).
- ``resource_kind`` says what was called: ``connector``, ``database`` or
  ``mcp``. Existing rows read ``connector``, database calls among them.
- ``mcp_server_id`` (indexed) names the MCP server of an ``mcp`` row.
- ``workflow_run_id``, ``entry`` and ``credential_source`` record the
  workflow run the call was made in, the entry point it came through, and
  whose credential it went out with. All nullable; rows written earlier
  leave them empty. None of the new id columns is a foreign key.
- The foreign keys on ``connector_id``, ``conversation_id`` and ``user_id``
  are dropped; the columns and their indexes stay. A call record is an
  activity record: it has to stay when the connector, the conversation or
  the user it names is deleted. The constraints had no delete rule, so with
  foreign keys enforced (PostgreSQL always; SQLite since
  ``PRAGMA foreign_keys=ON``) a conversation or connector that had a call
  record could not be deleted at all. That was rare; with MCP calls and
  chats without an agent recorded, most conversations that use a tool have
  one.

Additive: code from before this revision keeps working on the new shape,
so rolling the code back does not need a downgrade.

Downgrade deletes the rows the old shape cannot hold (MCP calls: no
connector), makes ``connector_id`` required again and drops the new
columns. It does **not** put the foreign keys back: by then records may
name connectors, conversations or users that are gone, and restoring the
constraints would mean deleting those records.

Revision ID: c1p2a1b2c3d4
Revises: t10p3c4d5e6f
Create Date: 2026-11-09
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import index_exists, table_exists, table_has_column

revision: str = "c1p2a1b2c3d4"
down_revision: str | None = "t10p3c4d5e6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "connector_call_logs"
_MCP_INDEX = "ix_connector_call_logs_mcp_server_id"
# The columns whose foreign key goes. They keep their indexes.
_REFERENCING_COLUMNS = ("connector_id", "conversation_id", "user_id")
# Gives the unnamed foreign keys SQLite reflects a name batch mode can drop
# them by.
_NAMING_CONVENTION = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _new_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("resource_kind", sa.String(20), nullable=True, server_default="connector"),
        sa.Column("mcp_server_id", sa.String(36), nullable=True),
        sa.Column("workflow_run_id", sa.String(36), nullable=True),
        sa.Column("entry", sa.String(20), nullable=True),
        sa.Column("credential_source", sa.String(20), nullable=True),
    ]


def _connector_id_nullable(bind: sa.Connection) -> bool:
    for column in sa.inspect(bind).get_columns(_TABLE):
        if column["name"] == "connector_id":
            return bool(column["nullable"])
    return True


def _foreign_key_names(bind: sa.Connection) -> list[str]:
    """Names of the foreign keys still present on the referencing columns.

    Read from the database, not assumed: PostgreSQL names them
    ``<table>_<column>_fkey`` unless they were created some other way, and
    SQLite reflects them unnamed (the naming convention then supplies one).
    """
    names: list[str] = []
    for fk in sa.inspect(bind).get_foreign_keys(_TABLE):
        columns = fk["constrained_columns"]
        if len(columns) != 1 or columns[0] not in _REFERENCING_COLUMNS:
            continue
        names.append(fk.get("name") or f"fk_{_TABLE}_{columns[0]}_{fk['referred_table']}")
    return names


def _rewrite(
    bind: sa.Connection,
    *,
    nullable: bool | None = None,
    drop_foreign_keys: Sequence[str] = (),
    drop_columns: Sequence[str] = (),
) -> None:
    """Apply the column and constraint changes in one pass over the table.

    SQLite cannot alter a column or drop a constraint in place: batch mode
    rebuilds the table, so everything goes into a single batch.
    """
    if nullable is None and not drop_foreign_keys and not drop_columns:
        return
    if bind.dialect.name == "postgresql":
        for name in drop_foreign_keys:
            op.drop_constraint(name, _TABLE, type_="foreignkey")
        if nullable is not None:
            op.alter_column(_TABLE, "connector_id", existing_type=sa.String(36), nullable=nullable)
        for name in drop_columns:
            op.drop_column(_TABLE, name)
        return

    with op.batch_alter_table(_TABLE, naming_convention=_NAMING_CONVENTION) as batch:
        for name in drop_foreign_keys:
            batch.drop_constraint(name, type_="foreignkey")
        if nullable is not None:
            batch.alter_column("connector_id", existing_type=sa.String(36), nullable=nullable)
        for name in drop_columns:
            batch.drop_column(name)


def upgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, _TABLE):
        return

    for column in _new_columns():
        if not table_has_column(bind, _TABLE, column.name):
            op.add_column(_TABLE, column)

    if not index_exists(bind, _TABLE, _MCP_INDEX):
        op.create_index(_MCP_INDEX, _TABLE, ["mcp_server_id"])

    _rewrite(
        bind,
        nullable=None if _connector_id_nullable(bind) else True,
        drop_foreign_keys=_foreign_key_names(bind),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, _TABLE):
        return

    if index_exists(bind, _TABLE, _MCP_INDEX):
        op.drop_index(_MCP_INDEX, table_name=_TABLE)

    make_required = _connector_id_nullable(bind)
    if make_required:
        # An MCP call has no connector; the old shape has no place for it.
        bind.execute(sa.text(f"DELETE FROM {_TABLE} WHERE connector_id IS NULL"))

    # The foreign keys stay dropped (see the module docstring).
    _rewrite(
        bind,
        nullable=False if make_required else None,
        drop_columns=[
            c.name for c in reversed(_new_columns()) if table_has_column(bind, _TABLE, c.name)
        ],
    )

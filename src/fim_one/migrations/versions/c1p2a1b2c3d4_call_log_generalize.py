"""Call log: room for MCP calls, and where a call came from

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
  leave them empty.
- The references to ``connectors`` and ``conversations`` become
  ``ON DELETE SET NULL``. They had no delete rule, so with foreign keys
  enforced (PostgreSQL always; SQLite since ``PRAGMA foreign_keys=ON``) a
  conversation or connector that had a call record could not be deleted.
  Few did; with MCP calls and chats without an agent recorded, most
  conversations that use a tool will.

Additive: code from before this revision keeps working on the new shape,
so rolling the code back does not need a downgrade.

Downgrade deletes the rows the old shape cannot hold (no connector: MCP
calls, and calls whose connector has since been deleted), restores the
plain references and drops the new columns.

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
# column -> referred table, for the two references that get a delete rule
_REFERENCES = {"connector_id": "connectors", "conversation_id": "conversations"}
# Deterministic names so the (otherwise unnamed) reflected FK can be dropped
# in SQLite batch mode; the re-created constraints carry these names on
# PostgreSQL too.
_NAMING_CONVENTION = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _fk_name(column: str) -> str:
    return f"fk_{_TABLE}_{column}_{_REFERENCES[column]}"


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


def _references(bind: sa.Connection, *, set_null: bool) -> list[tuple[str, str | None]]:
    """``(column, constraint name)`` of the references whose delete rule is
    (*set_null* true) or is not (*set_null* false) ``SET NULL``."""
    found: list[tuple[str, str | None]] = []
    for fk in sa.inspect(bind).get_foreign_keys(_TABLE):
        columns = fk["constrained_columns"]
        if len(columns) != 1 or columns[0] not in _REFERENCES:
            continue
        rule = str((fk.get("options") or {}).get("ondelete") or "").upper()
        if (rule == "SET NULL") is set_null:
            found.append((columns[0], fk.get("name")))
    return found


def _rewrite(
    bind: sa.Connection,
    *,
    nullable: bool | None,
    references: list[tuple[str, str | None]],
    ondelete: str | None,
    drop_columns: Sequence[str] = (),
) -> None:
    """Apply the column and reference changes in one pass over the table.

    SQLite cannot alter a column or a constraint in place: batch mode
    rebuilds the table, so everything goes into a single batch.
    """
    if nullable is None and not references and not drop_columns:
        return
    if bind.dialect.name == "postgresql":
        if nullable is not None:
            op.alter_column(_TABLE, "connector_id", existing_type=sa.String(36), nullable=nullable)
        for column, name in references:
            if name:
                op.drop_constraint(name, _TABLE, type_="foreignkey")
            op.create_foreign_key(
                _fk_name(column), _TABLE, _REFERENCES[column], [column], ["id"], ondelete=ondelete
            )
        for name in drop_columns:
            op.drop_column(_TABLE, name)
        return

    with op.batch_alter_table(_TABLE, naming_convention=_NAMING_CONVENTION) as batch:
        if nullable is not None:
            batch.alter_column("connector_id", existing_type=sa.String(36), nullable=nullable)
        for column, _name in references:
            batch.drop_constraint(_fk_name(column), type_="foreignkey")
            batch.create_foreign_key(
                _fk_name(column), _REFERENCES[column], [column], ["id"], ondelete=ondelete
            )
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
        references=_references(bind, set_null=False),
        ondelete="SET NULL",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, _TABLE):
        return

    if index_exists(bind, _TABLE, _MCP_INDEX):
        op.drop_index(_MCP_INDEX, table_name=_TABLE)

    make_required = _connector_id_nullable(bind)
    if make_required:
        # No connector: an MCP call, or a call whose connector was deleted
        # since. The old shape has no place for either.
        bind.execute(sa.text(f"DELETE FROM {_TABLE} WHERE connector_id IS NULL"))

    _rewrite(
        bind,
        nullable=False if make_required else None,
        references=_references(bind, set_null=True),
        ondelete=None,
        drop_columns=[
            c.name for c in reversed(_new_columns()) if table_has_column(bind, _TABLE, c.name)
        ],
    )

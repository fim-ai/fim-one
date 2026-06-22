"""allow_fallback defaults to FALSE for connectors and MCP servers

Sharing the owner's credential as a fallback for subscribers without their own
credential is now opt-in, not the default. This flips both the column
``server_default`` (future inserts) and every existing row to ``FALSE`` so the
dangerous "default shares the owner's token" behaviour no longer applies to any
connector or MCP server — owners can still always use their own credential, and
anyone who wants fallback must now enable it explicitly per resource.

Revision ID: d5f7h9j1l345
Revises: c4e6g8i0k234
Create Date: 2026-06-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import table_has_column

revision = "d5f7h9j1l345"
down_revision = "c4e6g8i0k234"
branch_labels = None
depends_on = None

# (table, column) pairs sharing the same flip.
_TARGETS = [
    ("connectors", "allow_fallback"),
    ("mcp_servers", "allow_fallback"),
]


def _set_default(table: str, column: str, value: str) -> None:
    bind = op.get_bind()
    if not table_has_column(bind, table, column):
        return
    # Rewrite existing rows. ``true``/``false`` are accepted by both SQLite
    # (3.23+) and PostgreSQL boolean columns.
    op.execute(f"UPDATE {table} SET {column} = {value}")
    # Change the column default for future inserts. SQLite cannot ALTER a
    # column in place — batch mode recreates the table.
    if bind.dialect.name == "postgresql":
        op.alter_column(
            table,
            column,
            server_default=sa.text(value.upper()),
            existing_type=sa.Boolean(),
            existing_nullable=False,
        )
    else:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                column,
                server_default=sa.text(value.upper()),
                existing_type=sa.Boolean(),
                existing_nullable=False,
            )


def upgrade() -> None:
    for table, column in _TARGETS:
        _set_default(table, column, "false")


def downgrade() -> None:
    for table, column in _TARGETS:
        _set_default(table, column, "true")

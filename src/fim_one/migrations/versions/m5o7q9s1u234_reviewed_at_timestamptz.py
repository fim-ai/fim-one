"""re-scan and convert any naive TIMESTAMP columns to TIMESTAMPTZ

l2n4p6r8t901 performed a one-time scan converting every TIMESTAMP WITHOUT
TIME ZONE column to TIMESTAMPTZ. Columns added *after* that migration —
notably the publish-review ``reviewed_at`` columns on agents / connectors /
knowledge_bases / mcp_servers (added by a1b2c3) — were created naive and so
were never converted. On PostgreSQL the ORM writes tz-aware datetimes
(``datetime.now(UTC)``), which asyncpg rejects against a naive column, making
admin approve/reject of those resources fail with a 500.

This migration re-runs the same information_schema scan to repair any naive
timestamp columns that have crept in since. It is naturally idempotent: the
query only selects ``timestamp without time zone`` columns, so columns already
converted to TIMESTAMPTZ are skipped. SQLite is unaffected (it does not
distinguish tz-aware timestamps), so it returns early.

Revision ID: m5o7q9s1u234
Revises: k2g4h6i8j901
Create Date: 2026-05-30
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "m5o7q9s1u234"
down_revision: Union[str, None] = "k2g4h6i8j901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    # PostgreSQL: convert any remaining TIMESTAMP WITHOUT TIME ZONE -> WITH TIME ZONE
    result = bind.execute(
        sa.text(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND data_type = 'timestamp without time zone'
            ORDER BY table_name, column_name
            """
        )
    )
    for row in result:
        table, col = row[0], row[1]
        op.alter_column(
            table,
            col,
            type_=sa.DateTime(timezone=True),
            existing_type=sa.DateTime(),
            postgresql_using=f'"{col}" AT TIME ZONE \'UTC\'',
        )


def downgrade() -> None:
    # Forward-only repair: reverting tz-aware columns back to naive would
    # re-introduce the asyncpg write failure this migration fixes, and this
    # migration cannot tell which columns it converted versus l2n4p6r8t901.
    pass

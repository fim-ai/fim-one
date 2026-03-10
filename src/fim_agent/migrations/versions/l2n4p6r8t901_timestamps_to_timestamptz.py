"""convert all TIMESTAMP columns to TIMESTAMP WITH TIME ZONE

asyncpg rejects mixing tz-aware Python datetimes with TIMESTAMP WITHOUT
TIME ZONE columns.  This migration converts all timestamp columns to
TIMESTAMPTZ for PostgreSQL compatibility.  SQLite is unaffected.

Revision ID: l2n4p6r8t901
Revises: k1m3o5q7s890
Create Date: 2026-03-10 17:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "l2n4p6r8t901"
down_revision: Union[str, None] = "k1m3o5q7s890"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    # PostgreSQL: convert all TIMESTAMP WITHOUT TIME ZONE -> WITH TIME ZONE
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
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    # PostgreSQL: revert TIMESTAMPTZ -> TIMESTAMP WITHOUT TIME ZONE
    result = bind.execute(
        sa.text(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND data_type = 'timestamp with time zone'
            ORDER BY table_name, column_name
            """
        )
    )
    for row in result:
        table, col = row[0], row[1]
        op.alter_column(
            table,
            col,
            type_=sa.DateTime(),
            existing_type=sa.DateTime(timezone=True),
            postgresql_using=f'"{col}" AT TIME ZONE \'UTC\'',
        )

"""fix_base_url_nullable_sqlite

Revision ID: 376d120aac52
Revises: p6r8t0v2x345
Create Date: 2026-03-11 14:54:38.460405
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from fim_agent.migrations.helpers import table_exists


# revision identifiers, used by Alembic.
revision: str = '376d120aac52'
down_revision: Union[str, None] = 'p6r8t0v2x345'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, "connectors"):
        return

    if bind.dialect.name != "postgresql":
        # SQLite cannot ALTER COLUMN — batch mode recreates the table
        with op.batch_alter_table("connectors") as batch_op:
            batch_op.alter_column("base_url", existing_type=sa.String(500), nullable=True)


def downgrade() -> None:
    pass

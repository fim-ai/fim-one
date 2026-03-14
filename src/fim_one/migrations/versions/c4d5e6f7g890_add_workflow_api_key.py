"""add api_key to workflows for external trigger

Revision ID: c4d5e6f7g890
Revises: b3c4d5e6f789
Create Date: 2026-03-14
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import index_exists, table_exists, table_has_column

revision: str = "c4d5e6f7g890"
down_revision: Union[str, None] = "b3c4d5e6f789"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    if table_exists(bind, "workflows") and not table_has_column(
        bind, "workflows", "api_key"
    ):
        op.add_column(
            "workflows",
            sa.Column("api_key", sa.String(64), nullable=True),
        )

    # Create unique index on api_key for fast lookups
    if table_exists(bind, "workflows") and not index_exists(
        bind, "workflows", "ix_workflows_api_key"
    ):
        op.create_index(
            "ix_workflows_api_key",
            "workflows",
            ["api_key"],
            unique=True,
        )


def downgrade() -> None:
    op.drop_index("ix_workflows_api_key", table_name="workflows")
    op.drop_column("workflows", "api_key")

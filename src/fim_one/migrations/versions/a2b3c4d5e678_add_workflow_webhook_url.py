"""add webhook_url to workflows

Revision ID: a2b3c4d5e678
Revises: v1w2x3y4z567
Create Date: 2026-03-14
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import table_exists, table_has_column

revision: str = "a2b3c4d5e678"
down_revision: Union[str, None] = "v1w2x3y4z567"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    if table_exists(bind, "workflows") and not table_has_column(
        bind, "workflows", "webhook_url"
    ):
        op.add_column(
            "workflows",
            sa.Column("webhook_url", sa.String(500), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("workflows", "webhook_url")

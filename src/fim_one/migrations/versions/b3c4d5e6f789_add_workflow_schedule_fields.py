"""add workflow schedule fields

Revision ID: b3c4d5e6f789
Revises: a2b3c4d5e678
Create Date: 2026-03-14
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import table_exists, table_has_column

revision: str = "b3c4d5e6f789"
down_revision: Union[str, None] = "a2b3c4d5e678"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    if not table_exists(bind, "workflows"):
        return

    if not table_has_column(bind, "workflows", "schedule_cron"):
        op.add_column(
            "workflows",
            sa.Column("schedule_cron", sa.String(100), nullable=True),
        )

    if not table_has_column(bind, "workflows", "schedule_enabled"):
        op.add_column(
            "workflows",
            sa.Column(
                "schedule_enabled",
                sa.Boolean,
                nullable=False,
                server_default=sa.text("FALSE"),
            ),
        )

    if not table_has_column(bind, "workflows", "schedule_inputs"):
        op.add_column(
            "workflows",
            sa.Column("schedule_inputs", sa.JSON, nullable=True),
        )

    if not table_has_column(bind, "workflows", "schedule_timezone"):
        op.add_column(
            "workflows",
            sa.Column(
                "schedule_timezone",
                sa.String(50),
                nullable=True,
                server_default="UTC",
            ),
        )


def downgrade() -> None:
    op.drop_column("workflows", "schedule_timezone")
    op.drop_column("workflows", "schedule_inputs")
    op.drop_column("workflows", "schedule_enabled")
    op.drop_column("workflows", "schedule_cron")

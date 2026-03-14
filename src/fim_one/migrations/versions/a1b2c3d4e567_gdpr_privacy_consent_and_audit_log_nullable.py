"""GDPR: add privacy_accepted_at to users, make audit_logs.admin_id nullable

Revision ID: a1b2c3d4e567
Revises: z4a6b8c0d123
Create Date: 2026-03-14
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import table_has_column

revision: str = "a1b2c3d4e567"
down_revision: Union[str, None] = "z4a6b8c0d123"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # Add privacy_accepted_at to users
    if not table_has_column(bind, "users", "privacy_accepted_at"):
        with op.batch_alter_table("users") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "privacy_accepted_at",
                    sa.DateTime(timezone=True),
                    nullable=True,
                )
            )

    # Make audit_logs.admin_id nullable (for GDPR anonymization on user deletion)
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.alter_column(
            "admin_id",
            existing_type=sa.String(36),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.alter_column(
            "admin_id",
            existing_type=sa.String(36),
            nullable=False,
        )

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("privacy_accepted_at")

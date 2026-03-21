"""add tool_choice_enabled to model_provider_models

Revision ID: p4q5r6s7t890
Revises: o3p4q5r6s789
Create Date: 2026-03-22
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import table_has_column

# revision identifiers, used by Alembic.
revision: str = "p4q5r6s7t890"
down_revision: Union[str, None] = "o3p4q5r6s789"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    if not table_has_column(bind, "model_provider_models", "tool_choice_enabled"):
        with op.batch_alter_table("model_provider_models") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "tool_choice_enabled",
                    sa.Boolean,
                    nullable=False,
                    server_default=sa.text("TRUE"),
                )
            )


def downgrade() -> None:
    with op.batch_alter_table("model_provider_models") as batch_op:
        batch_op.drop_column("tool_choice_enabled")

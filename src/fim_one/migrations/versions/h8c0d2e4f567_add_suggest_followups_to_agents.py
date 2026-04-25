"""Add ``suggest_followups`` opt-in toggle to ``agents``.

When True the chat SSE stream emits ``post_processing`` and
``suggestions`` events between ``done`` and ``end`` so the playground
can surface the existing fast-LLM follow-up question generation.
Default FALSE — the feature is opt-in per agent because it costs an
extra round-trip on the fast model.

Revision ID: h8c0d2e4f567
Revises: g7b9c1d3e456
Create Date: 2026-04-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "h8c0d2e4f567"
down_revision = "g7b9c1d3e456"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    from fim_one.migrations.helpers import table_exists, table_has_column

    if table_exists(bind, "agents") and not table_has_column(
        bind, "agents", "suggest_followups"
    ):
        op.add_column(
            "agents",
            sa.Column(
                "suggest_followups",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("FALSE"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    from fim_one.migrations.helpers import table_exists, table_has_column

    if table_exists(bind, "agents") and table_has_column(
        bind, "agents", "suggest_followups"
    ):
        with op.batch_alter_table("agents") as batch_op:
            batch_op.drop_column("suggest_followups")

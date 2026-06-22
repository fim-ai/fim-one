"""workflow_runs.total_tokens — meter unattended workflow LLM usage

Webhook- and cron-triggered workflow runs execute LLM/Agent nodes but used to
record no token usage, so they were a free, unmetered LLM spigot. This adds a
per-run token counter (billed to the workflow owner) that the quota window sums
alongside chat usage.

Revision ID: e6g8i0k2m456
Revises: d5f7h9j1l345
Create Date: 2026-06-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import table_exists, table_has_column

revision = "e6g8i0k2m456"
down_revision = "d5f7h9j1l345"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, "workflow_runs"):
        return
    if not table_has_column(bind, "workflow_runs", "total_tokens"):
        op.add_column(
            "workflow_runs",
            sa.Column(
                "total_tokens",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, "workflow_runs"):
        return
    if table_has_column(bind, "workflow_runs", "total_tokens"):
        with op.batch_alter_table("workflow_runs") as batch_op:
            batch_op.drop_column("total_tokens")

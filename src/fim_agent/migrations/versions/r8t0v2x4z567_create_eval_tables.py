"""create eval tables

Revision ID: r8t0v2x4z567
Revises: q7s9u1w3y456
Create Date: 2026-03-11 20:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from fim_agent.migrations.helpers import table_exists

revision: str = "r8t0v2x4z567"
down_revision: Union[str, None] = "q7s9u1w3y456"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    if not table_exists(bind, "eval_datasets"):
        op.create_table(
            "eval_datasets",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not table_exists(bind, "eval_cases"):
        op.create_table(
            "eval_cases",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "dataset_id",
                sa.String(36),
                sa.ForeignKey("eval_datasets.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("prompt", sa.Text, nullable=False),
            sa.Column("expected_behavior", sa.Text, nullable=False),
            sa.Column("assertions", sa.JSON, nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not table_exists(bind, "eval_runs"):
        op.create_table(
            "eval_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("agent_id", sa.String(36), sa.ForeignKey("agents.id"), nullable=False, index=True),
            sa.Column(
                "dataset_id",
                sa.String(36),
                sa.ForeignKey("eval_datasets.id"),
                nullable=False,
                index=True,
            ),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("total_cases", sa.Integer, nullable=False, server_default="0"),
            sa.Column("passed_cases", sa.Integer, nullable=False, server_default="0"),
            sa.Column("failed_cases", sa.Integer, nullable=False, server_default="0"),
            sa.Column("avg_latency_ms", sa.Float, nullable=True),
            sa.Column("total_tokens", sa.Integer, nullable=True),
            sa.Column("error_message", sa.Text, nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not table_exists(bind, "eval_case_results"):
        op.create_table(
            "eval_case_results",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "run_id",
                sa.String(36),
                sa.ForeignKey("eval_runs.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("case_id", sa.String(36), sa.ForeignKey("eval_cases.id"), nullable=False, index=True),
            sa.Column("status", sa.String(10), nullable=False),
            sa.Column("agent_answer", sa.Text, nullable=True),
            sa.Column("grader_reasoning", sa.Text, nullable=True),
            sa.Column("latency_ms", sa.Integer, nullable=True),
            sa.Column("prompt_tokens", sa.Integer, nullable=True),
            sa.Column("completion_tokens", sa.Integer, nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    op.drop_table("eval_case_results")
    op.drop_table("eval_runs")
    op.drop_table("eval_cases")
    op.drop_table("eval_datasets")

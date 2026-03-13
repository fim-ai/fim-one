"""create workflow tables

Revision ID: w1x2y3z4a567
Revises: f8g9h0a1b2c3
Create Date: 2026-03-14
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import table_exists

revision: str = "w1x2y3z4a567"
down_revision: Union[str, None] = "f8g9h0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    if not table_exists(bind, "workflows"):
        op.create_table(
            "workflows",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id"),
                nullable=False,
                index=True,
            ),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("icon", sa.String(100), nullable=True),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("blueprint", sa.JSON, nullable=False),
            sa.Column("input_schema", sa.JSON, nullable=True),
            sa.Column("output_schema", sa.JSON, nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
            sa.Column(
                "is_active",
                sa.Boolean,
                nullable=False,
                server_default=sa.text("TRUE"),
            ),
            sa.Column(
                "visibility",
                sa.String(20),
                nullable=False,
                server_default="personal",
            ),
            sa.Column(
                "org_id",
                sa.String(36),
                sa.ForeignKey("organizations.id"),
                nullable=True,
                index=True,
            ),
            sa.Column("publish_status", sa.String(20), nullable=True),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reviewed_by", sa.String(36), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("review_note", sa.Text, nullable=True),
            sa.Column("env_vars_blob", sa.Text, nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not table_exists(bind, "workflow_runs"):
        op.create_table(
            "workflow_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "workflow_id",
                sa.String(36),
                sa.ForeignKey("workflows.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id"),
                nullable=False,
                index=True,
            ),
            sa.Column("blueprint_snapshot", sa.JSON, nullable=False),
            sa.Column("inputs", sa.JSON, nullable=True),
            sa.Column("outputs", sa.JSON, nullable=True),
            sa.Column("node_results", sa.JSON, nullable=True),
            sa.Column(
                "status",
                sa.String(20),
                nullable=False,
                server_default="pending",
            ),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("duration_ms", sa.Integer, nullable=True),
            sa.Column("error", sa.Text, nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("workflow_runs")
    op.drop_table("workflows")

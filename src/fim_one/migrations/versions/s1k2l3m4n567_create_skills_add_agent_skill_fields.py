"""create skills table and add agent skill fields

Revision ID: s1k2l3m4n567
Revises: x2y3z4a5b678
Create Date: 2026-03-14
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import table_exists, table_has_column

revision: str = "s1k2l3m4n567"
down_revision: Union[str, None] = "x2y3z4a5b678"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # -- Create skills table --
    if not table_exists(bind, "skills"):
        op.create_table(
            "skills",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id"),
                nullable=True,
                index=True,
            ),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("content", sa.Text, nullable=False),
            sa.Column("script", sa.Text, nullable=True),
            sa.Column("script_type", sa.String(20), nullable=True),
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
            sa.Column(
                "is_active",
                sa.Boolean,
                nullable=False,
                server_default=sa.text("TRUE"),
            ),
            sa.Column(
                "status",
                sa.String(20),
                nullable=False,
                server_default="draft",
            ),
            sa.Column("publish_status", sa.String(20), nullable=True),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reviewed_by", sa.String(36), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("review_note", sa.Text, nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )

    # -- Add skill_ids and compact_instructions to agents table --
    if table_exists(bind, "agents"):
        if not table_has_column(bind, "agents", "skill_ids"):
            op.add_column(
                "agents",
                sa.Column("skill_ids", sa.JSON, nullable=True),
            )
        if not table_has_column(bind, "agents", "compact_instructions"):
            op.add_column(
                "agents",
                sa.Column("compact_instructions", sa.Text, nullable=True),
            )


def downgrade() -> None:
    op.drop_column("agents", "compact_instructions")
    op.drop_column("agents", "skill_ids")
    op.drop_table("skills")

"""Add publish review system for org-level resource approval.

Revision ID: a1b2c3
Revises: t1u3v5x7z890
Create Date: 2026-03-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3"
down_revision = "t1u3v5x7z890"
branch_labels = None
depends_on = None

# Tables that get review columns
_RESOURCE_TABLES = ("agents", "connectors", "knowledge_bases", "mcp_servers")

# Platform org UUID
_PLATFORM_ORG_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    bind = op.get_bind()
    from fim_one.migrations.helpers import table_exists, table_has_column

    # 1. Add require_publish_review to organizations
    if table_exists(bind, "organizations") and not table_has_column(
        bind, "organizations", "require_publish_review"
    ):
        op.add_column(
            "organizations",
            sa.Column(
                "require_publish_review",
                sa.Boolean,
                nullable=False,
                server_default=sa.text("FALSE"),
            ),
        )

    # 2. Set Platform org to require_publish_review = TRUE
    if table_exists(bind, "organizations") and table_has_column(
        bind, "organizations", "require_publish_review"
    ):
        bind.execute(
            sa.text(
                "UPDATE organizations SET require_publish_review = TRUE "
                "WHERE id = :org_id"
            ),
            {"org_id": _PLATFORM_ORG_ID},
        )

    # 3. Add review columns to all 4 resource tables
    for table_name in _RESOURCE_TABLES:
        if not table_exists(bind, table_name):
            continue

        if not table_has_column(bind, table_name, "publish_status"):
            op.add_column(
                table_name,
                sa.Column("publish_status", sa.String(20), nullable=True),
            )

        if not table_has_column(bind, table_name, "reviewed_by"):
            op.add_column(
                table_name,
                sa.Column("reviewed_by", sa.String(36), nullable=True),
            )

        if not table_has_column(bind, table_name, "reviewed_at"):
            op.add_column(
                table_name,
                sa.Column("reviewed_at", sa.DateTime, nullable=True),
            )

        if not table_has_column(bind, table_name, "review_note"):
            op.add_column(
                table_name,
                sa.Column("review_note", sa.Text, nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    from fim_one.migrations.helpers import table_exists, table_has_column

    # Remove review columns from resource tables
    for table_name in _RESOURCE_TABLES:
        if not table_exists(bind, table_name):
            continue
        with op.batch_alter_table(table_name) as batch_op:
            for col in ("publish_status", "reviewed_by", "reviewed_at", "review_note"):
                if table_has_column(bind, table_name, col):
                    batch_op.drop_column(col)

    # Remove require_publish_review from organizations
    if table_exists(bind, "organizations") and table_has_column(
        bind, "organizations", "require_publish_review"
    ):
        with op.batch_alter_table("organizations") as batch_op:
            batch_op.drop_column("require_publish_review")

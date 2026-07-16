"""recall shared knowledge bases

Knowledge bases are no longer shareable (Reduce Feature). This migration
destructively recalls every previously org/market-published KB back to
personal visibility and drops all KB resource subscriptions. Data recall
is one-way — downgrade is a no-op.

Revision ID: f7h9j1l3n567
Revises: e6g8i0k2m456
Create Date: 2026-07-08
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import table_exists, table_has_column

revision: str = "f7h9j1l3n567"
down_revision: Union[str, None] = "e6g8i0k2m456"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # ── 1. Recall all non-personal knowledge bases to personal ─────────────
    if table_exists(bind, "knowledge_bases") and table_has_column(
        bind, "knowledge_bases", "visibility"
    ):
        set_parts = ["visibility='personal'"]
        for col in ("org_id", "publish_status", "reviewed_by", "reviewed_at", "review_note"):
            if table_has_column(bind, "knowledge_bases", col):
                set_parts.append(f"{col}=NULL")
        op.execute(
            sa.text(
                "UPDATE knowledge_bases SET "
                + ", ".join(set_parts)
                + " WHERE visibility != 'personal'"
            )
        )

    # ── 2. Drop all KB subscriptions (no KB can be subscribed anymore) ─────
    if table_exists(bind, "resource_subscriptions"):
        op.execute(
            sa.text(
                "DELETE FROM resource_subscriptions "
                "WHERE resource_type='knowledge_base'"
            )
        )


def downgrade() -> None:
    # Data recall is one-way; nothing to restore.
    pass

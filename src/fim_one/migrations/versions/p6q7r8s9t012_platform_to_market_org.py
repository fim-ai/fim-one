"""platform to market org

Revision ID: p6q7r8s9t012
Revises: o5p6q7r8s901
Create Date: 2026-03-14
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import table_exists, table_has_column

revision: str = "p6q7r8s9t012"
down_revision: Union[str, None] = "o5p6q7r8s901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MARKET_ORG_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    bind = op.get_bind()

    # ── 1. Update Market org metadata ──────────────────────────────────────
    if table_exists(bind, "organizations"):
        # Build SET clause; include review_skills only if the column exists
        if table_has_column(bind, "organizations", "review_skills"):
            dialect = bind.dialect.name
            true_literal = "1" if dialect == "sqlite" else "TRUE"
            op.execute(
                sa.text(
                    "UPDATE organizations SET name='Market', slug='market', "
                    "description='Marketplace. Admin-managed, no membership required.', "
                    f"review_skills={true_literal} "
                    "WHERE id=:org_id"
                ).bindparams(org_id=MARKET_ORG_ID)
            )
        else:
            op.execute(
                sa.text(
                    "UPDATE organizations SET name='Market', slug='market', "
                    "description='Marketplace. Admin-managed, no membership required.' "
                    "WHERE id=:org_id"
                ).bindparams(org_id=MARKET_ORG_ID)
            )

    # ── 2. Delete non-owner memberships from Market org ────────────────────
    if table_exists(bind, "org_memberships"):
        op.execute(
            sa.text(
                "DELETE FROM org_memberships "
                "WHERE org_id=:org_id AND role != 'owner'"
            ).bindparams(org_id=MARKET_ORG_ID)
        )


def downgrade() -> None:
    pass

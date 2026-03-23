"""merge review and mcp_credentials heads

Revision ID: 655b0da054b4
Revises: d5e6f7a8b901, u1v3x5z7a890
Create Date: 2026-03-13 14:58:00.548199
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '655b0da054b4'
down_revision: tuple[str, str] = ('d5e6f7a8b901', 'u1v3x5z7a890')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

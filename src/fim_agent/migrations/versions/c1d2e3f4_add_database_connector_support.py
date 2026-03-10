"""add database connector support

Revision ID: c1d2e3f4
Revises: m3o5q7s9u012
Create Date: 2026-03-11 12:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from fim_agent.migrations.helpers import table_exists, table_has_column

# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4"
down_revision: Union[str, None] = "m3o5q7s9u012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # -- Add db_config column to connectors table --
    if table_exists(bind, "connectors") and not table_has_column(bind, "connectors", "db_config"):
        op.add_column("connectors", sa.Column("db_config", sa.JSON(), nullable=True))

    # -- Make base_url nullable (it may already be nullable) --
    # SQLite doesn't support ALTER COLUMN, but the column is already String(500)
    # and we need it nullable for database connectors that have no base_url.
    # For PG, we alter; for SQLite, it's effectively nullable already.
    if table_exists(bind, "connectors"):
        dialect = bind.dialect.name
        if dialect == "postgresql":
            op.alter_column("connectors", "base_url", existing_type=sa.String(500), nullable=True)

    # -- Create database_schemas table --
    if not table_exists(bind, "database_schemas"):
        op.create_table(
            "database_schemas",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "connector_id",
                sa.String(36),
                sa.ForeignKey("connectors.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("table_name", sa.String(200), nullable=False),
            sa.Column("display_name", sa.String(200), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column(
                "is_visible",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("TRUE"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )

    # -- Create schema_columns table --
    if not table_exists(bind, "schema_columns"):
        op.create_table(
            "schema_columns",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "schema_id",
                sa.String(36),
                sa.ForeignKey("database_schemas.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("column_name", sa.String(200), nullable=False),
            sa.Column("display_name", sa.String(200), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("data_type", sa.String(100), nullable=False),
            sa.Column(
                "is_nullable",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("TRUE"),
            ),
            sa.Column(
                "is_primary_key",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("FALSE"),
            ),
            sa.Column(
                "is_visible",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("TRUE"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("schema_columns")
    op.drop_table("database_schemas")
    op.drop_column("connectors", "db_config")
    # Restore base_url NOT NULL is risky if data exists — skip for safety

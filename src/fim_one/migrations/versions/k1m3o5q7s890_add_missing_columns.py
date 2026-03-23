"""add missing columns to users and agents tables

Columns that existed in ORM models but had no migration —
previously auto-created by metadata.create_all() on SQLite.

Uses individual op.add_column() instead of batch_alter_table to avoid
SQLAlchemy CircularDependencyError on SQLite table rebuild when many
columns already exist from metadata.create_all().

Revision ID: k1m3o5q7s890
Revises: j0l2n4p6r789
Create Date: 2026-03-10 16:00:00.000000
"""
from __future__ import annotations

from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa

from fim_one.migrations.helpers import index_exists, table_exists, table_has_column


# revision identifiers, used by Alembic.
revision: str = "k1m3o5q7s890"
down_revision: Union[str, None] = "j0l2n4p6r789"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # --- users table: add columns one by one (skip if already present) ---
    user_cols: list[tuple[str, sa.Column[Any]]] = [
        ("is_active", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE"))),
        ("tokens_invalidated_at", sa.Column("tokens_invalidated_at", sa.DateTime(), nullable=True)),
        ("token_quota", sa.Column("token_quota", sa.Integer(), nullable=True)),
        ("oauth_provider", sa.Column("oauth_provider", sa.String(20), nullable=True)),
        ("oauth_id", sa.Column("oauth_id", sa.String(255), nullable=True)),
        ("email", sa.Column("email", sa.String(255), nullable=False, server_default="")),
        ("avatar", sa.Column("avatar", sa.String(255), nullable=True)),
    ]
    for col_name, col_obj in user_cols:
        if not table_has_column(bind, "users", col_name):
            op.add_column("users", col_obj)

    # password_hash: NOT NULL -> nullable (for OAuth users).
    # On SQLite batch_alter_table would trigger a full table rebuild and
    # hit CircularDependencyError when many columns exist.  Use a PG-only
    # ALTER COLUMN; on SQLite the column is already nullable from
    # metadata.create_all().
    if bind.dialect.name != "sqlite":
        op.alter_column(
            "users", "password_hash",
            existing_type=sa.String(255),
            nullable=True,
        )

    # unique constraint for OAuth binding lookup — use create_index (not
    # create_unique_constraint) because SQLite doesn't support ALTER TABLE
    # ADD CONSTRAINT.  A unique index enforces the same uniqueness guarantee.
    if not index_exists(bind, "users", "uq_user_oauth"):
        op.create_index("uq_user_oauth", "users", ["oauth_provider", "oauth_id"], unique=True)

    # --- agents table: add columns one by one (skip if already present) ---
    agent_cols = [
        ("icon", sa.Column("icon", sa.String(100), nullable=True)),
        ("connector_ids", sa.Column("connector_ids", sa.JSON(), nullable=True)),
    ]
    for col_name, col_obj in agent_cols:
        if not table_has_column(bind, "agents", col_name):
            op.add_column("agents", col_obj)

    # --- Data migration: backfill oauth bindings ---
    if table_exists(bind, "user_oauth_bindings") and table_has_column(bind, "users", "oauth_provider"):
        result = bind.execute(sa.text("SELECT COUNT(*) FROM user_oauth_bindings"))
        count = result.scalar()
        if not count or count == 0:
            import uuid as _uuid
            result = bind.execute(sa.text(
                "SELECT id, oauth_provider, oauth_id, email, display_name "
                "FROM users WHERE oauth_provider IS NOT NULL AND oauth_id IS NOT NULL"
            ))
            for row in result:
                user_id, provider, oauth_id, email, display_name = row
                bind.execute(
                    sa.text(
                        "INSERT INTO user_oauth_bindings (id, user_id, provider, oauth_id, email, display_name) "
                        "VALUES (:id, :user_id, :provider, :oauth_id, :email, :display_name)"
                    ),
                    {"id": str(_uuid.uuid4()), "user_id": user_id, "provider": provider, "oauth_id": oauth_id, "email": email, "display_name": display_name},
                )

    # --- Data migration: backfill NULL emails with placeholder ---
    if table_has_column(bind, "users", "email"):
        bind.execute(sa.text(
            "UPDATE users SET email = username || '@change.me' WHERE email IS NULL OR email = ''"
        ))


def downgrade() -> None:
    bind = op.get_bind()

    # --- agents ---
    if table_has_column(bind, "agents", "connector_ids"):
        op.drop_column("agents", "connector_ids")
    if table_has_column(bind, "agents", "icon"):
        op.drop_column("agents", "icon")

    # --- users ---
    if index_exists(bind, "users", "uq_user_oauth"):
        op.drop_index("uq_user_oauth", table_name="users")

    if bind.dialect.name != "sqlite":
        op.alter_column(
            "users", "password_hash",
            existing_type=sa.String(255),
            nullable=False,
        )

    for col_name in ("avatar", "email", "oauth_id", "oauth_provider",
                     "token_quota", "tokens_invalidated_at", "is_active"):
        if table_has_column(bind, "users", col_name):
            op.drop_column("users", col_name)

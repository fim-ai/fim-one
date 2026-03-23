"""add resource visibility columns

Revision ID: o5q7s9u1w234
Revises: n4p6r8t0v123
Create Date: 2026-03-11 12:00:00.000000
"""
from __future__ import annotations

from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa

from fim_one.migrations.helpers import table_has_column, index_exists

revision: str = "o5q7s9u1w234"
down_revision: Union[str, None] = "n4p6r8t0v123"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ["agents", "connectors", "knowledge_bases", "mcp_servers"]


def upgrade() -> None:
    bind = op.get_bind()

    for table in _TABLES:
        # Add visibility column
        if not table_has_column(bind, table, "visibility"):
            op.add_column(
                table,
                sa.Column(
                    "visibility",
                    sa.String(20),
                    nullable=False,
                    server_default="personal",
                ),
            )

        # Add org_id column
        # SQLite cannot add a column with FK constraint via ALTER TABLE,
        # so only include the FK on PostgreSQL.
        if not table_has_column(bind, table, "org_id"):
            col_args: list[Any] = [sa.String(36)]
            if bind.dialect.name != "sqlite":
                col_args.append(sa.ForeignKey("organizations.id"))
            op.add_column(
                table,
                sa.Column("org_id", *col_args, nullable=True),
            )

        # Add indexes
        ix_vis = f"ix_{table}_visibility"
        if not index_exists(bind, table, ix_vis):
            op.create_index(ix_vis, table, ["visibility"])

        ix_org = f"ix_{table}_org_id"
        if not index_exists(bind, table, ix_org):
            op.create_index(ix_org, table, ["org_id"])

    # Backfill: existing is_global=True rows -> visibility='global'
    # Agents and MCP servers have is_global column
    for table in ["agents", "mcp_servers"]:
        if table_has_column(bind, table, "is_global"):
            bind.execute(
                sa.text(
                    f"UPDATE {table} SET visibility = 'global' "
                    f"WHERE is_global = :val AND visibility = 'personal'"
                ),
                {"val": True},
            )

    # Resolve legacy clones (user_id=NULL) for agents
    # If cloned_from_agent_id exists and source still exists:
    #   -> set source visibility='global', status='published', delete clone
    # If source is gone:
    #   -> assign to first admin, set visibility='global'
    if table_has_column(bind, "agents", "cloned_from_agent_id"):
        conn = bind
        # Find clones: user_id IS NULL AND is_global = TRUE
        clones = conn.execute(
            sa.text(
                "SELECT id, cloned_from_agent_id FROM agents "
                "WHERE user_id IS NULL AND is_global = :is_global"
            ),
            {"is_global": True},
        ).fetchall()

        for clone_id, source_id in clones:
            if source_id:
                # Check if source exists
                source = conn.execute(
                    sa.text("SELECT id FROM agents WHERE id = :id"),
                    {"id": source_id},
                ).fetchone()

                if source:
                    # Set source as globally visible
                    conn.execute(
                        sa.text(
                            "UPDATE agents SET visibility = 'global', "
                            "status = 'published', is_global = :is_global "
                            "WHERE id = :id"
                        ),
                        {"id": source_id, "is_global": True},
                    )
                    # Delete clone
                    conn.execute(
                        sa.text("DELETE FROM agents WHERE id = :id"),
                        {"id": clone_id},
                    )
                    continue

            # Source doesn't exist or no source_id -- assign to first admin
            admin = conn.execute(
                sa.text(
                    "SELECT id FROM users WHERE is_admin = :is_admin LIMIT 1"
                ),
                {"is_admin": True},
            ).fetchone()

            if admin:
                conn.execute(
                    sa.text(
                        "UPDATE agents SET user_id = :uid, visibility = 'global' "
                        "WHERE id = :id"
                    ),
                    {"uid": admin[0], "id": clone_id},
                )

    # Same for MCP servers
    if table_has_column(bind, "mcp_servers", "cloned_from_server_id"):
        conn = bind
        clones = conn.execute(
            sa.text(
                "SELECT id, cloned_from_server_id FROM mcp_servers "
                "WHERE user_id IS NULL AND is_global = :is_global"
            ),
            {"is_global": True},
        ).fetchall()

        for clone_id, source_id in clones:
            if source_id:
                source = conn.execute(
                    sa.text("SELECT id FROM mcp_servers WHERE id = :id"),
                    {"id": source_id},
                ).fetchone()

                if source:
                    conn.execute(
                        sa.text(
                            "UPDATE mcp_servers SET visibility = 'global', "
                            "is_global = :is_global WHERE id = :id"
                        ),
                        {"id": source_id, "is_global": True},
                    )
                    conn.execute(
                        sa.text("DELETE FROM mcp_servers WHERE id = :id"),
                        {"id": clone_id},
                    )
                    continue

            admin = conn.execute(
                sa.text(
                    "SELECT id FROM users WHERE is_admin = :is_admin LIMIT 1"
                ),
                {"is_admin": True},
            ).fetchone()

            if admin:
                conn.execute(
                    sa.text(
                        "UPDATE mcp_servers SET user_id = :uid, "
                        "visibility = 'global' WHERE id = :id"
                    ),
                    {"uid": admin[0], "id": clone_id},
                )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_index(f"ix_{table}_org_id", table_name=table)
        op.drop_index(f"ix_{table}_visibility", table_name=table)
        op.drop_column(table, "org_id")
        op.drop_column(table, "visibility")

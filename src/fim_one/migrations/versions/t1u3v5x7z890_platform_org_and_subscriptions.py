"""Platform org, resource subscriptions, discoverable agents, KB standalone conv.

Revision ID: t1u3v5x7z890
Revises: s9u1w3y5a678
Create Date: 2026-03-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "t1u3v5x7z890"
down_revision = "s9u1w3y5a678"
branch_labels = None
depends_on = None

PLATFORM_ORG_ID = "00000000-0000-0000-0000-000000000001"
PLATFORM_ORG_SLUG = "platform"


def upgrade() -> None:
    bind = op.get_bind()

    from fim_one.migrations.helpers import table_exists, table_has_column, index_exists

    # ── Phase A: Platform org ──────────────────────────────────────────────

    # 1. Find first admin user (or first user)
    # Users table uses is_admin boolean column (not a role column)
    dialect = bind.dialect.name
    true_literal = "1" if dialect == "sqlite" else "TRUE"
    admin_row = bind.execute(
        sa.text(f"SELECT id FROM users WHERE is_admin={true_literal} ORDER BY created_at ASC LIMIT 1")
    ).fetchone()
    if admin_row is None:
        admin_row = bind.execute(
            sa.text("SELECT id FROM users ORDER BY created_at ASC LIMIT 1")
        ).fetchone()

    if admin_row:
        admin_id = admin_row[0]
        # Create Platform org
        existing_org = bind.execute(
            sa.text("SELECT id FROM organizations WHERE id=:id"),
            {"id": PLATFORM_ORG_ID}
        ).fetchone()
        if not existing_org:
            import uuid as _uuid
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            bind.execute(
                sa.text(
                    "INSERT INTO organizations (id, name, slug, description, owner_id, is_active, created_at, updated_at) "
                    f"VALUES (:id, :name, :slug, :desc, :owner, {true_literal}, :now, :now)"
                ),
                {
                    "id": PLATFORM_ORG_ID,
                    "name": "Platform",
                    "slug": PLATFORM_ORG_SLUG,
                    "desc": "Default platform-wide organization. All users are members.",
                    "owner": admin_id,
                    "now": now,
                },
            )

        # 2. Enroll all existing users in Platform org
        # Admin gets owner role, others get member role
        all_users = bind.execute(
            sa.text("SELECT id FROM users ORDER BY created_at ASC")
        ).fetchall()
        for user_row in all_users:
            uid = user_row[0]
            member_role = "owner" if uid == admin_id else "member"
            existing_member = bind.execute(
                sa.text(
                    "SELECT id FROM org_memberships WHERE org_id=:org AND user_id=:uid"
                ),
                {"org": PLATFORM_ORG_ID, "uid": uid},
            ).fetchone()
            if not existing_member:
                import uuid as _uuid2
                from datetime import datetime as dt2, timezone as tz2
                mid = str(_uuid2.uuid4())
                now2 = dt2.now(tz2.utc).isoformat()
                bind.execute(
                    sa.text(
                        "INSERT INTO org_memberships (id, org_id, user_id, role, created_at, updated_at) "
                        "VALUES (:id, :org, :uid, :role, :now, :now)"
                    ),
                    {"id": mid, "org": PLATFORM_ORG_ID, "uid": uid, "role": member_role, "now": now2},
                )

    # ── Phase B: Convert global → Platform org ────────────────────────────

    for table in ("agents", "connectors", "knowledge_bases", "mcp_servers"):
        if table_exists(bind, table):
            # Check if visibility column exists before updating
            if table_has_column(bind, table, "visibility"):
                bind.execute(
                    sa.text(
                        f"UPDATE {table} SET visibility='org', org_id=:org "
                        "WHERE visibility='global'"
                    ),
                    {"org": PLATFORM_ORG_ID},
                )
            # Also set user_id for orphan records (user_id IS NULL)
            if admin_row and table_has_column(bind, table, "user_id"):
                bind.execute(
                    sa.text(
                        f"UPDATE {table} SET user_id=:uid WHERE user_id IS NULL"
                    ),
                    {"uid": admin_id},
                )

    # ── Phase C: Drop deprecated fields ──────────────────────────────────

    # agents — drop is_global, cloned_from_agent_id, cloned_from_user_id
    if table_exists(bind, "agents"):
        cols_to_drop_agents = []
        for col in ("is_global", "cloned_from_agent_id", "cloned_from_user_id"):
            if table_has_column(bind, "agents", col):
                cols_to_drop_agents.append(col)
        if cols_to_drop_agents:
            with op.batch_alter_table("agents") as batch_op:
                for col in cols_to_drop_agents:
                    batch_op.drop_column(col)

    # mcp_servers — drop is_global, cloned_from_server_id, cloned_from_user_id
    if table_exists(bind, "mcp_servers"):
        cols_to_drop_mcp = []
        for col in ("is_global", "cloned_from_server_id", "cloned_from_user_id"):
            if table_has_column(bind, "mcp_servers", col):
                cols_to_drop_mcp.append(col)
        if cols_to_drop_mcp:
            with op.batch_alter_table("mcp_servers") as batch_op:
                for col in cols_to_drop_mcp:
                    batch_op.drop_column(col)

    # ── Phase D: Add new fields ───────────────────────────────────────────

    # mcp_servers.allow_fallback
    if table_exists(bind, "mcp_servers") and not table_has_column(bind, "mcp_servers", "allow_fallback"):
        with op.batch_alter_table("mcp_servers") as batch_op:
            batch_op.add_column(
                sa.Column("allow_fallback", sa.Boolean(), nullable=False, server_default=sa.text("TRUE"))
            )

    # agents.discoverable
    if table_exists(bind, "agents") and not table_has_column(bind, "agents", "discoverable"):
        with op.batch_alter_table("agents") as batch_op:
            batch_op.add_column(
                sa.Column("discoverable", sa.Boolean(), nullable=False, server_default=sa.text("FALSE"))
            )

    # agents.sub_agent_ids
    if table_exists(bind, "agents") and not table_has_column(bind, "agents", "sub_agent_ids"):
        with op.batch_alter_table("agents") as batch_op:
            batch_op.add_column(sa.Column("sub_agent_ids", sa.JSON(), nullable=True))

    # conversations.kb_ids
    if table_exists(bind, "conversations") and not table_has_column(bind, "conversations", "kb_ids"):
        with op.batch_alter_table("conversations") as batch_op:
            batch_op.add_column(sa.Column("kb_ids", sa.JSON(), nullable=True))

    # ── Phase E: Create new tables ────────────────────────────────────────

    # mcp_server_credentials
    if not table_exists(bind, "mcp_server_credentials"):
        op.create_table(
            "mcp_server_credentials",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("server_id", sa.String(36), sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
            sa.Column("env_blob", sa.Text(), nullable=True),
            sa.Column("headers_blob", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("server_id", "user_id", name="uq_mcp_server_user_credential"),
        )
        if not index_exists(bind, "mcp_server_credentials", "ix_mcp_server_credentials_server_id"):
            op.create_index("ix_mcp_server_credentials_server_id", "mcp_server_credentials", ["server_id"])
        if not index_exists(bind, "mcp_server_credentials", "ix_mcp_server_credentials_user_id"):
            op.create_index("ix_mcp_server_credentials_user_id", "mcp_server_credentials", ["user_id"])

    # resource_subscriptions
    if not table_exists(bind, "resource_subscriptions"):
        op.create_table(
            "resource_subscriptions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("resource_type", sa.String(30), nullable=False),
            sa.Column("resource_id", sa.String(36), nullable=False),
            sa.Column("org_id", sa.String(36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("user_id", "resource_type", "resource_id", name="uq_resource_subscription"),
        )
        if not index_exists(bind, "resource_subscriptions", "ix_resource_subscriptions_user_id"):
            op.create_index("ix_resource_subscriptions_user_id", "resource_subscriptions", ["user_id"])
        if not index_exists(bind, "resource_subscriptions", "ix_resource_subscriptions_resource_id"):
            op.create_index("ix_resource_subscriptions_resource_id", "resource_subscriptions", ["resource_id"])
        if not index_exists(bind, "resource_subscriptions", "ix_resource_subscriptions_org_id"):
            op.create_index("ix_resource_subscriptions_org_id", "resource_subscriptions", ["org_id"])

    # ── Phase F: Migrate existing org resources to subscriptions ─────────

    if table_exists(bind, "resource_subscriptions"):
        resource_map = {
            "agents": "agent",
            "connectors": "connector",
            "knowledge_bases": "knowledge_base",
            "mcp_servers": "mcp_server",
        }
        import uuid as _uuid3
        from datetime import datetime as dt3, timezone as tz3

        for table, rtype in resource_map.items():
            if not table_exists(bind, table):
                continue
            if not table_has_column(bind, table, "visibility") or not table_has_column(bind, table, "org_id"):
                continue
            # Get all org-visible resources with their org
            rows = bind.execute(
                sa.text(
                    f"SELECT id, user_id, org_id FROM {table} "
                    "WHERE visibility='org' AND org_id IS NOT NULL"
                )
            ).fetchall()
            for row in rows:
                res_id, owner_id, org_id = row[0], row[1], row[2]
                if not owner_id or not org_id:
                    continue
                # Get all members of this org (excluding the owner)
                members = bind.execute(
                    sa.text(
                        "SELECT user_id FROM org_memberships "
                        "WHERE org_id=:org AND user_id != :owner"
                    ),
                    {"org": org_id, "owner": owner_id},
                ).fetchall()
                for member_row in members:
                    member_uid = member_row[0]
                    # Check if subscription already exists
                    exists = bind.execute(
                        sa.text(
                            "SELECT id FROM resource_subscriptions "
                            "WHERE user_id=:uid AND resource_type=:rt AND resource_id=:rid"
                        ),
                        {"uid": member_uid, "rt": rtype, "rid": res_id},
                    ).fetchone()
                    if not exists:
                        sub_id = str(_uuid3.uuid4())
                        now3 = dt3.now(tz3.utc).isoformat()
                        bind.execute(
                            sa.text(
                                "INSERT INTO resource_subscriptions "
                                "(id, user_id, resource_type, resource_id, org_id, created_at) "
                                "VALUES (:id, :uid, :rt, :rid, :org, :now)"
                            ),
                            {
                                "id": sub_id,
                                "uid": member_uid,
                                "rt": rtype,
                                "rid": res_id,
                                "org": org_id,
                                "now": now3,
                            },
                        )


def downgrade() -> None:
    """Downgrade is not supported for this migration."""
    pass

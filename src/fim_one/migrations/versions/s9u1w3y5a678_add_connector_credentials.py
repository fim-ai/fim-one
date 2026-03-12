"""Add connector_credentials table and allow_fallback column.

Revision ID: s9u1w3y5a678
Revises: r8t0v2x4z567
Create Date: 2026-03-12
"""

from __future__ import annotations

import json
import sqlalchemy as sa
from alembic import op

revision = "s9u1w3y5a678"
down_revision = "r8t0v2x4z567"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    from fim_one.migrations.helpers import index_exists, table_exists, table_has_column

    # 1. Create connector_credentials table
    if not table_exists(bind, "connector_credentials"):
        op.create_table(
            "connector_credentials",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "connector_id",
                sa.String(36),
                sa.ForeignKey("connectors.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("credentials_blob", sa.Text, nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime,
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
            ),
            sa.Column("updated_at", sa.DateTime, nullable=True),
            sa.UniqueConstraint(
                "connector_id", "user_id", name="uq_connector_user_credential"
            ),
        )

    # 2. Create indexes
    if not index_exists(
        bind, "connector_credentials", "ix_connector_credentials_connector_id"
    ):
        op.create_index(
            "ix_connector_credentials_connector_id",
            "connector_credentials",
            ["connector_id"],
        )
    if not index_exists(
        bind, "connector_credentials", "ix_connector_credentials_user_id"
    ):
        op.create_index(
            "ix_connector_credentials_user_id",
            "connector_credentials",
            ["user_id"],
        )

    # 3. Add allow_fallback column to connectors
    if not table_has_column(bind, "connectors", "allow_fallback"):
        op.add_column(
            "connectors",
            sa.Column(
                "allow_fallback",
                sa.Boolean,
                nullable=False,
                server_default=sa.text("TRUE"),
            ),
        )

    # 4. Data migration: extract sensitive fields from existing auth_config
    #    into connector_credentials rows
    from fim_one.core.security.encryption import encrypt_credential
    import uuid
    from datetime import datetime

    result = bind.execute(
        sa.text("SELECT id, auth_type, auth_config FROM connectors")
    )
    rows = result.fetchall()

    for row in rows:
        connector_id = row[0]
        auth_type = row[1]
        auth_config_raw = row[2]

        if not auth_config_raw or not auth_type:
            continue

        # Parse auth_config (may be JSON string or dict depending on DB driver)
        if isinstance(auth_config_raw, str):
            try:
                auth_config = json.loads(auth_config_raw)
            except Exception:
                continue
        elif isinstance(auth_config_raw, dict):
            auth_config = auth_config_raw
        else:
            continue

        cred_blob: dict = {}
        if auth_type == "bearer":
            token = auth_config.get("default_token")
            if token:
                cred_blob["default_token"] = token
        elif auth_type == "api_key":
            key = auth_config.get("default_api_key")
            if key:
                cred_blob["default_api_key"] = key
        elif auth_type == "basic":
            username = auth_config.get("default_username")
            password = auth_config.get("default_password")
            if username:
                cred_blob["default_username"] = username
            if password:
                cred_blob["default_password"] = password

        if not cred_blob:
            continue

        # Check if default credential row already exists (idempotent)
        existing = bind.execute(
            sa.text(
                "SELECT id FROM connector_credentials "
                "WHERE connector_id = :cid AND user_id IS NULL"
            ),
            {"cid": connector_id},
        ).fetchone()

        if existing is None:
            encrypted = encrypt_credential(cred_blob)
            now = datetime.utcnow().isoformat()
            bind.execute(
                sa.text(
                    "INSERT INTO connector_credentials "
                    "(id, connector_id, user_id, credentials_blob, created_at) "
                    "VALUES (:id, :cid, NULL, :blob, :now)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "cid": connector_id,
                    "blob": encrypted,
                    "now": now,
                },
            )

    # 5. Cleanup: remove sensitive fields from auth_config JSON
    result2 = bind.execute(
        sa.text("SELECT id, auth_type, auth_config FROM connectors")
    )
    rows2 = result2.fetchall()

    for row in rows2:
        connector_id = row[0]
        auth_config_raw = row[2]

        if not auth_config_raw:
            continue

        if isinstance(auth_config_raw, str):
            try:
                auth_config = json.loads(auth_config_raw)
            except Exception:
                continue
        elif isinstance(auth_config_raw, dict):
            auth_config = dict(auth_config_raw)
        else:
            continue

        fields_to_remove = [
            "default_token",
            "default_api_key",
            "default_username",
            "default_password",
        ]
        changed = False
        for f in fields_to_remove:
            if f in auth_config:
                del auth_config[f]
                changed = True

        if changed:
            bind.execute(
                sa.text("UPDATE connectors SET auth_config = :cfg WHERE id = :id"),
                {"cfg": json.dumps(auth_config), "id": connector_id},
            )


def downgrade() -> None:
    op.drop_table("connector_credentials")
    bind = op.get_bind()
    from fim_one.migrations.helpers import table_has_column

    if table_has_column(bind, "connectors", "allow_fallback"):
        with op.batch_alter_table("connectors") as batch_op:
            batch_op.drop_column("allow_fallback")

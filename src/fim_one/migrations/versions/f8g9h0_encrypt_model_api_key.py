"""Encrypt ModelConfig.api_key at rest using Fernet.

Changes column type from String(500) to Text for model_configs.api_key
(EncryptedString TypeDecorator stores Fernet ciphertext as plain Text),
then encrypts all existing plaintext values in-place.

Revision ID: f8g9h0a1b2c3
Revises: e7f8g9a1b2c3
Create Date: 2026-03-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from fim_one.migrations.helpers import table_exists

revision = "f8g9h0a1b2c3"
down_revision = "e7f8g9a1b2c3"
branch_labels = None
depends_on = None


def _encrypt_value(value: str | None) -> str | None:
    """Encrypt a plaintext API key; skip if already encrypted or empty."""
    if not value:
        return value
    # Fernet tokens start with 'gAAAAA'; skip if already encrypted
    if value.startswith("gAAAAA"):
        return value
    try:
        from fim_one.core.security.encryption import encrypt_string
        return encrypt_string(value)
    except Exception:
        return value


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if not table_exists(bind, "model_configs"):
        return

    # 1. Change column type from String(500) to Text (for PG)
    #    SQLite: String is stored as TEXT affinity, so this is effectively a no-op
    if dialect != "sqlite":
        with op.batch_alter_table("model_configs") as batch_op:
            batch_op.alter_column(
                "api_key",
                type_=sa.Text(),
                existing_type=sa.String(500),
                existing_nullable=True,
            )

    # 2. Encrypt existing plaintext api_key values in-place
    rows = bind.execute(
        sa.text("SELECT id, api_key FROM model_configs WHERE api_key IS NOT NULL")
    ).fetchall()
    for row_id, api_key_val in rows:
        new_val = _encrypt_value(api_key_val)
        if new_val != api_key_val:
            bind.execute(
                sa.text("UPDATE model_configs SET api_key = :val WHERE id = :id"),
                {"val": new_val, "id": row_id},
            )


def downgrade() -> None:
    # Downgrade is lossy — decrypt_string fallback handles legacy plaintext,
    # so downgrade is safe to leave as a no-op.
    pass

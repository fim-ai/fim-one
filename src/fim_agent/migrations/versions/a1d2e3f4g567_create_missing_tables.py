"""create missing tables for PostgreSQL deployment

All 15 tables that were previously auto-created by metadata.create_all()
but had no Alembic migration.  Inserted between a1c2d3e4f567 and
b2d4e6f8a901 so that connector_call_logs can reference connectors.

Revision ID: a1d2e3f4g567
Revises: a1c2d3e4f567
Create Date: 2026-03-10 15:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1d2e3f4g567"
down_revision: Union[str, None] = "a1c2d3e4f567"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── tables with NO foreign keys ──────────────────────────────────

    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(100), primary_key=True, nullable=False),
        sa.Column("value", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "email_verifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, index=True),
        sa.Column("code", sa.String(6), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False, server_default="register"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "invite_codes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(16), nullable=False, unique=True, index=True),
        sa.Column("created_by_id", sa.String(36), nullable=False),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("max_uses", sa.Integer, nullable=False, server_default="1"),
        sa.Column("use_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("admin_id", sa.String(36), nullable=False, index=True),
        sa.Column("admin_username", sa.String(50), nullable=False),
        sa.Column("action", sa.String(100), nullable=False, index=True),
        sa.Column("target_type", sa.String(50), nullable=True),
        sa.Column("target_id", sa.String(36), nullable=True),
        sa.Column("target_label", sa.String(255), nullable=True),
        sa.Column("detail", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=True, index=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("key_prefix", sa.String(8), nullable=False),
        sa.Column("key_hash", sa.String(255), nullable=False, unique=True),
        sa.Column("scopes", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_requests", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "sensitive_words",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("word", sa.String(100), nullable=False, unique=True, index=True),
        sa.Column("category", sa.String(50), nullable=False, server_default="general"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
        sa.Column("created_by_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "ip_rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ip_address", sa.String(45), nullable=False, index=True),
        sa.Column("rule_type", sa.String(10), nullable=False),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
        sa.Column("created_by_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "announcements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("level", sa.String(20), nullable=False, server_default="info"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("target_group", sa.String(50), nullable=True),
        sa.Column("created_by_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "login_history",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=True, index=True),
        sa.Column("username", sa.String(50), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("success", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
        sa.Column("failure_reason", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── tables with FK to users ──────────────────────────────────────

    op.create_table(
        "connectors",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("icon", sa.String(100), nullable=True),
        sa.Column("type", sa.String(20), server_default="api"),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("auth_type", sa.String(20), server_default="none"),
        sa.Column("auth_config", sa.JSON, nullable=True),
        sa.Column("status", sa.String(20), server_default="published"),
        sa.Column("is_official", sa.Boolean, server_default=sa.text("FALSE")),
        sa.Column("forked_from", sa.String(36), nullable=True),
        sa.Column("version", sa.Integer, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("chunk_strategy", sa.String(20), server_default="recursive"),
        sa.Column("chunk_size", sa.Integer, server_default="1000"),
        sa.Column("chunk_overlap", sa.Integer, server_default="200"),
        sa.Column("retrieval_mode", sa.String(20), server_default="hybrid"),
        sa.Column("document_count", sa.Integer, server_default="0"),
        sa.Column("total_chunks", sa.Integer, server_default="0"),
        sa.Column("status", sa.String(20), server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("transport", sa.String(20), server_default="stdio"),
        sa.Column("command", sa.String(500), nullable=True),
        sa.Column("args", sa.JSON, nullable=True),
        sa.Column("env", sa.JSON, nullable=True),
        sa.Column("url", sa.String(500), nullable=True),
        sa.Column("working_dir", sa.String(500), nullable=True),
        sa.Column("headers", sa.JSON, nullable=True),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("TRUE")),
        sa.Column("tool_count", sa.Integer, server_default="0"),
        sa.Column("is_global", sa.Boolean, nullable=False, server_default=sa.text("FALSE")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "user_oauth_bindings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("oauth_id", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("display_name", sa.String(100), nullable=True),
        sa.Column("bound_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("provider", "oauth_id", name="uq_oauth_binding"),
        sa.UniqueConstraint("user_id", "provider", name="uq_user_provider"),
    )

    # ── tables with FK to connectors / knowledge_bases ───────────────

    op.create_table(
        "connector_actions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("connector_id", sa.String(36), sa.ForeignKey("connectors.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("method", sa.String(10), server_default="GET"),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("parameters_schema", sa.JSON, nullable=True),
        sa.Column("request_body_template", sa.JSON, nullable=True),
        sa.Column("response_extract", sa.String(200), nullable=True),
        sa.Column("requires_confirmation", sa.Boolean, server_default=sa.text("FALSE")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "kb_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kb_id", sa.String(36), sa.ForeignKey("knowledge_bases.id"), nullable=False, index=True),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("file_path", sa.String(1000), nullable=False),
        sa.Column("file_size", sa.Integer, server_default="0"),
        sa.Column("file_type", sa.String(20), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("chunk_count", sa.Integer, server_default="0"),
        sa.Column("status", sa.String(20), server_default="processing"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("kb_documents")
    op.drop_table("connector_actions")
    op.drop_table("user_oauth_bindings")
    op.drop_table("mcp_servers")
    op.drop_table("knowledge_bases")
    op.drop_table("connectors")
    op.drop_table("login_history")
    op.drop_table("announcements")
    op.drop_table("ip_rules")
    op.drop_table("sensitive_words")
    op.drop_table("api_keys")
    op.drop_table("invite_codes")
    op.drop_table("audit_logs")
    op.drop_table("email_verifications")
    op.drop_table("system_settings")

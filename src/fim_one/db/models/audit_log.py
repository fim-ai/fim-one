"""AuditLog ORM model: the general audit event table."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from fim_one.db.base import Base, TimestampMixin, UUIDPKMixin


class AuditLog(UUIDPKMixin, TimestampMixin, Base):
    """One security-relevant event: who did what to which thing, and how it ended.

    Written only by :mod:`fim_one.core.audit.writer`. No column is a
    foreign key: a record outlives the user, organization and resource it
    names. The table began as the platform administrators' log, which is
    why the actor's columns are still called ``admin_id`` and
    ``admin_username`` in the database; rows from then leave the columns
    added since empty, and read as an administrator's successful action.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_org_id_created_at", "org_id", "created_at"),
        Index("ix_audit_logs_target", "target_type", "target_id"),
    )

    # Who acted: the user's id (empty for the system and for an anonymous
    # caller) and their username or email at the time.
    actor_id: Mapped[str | None] = mapped_column("admin_id", String(36), nullable=True, index=True)
    actor_label: Mapped[str] = mapped_column("admin_username", String(255), nullable=False)
    # ``user`` / ``admin`` / ``system`` / ``anonymous`` / ``external``.
    actor_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # How the actor was authenticated (``core.assembly.principal.AuthMethod``)
    # and with what: an API key's id or a trigger key's prefix, never the key.
    auth_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    credential_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)

    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # Whose organization the thing acted on belongs to.
    org_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    target_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # ``success`` / ``denied`` / ``failed``.
    outcome: Mapped[str] = mapped_column(
        String(20), nullable=False, default="success", server_default="success"
    )
    # {"role": {"from": "member", "to": "admin"}}; a secret field says only
    # {"changed": true}.
    changes: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Where the request came from. Personal data: shown to platform
    # administrators and to the actor, not to organization administrators.
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # What the event happened in.
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    workflow_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

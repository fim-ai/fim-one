"""Channel and ConfirmationRequest ORM models.

A **Channel** is an org-scoped outbound messaging integration (Feishu / WeCom /
Slack / ...).  Sensitive credentials (app_secret, verification_token,
encrypt_key) are encrypted at-rest via ``EncryptedJSON`` so the raw values
never sit on disk in plaintext.

A **ConfirmationRequest** is a pending human-in-the-loop gate record created
by ``FeishuGateHook`` when a tool marked ``requires_confirmation=True`` is
about to run.  The hook blocks until the callback endpoint flips the status
to ``approved`` or ``rejected``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from fim_one.core.security.encryption import EncryptedJSON
from fim_one.db.base import Base, TimestampMixin, UUIDPKMixin


__all__ = ["Channel", "ConfirmationRequest"]


class Channel(UUIDPKMixin, TimestampMixin, Base):
    """An org-scoped outbound messaging channel (Feishu / WeCom / Slack).

    ``config`` is an ``EncryptedJSON`` column that stores provider-specific
    secrets (e.g. Feishu's ``app_id``/``app_secret``/``chat_id``/
    ``verification_token``/``encrypt_key``).  The encryption is transparent:
    the ORM returns a plain ``dict`` on read.
    """

    __tablename__ = "channels"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    org_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Encrypted-at-rest provider config (app_id/app_secret/chat_id/tokens).
    config: Mapped[dict[str, Any]] = mapped_column(EncryptedJSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=sa.text("TRUE"),
    )
    created_by: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class ConfirmationRequest(UUIDPKMixin, TimestampMixin, Base):
    """A pending human-in-the-loop approval for a tool call.

    Created by ``FeishuGateHook`` before a sensitive tool is invoked.  The
    callback endpoint (``/api/channels/{id}/callback``) flips ``status`` to
    ``approved`` or ``rejected`` when the card button is clicked; the hook
    polls until the terminal state is reached (or ``expired`` by timeout).
    """

    __tablename__ = "confirmation_requests"

    tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Delivery mode for this request: "channel" (pushed to a messaging channel,
    # the original behaviour) or "inline" (surfaced in the frontend). Existing
    # rows are backfilled to "channel" in the Phase 1 migration.
    mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="channel",
        server_default=sa.text("'channel'"),
    )
    # Optional explicit approver. When set, only this user's approval counts;
    # otherwise the Agent's confirmation_approver_scope governs eligibility.
    approver_user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    org_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Nullable since v0.9: inline-mode requests do NOT go through a
    # messaging channel, so no ``Channel`` row exists to point at.
    # Channel-mode requests still set this column.
    channel_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # pending / approved / rejected / expired
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(
        sa.JSON, nullable=True
    )
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    responded_by_open_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )

"""Agent ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fim_one.db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from .conversation import Conversation
    from .user import User


class Agent(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "agents"

    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True, index=True
    )
    is_builder: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="FALSE"
    )
    visibility: Mapped[str] = mapped_column(
        String(20), nullable=False, default="personal", server_default="personal"
    )
    org_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    icon: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_mode: Mapped[str] = mapped_column(String(20), default="react")
    model_config_json: Any = Column(JSON, nullable=True)
    tool_categories: Any = Column(JSON, nullable=True)
    suggested_prompts: Any = Column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    kb_ids: Any = Column(JSON, nullable=True)
    connector_ids: Any = Column(JSON, nullable=True)
    mcp_server_ids: Any = Column(JSON, nullable=True)  # list[str]
    # Deprecated: use Skill.resource_refs to bind Skills → Agents instead.
    # Kept for backward compatibility (dependency_analyzer reads it for existing data).
    skill_ids: Any = Column(JSON, nullable=True)  # list[str]
    compact_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    grounding_config: Any = Column(JSON, nullable=True)
    sandbox_config: Any = Column(JSON, nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default=sa.text("TRUE")
    )

    # --- Human-in-the-loop approval routing (Phase 1) -----------------------
    # confirmation_mode: "auto" (infer from connector+channel), "inline_only"
    # (always surface an in-app approval card), "channel_only" (always push
    # to approval_channel_id; fail if none bound).
    confirmation_mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="auto",
        server_default=sa.text("'auto'"),
    )
    # confirmation_approver_scope: who can approve.
    #   "initiator"    — only the user who triggered the tool call
    #   "agent_owner"  — only the agent owner (user_id)
    #   "org_members"  — any member of the agent's org
    confirmation_approver_scope: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="initiator",
        server_default=sa.text("'initiator'"),
    )
    # If True, every tool call goes through the approval gate regardless of
    # the connector's requires_confirmation flag.
    require_confirmation_for_all: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=sa.text("FALSE"),
    )
    # Explicit channel binding for "channel" confirmation mode. Nullable; when
    # NULL the hook falls back to inline (if allowed) or fails closed.
    approval_channel_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("channels.id", ondelete="SET NULL"),
        nullable=True,
    )

    forked_from: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # Publish review fields
    publish_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User | None] = relationship(back_populates="agents", lazy="raise")
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="agent", lazy="raise", passive_deletes=True
    )

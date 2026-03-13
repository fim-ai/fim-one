"""Agent ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

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
    is_global: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="FALSE"
    )
    is_builder: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="FALSE"
    )
    cloned_from_agent_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    cloned_from_user_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
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
    grounding_config: Any = Column(JSON, nullable=True)
    sandbox_config: Any = Column(JSON, nullable=True)

    # Publish review fields
    publish_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User | None] = relationship(back_populates="agents", lazy="raise")
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="agent", lazy="raise", passive_deletes=True
    )

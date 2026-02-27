"""Agent ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fim_agent.db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from .conversation import Conversation
    from .user import User


class Agent(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "agents"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_mode: Mapped[str] = mapped_column(String(20), default="react")
    model_config_json: Any = Column(JSON, nullable=True)
    tool_categories: Any = Column(JSON, nullable=True)
    suggested_prompts: Any = Column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    kb_ids: Any = Column(JSON, nullable=True)
    grounding_config: Any = Column(JSON, nullable=True)

    user: Mapped[User] = relationship(back_populates="agents")
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="agent", lazy="selectin"
    )

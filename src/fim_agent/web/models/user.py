"""User ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fim_agent.db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from .agent import Agent
    from .conversation import Conversation
    from .knowledge_base import KnowledgeBase
    from .model_config import ModelConfig


class User(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    refresh_token: Mapped[str | None] = mapped_column(String(500), nullable=True)
    refresh_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    conversations: Mapped[list[Conversation]] = relationship(back_populates="user", lazy="selectin")
    agents: Mapped[list[Agent]] = relationship(back_populates="user", lazy="selectin")
    knowledge_bases: Mapped[list[KnowledgeBase]] = relationship(
        back_populates="user", lazy="selectin"
    )
    model_configs: Mapped[list[ModelConfig]] = relationship(back_populates="user", lazy="selectin")

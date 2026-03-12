"""Conversation ORM model."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, Column, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fim_one.db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from .agent import Agent
    from .message import Message
    from .user import User


class Conversation(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "conversations"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="")
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("agents.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    starred: Mapped[bool] = mapped_column(Boolean, default=False)
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    fast_llm_tokens: Mapped[int] = mapped_column(Integer, default=0)
    kb_ids: Any = Column(JSON, nullable=True)  # list[str] — standalone KB mode

    user: Mapped[User] = relationship(back_populates="conversations", lazy="raise")
    agent: Mapped[Agent | None] = relationship(back_populates="conversations", lazy="raise")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        lazy="raise",
    )

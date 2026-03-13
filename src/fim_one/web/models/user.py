"""User ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fim_one.db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from .agent import Agent
    from .connector import Connector
    from .conversation import Conversation
    from .knowledge_base import KnowledgeBase
    from .mcp_server import MCPServer
    from .model_config import ModelConfig
    from .oauth_binding import UserOAuthBinding
    from .skill import Skill
    from .workflow import Workflow


class User(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("oauth_provider", "oauth_id", name="uq_user_oauth"),
    )

    username: Mapped[str | None] = mapped_column(String(50), unique=True, index=True, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(50), nullable=True, default=None)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="TRUE")
    refresh_token: Mapped[str | None] = mapped_column(String(500), nullable=True)
    refresh_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tokens_invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    token_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    system_instructions: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    preferred_language: Mapped[str] = mapped_column(
        String(10), nullable=False, default="auto", server_default="auto"
    )
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="FALSE")
    oauth_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    oauth_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    avatar: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    username_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="user", lazy="raise", cascade="all, delete-orphan"
    )
    agents: Mapped[list[Agent]] = relationship(
        back_populates="user", lazy="raise", cascade="all, delete-orphan"
    )
    knowledge_bases: Mapped[list[KnowledgeBase]] = relationship(
        back_populates="user", lazy="raise", cascade="all, delete-orphan"
    )
    model_configs: Mapped[list[ModelConfig]] = relationship(
        back_populates="user", lazy="raise", cascade="all, delete-orphan"
    )
    connectors: Mapped[list[Connector]] = relationship(
        back_populates="user", lazy="raise", cascade="all, delete-orphan"
    )
    mcp_servers: Mapped[list[MCPServer]] = relationship(
        back_populates="user", lazy="raise", cascade="all, delete-orphan"
    )
    oauth_bindings: Mapped[list[UserOAuthBinding]] = relationship(
        back_populates="user", lazy="raise", cascade="all, delete-orphan"
    )
    workflows: Mapped[list[Workflow]] = relationship(
        back_populates="user", lazy="raise", cascade="all, delete-orphan"
    )
    skills: Mapped[list[Skill]] = relationship(
        back_populates="user", lazy="raise", cascade="all, delete-orphan"
    )

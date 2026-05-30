"""User ORM model."""

from __future__ import annotations

import sqlalchemy as sa
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fim_one.core.security.encryption import EncryptedString
from fim_one.db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from .agent import Agent
    from .billing import BillingPlan, Subscription
    from .connector import Connector
    from .conversation import Conversation
    from .knowledge_base import KnowledgeBase
    from .mcp_server import MCPServer
    from .model_config import ModelConfig
    from .notification_preference import NotificationPreference
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
    privacy_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Personal settings
    timezone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    default_agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    default_exec_mode: Mapped[str | None] = mapped_column(String(10), nullable=True)
    default_reasoning: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Two-factor authentication
    totp_secret: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=sa.text("FALSE")
    )
    totp_backup_codes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Stripe billing (v1 MVP). ``token_quota`` above stays as the legacy
    # admin-override knob; v2 will migrate that semantic into the plan layer.
    # ``tokens_used_this_period`` is a soft counter reset on Stripe's
    # ``invoice.payment_succeeded`` webhook; ``quota_reset_at`` mirrors the
    # current Subscription's ``current_period_end`` for fast read paths.
    stripe_customer_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    tokens_used_this_period: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    quota_reset_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    plan_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("billing_plans.id"), nullable=True, index=True
    )

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
    notification_preferences: Mapped[list[NotificationPreference]] = relationship(
        back_populates="user", lazy="raise", cascade="all, delete-orphan"
    )
    # One-to-one with Subscription (v1 MVP allows at most one active sub
    # per user). ``uselist=False`` projects the collection as a single row.
    subscription: Mapped[Subscription | None] = relationship(
        "Subscription",
        back_populates="user",
        lazy="raise",
        uselist=False,
        cascade="all, delete-orphan",
    )
    plan: Mapped[BillingPlan | None] = relationship(
        "BillingPlan", lazy="joined", foreign_keys=[plan_id]
    )

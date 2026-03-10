"""UserOAuthBinding ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fim_agent.db.base import Base, UUIDPKMixin

if TYPE_CHECKING:
    from .user import User


class UserOAuthBinding(UUIDPKMixin, Base):
    __tablename__ = "user_oauth_bindings"
    __table_args__ = (
        UniqueConstraint("provider", "oauth_id", name="uq_oauth_binding"),
        UniqueConstraint("user_id", "provider", name="uq_user_provider"),
    )

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    oauth_id: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="oauth_bindings", lazy="raise")

"""InviteCode ORM model."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from fim_agent.db.base import Base, TimestampMixin, UUIDPKMixin

__all__ = ["InviteCode"]


class InviteCode(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "invite_codes"

    code: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(36), nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    max_uses: Mapped[int] = mapped_column(Integer, default=1, nullable=False, server_default="1")
    use_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, server_default="TRUE")

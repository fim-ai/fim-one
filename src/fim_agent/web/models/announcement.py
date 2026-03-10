"""Announcement ORM model — multiple scheduled announcements."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from fim_agent.db.base import Base, TimestampMixin, UUIDPKMixin


class Announcement(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "announcements"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False, default="info")  # info, warning, error
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    target_group: Mapped[str | None] = mapped_column(String(50), nullable=True)  # null=all, "admin", etc.
    created_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

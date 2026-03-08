"""SensitiveWord ORM model — content moderation word list."""

from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from fim_agent.db.base import Base, TimestampMixin, UUIDPKMixin


class SensitiveWord(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "sensitive_words"

    word: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False, default="general")
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="warn")  # warn, block
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

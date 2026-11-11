"""Knowledge Base and KB Document ORM models."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fim_one.db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from .user import User

VISION_SKIPPED_PREFIX = "vision_skipped:"


class KnowledgeBase(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_bases"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_strategy: Mapped[str] = mapped_column(String(20), default="recursive")
    chunk_size: Mapped[int] = mapped_column(Integer, default=1000)
    chunk_overlap: Mapped[int] = mapped_column(Integer, default=200)
    retrieval_mode: Mapped[str] = mapped_column(String(20), default="hybrid")
    document_count: Mapped[int] = mapped_column(Integer, default=0)
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="active")
    visibility: Mapped[str] = mapped_column(
        String(20), nullable=False, default="personal", server_default="personal"
    )
    org_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=True, index=True
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default=sa.text("TRUE")
    )

    # Publish review fields
    publish_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship(back_populates="knowledge_bases", lazy="raise")
    documents: Mapped[list[KBDocument]] = relationship(
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
        lazy="raise",
    )


class KBDocument(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "kb_documents"

    kb_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("knowledge_bases.id"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="processing")
    # On a failed document, why it failed. On a ready document it can
    # instead hold ``vision_skipped:<refusal code>``: the document was read
    # without the vision model because the quota check refused it (see
    # ``vision_notice``).
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="documents", lazy="raise")

    def mark_vision_skipped(self, refusal_code: str | None) -> None:
        """Record on a ready document whether the vision model was left out, and why.

        ``None`` clears the record (the document was read with the vision
        model, or no vision model would have been used).
        """
        self.error_message = f"{VISION_SKIPPED_PREFIX}{refusal_code}" if refusal_code else None

    @property
    def vision_notice(self) -> str | None:
        """The refusal code under which a ready document was read without the vision model."""
        if (
            self.status == "ready"
            and self.error_message
            and self.error_message.startswith(VISION_SKIPPED_PREFIX)
        ):
            return self.error_message[len(VISION_SKIPPED_PREFIX) :]
        return None

    @property
    def failure_message(self) -> str | None:
        """``error_message`` without the vision record: what the API shows as the error."""
        return None if self.vision_notice else self.error_message

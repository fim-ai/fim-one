"""Knowledge Base and KB Document ORM models."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Column, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fim_agent.db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from .user import User


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

    user: Mapped[User] = relationship(back_populates="knowledge_bases")
    documents: Mapped[list[KBDocument]] = relationship(
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
        lazy="selectin",
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
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="documents")

"""MCPServer ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fim_one.db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from .user import User

__all__ = ["MCPServer"]


class MCPServer(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "mcp_servers"

    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    transport: Mapped[str] = mapped_column(String(20), default="stdio")
    command: Mapped[str | None] = mapped_column(String(500), nullable=True)
    args: Any = Column(JSON, nullable=True)
    env: Any = Column(JSON, nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    working_dir: Mapped[str | None] = mapped_column(String(500), nullable=True)
    headers: Any = Column(JSON, nullable=True)  # dict[str, str] for SSE/Streamable HTTP
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    tool_count: Mapped[int] = mapped_column(Integer, default=0)
    is_global: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default="FALSE")
    cloned_from_server_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    cloned_from_user_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    visibility: Mapped[str] = mapped_column(
        String(20), nullable=False, default="personal", server_default="personal"
    )
    org_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=True, index=True
    )

    # Publish review fields
    publish_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User | None] = relationship(back_populates="mcp_servers", lazy="raise")

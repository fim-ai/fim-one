"""Connector and ConnectorAction ORM models."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fim_one.db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from .database_schema import DatabaseSchema
    from .user import User

__all__ = ["Connector", "ConnectorAction"]


class Connector(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "connectors"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon: Mapped[str | None] = mapped_column(String(100), nullable=True)
    type: Mapped[str] = mapped_column(String(20), default="api")
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    auth_type: Mapped[str] = mapped_column(String(20), default="none")
    auth_config: Any = Column(JSON, nullable=True)
    db_config: Any = Column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="published")
    is_official: Mapped[bool] = mapped_column(Boolean, default=False)
    forked_from: Mapped[str | None] = mapped_column(String(36), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    visibility: Mapped[str] = mapped_column(
        String(20), nullable=False, default="personal", server_default="personal"
    )
    org_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=True, index=True
    )
    allow_fallback: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sa.text("TRUE")
    )

    # Publish review fields
    publish_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship(back_populates="connectors", lazy="raise")
    actions: Mapped[list[ConnectorAction]] = relationship(
        back_populates="connector", cascade="all, delete-orphan", lazy="raise"
    )
    database_schemas: Mapped[list[DatabaseSchema]] = relationship(
        back_populates="connector", cascade="all, delete-orphan", lazy="raise"
    )


class ConnectorAction(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "connector_actions"

    connector_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("connectors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    method: Mapped[str] = mapped_column(String(10), default="GET")
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    parameters_schema: Any = Column(JSON, nullable=True)
    request_body_template: Any = Column(JSON, nullable=True)
    response_extract: Mapped[str | None] = mapped_column(String(200), nullable=True)
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)

    connector: Mapped[Connector] = relationship(back_populates="actions", lazy="raise")

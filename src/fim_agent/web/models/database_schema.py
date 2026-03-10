"""DatabaseSchema and SchemaColumn ORM models for database connectors."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fim_agent.db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from .connector import Connector

__all__ = ["DatabaseSchema", "SchemaColumn"]


class DatabaseSchema(UUIDPKMixin, TimestampMixin, Base):
    """Represents a discovered table in a database connector."""

    __tablename__ = "database_schemas"

    connector_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("connectors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    table_name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_visible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="TRUE"
    )

    connector: Mapped[Connector] = relationship(back_populates="database_schemas", lazy="raise")
    columns: Mapped[list[SchemaColumn]] = relationship(
        back_populates="schema", cascade="all, delete-orphan", lazy="raise"
    )


class SchemaColumn(UUIDPKMixin, TimestampMixin, Base):
    """Represents a column within a DatabaseSchema table."""

    __tablename__ = "schema_columns"

    schema_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("database_schemas.id", ondelete="CASCADE"), nullable=False, index=True
    )
    column_name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_type: Mapped[str] = mapped_column(String(100), nullable=False)
    is_nullable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="TRUE"
    )
    is_primary_key: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="FALSE"
    )
    is_visible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="TRUE"
    )

    schema: Mapped[DatabaseSchema] = relationship(back_populates="columns", lazy="raise")

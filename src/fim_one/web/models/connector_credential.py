"""ConnectorCredential ORM model — stores encrypted auth credentials."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from fim_one.db.base import Base, TimestampMixin, UUIDPKMixin


__all__ = ["ConnectorCredential"]


class ConnectorCredential(UUIDPKMixin, TimestampMixin, Base):
    """Per-connector (optionally per-user) credential storage.

    user_id = NULL  -> owner's default credential
    user_id = <id>  -> per-user override
    """

    __tablename__ = "connector_credentials"
    __table_args__ = (
        UniqueConstraint("connector_id", "user_id", name="uq_connector_user_credential"),
    )

    connector_id: Mapped[str] = mapped_column(
        String(36),
        sa.ForeignKey("connectors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str | None] = mapped_column(
        String(36),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    credentials_blob: Mapped[str] = mapped_column(Text, nullable=False)

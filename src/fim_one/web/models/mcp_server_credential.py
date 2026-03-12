"""MCPServerCredential — per-user encrypted env/headers override."""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from fim_one.db.base import Base, TimestampMixin, UUIDPKMixin

__all__ = ["MCPServerCredential"]


class MCPServerCredential(UUIDPKMixin, TimestampMixin, Base):
    """Per-MCP-server (optionally per-user) credential storage.

    user_id = NULL  -> owner's default credential (reserved / future)
    user_id = <id>  -> per-user override
    """

    __tablename__ = "mcp_server_credentials"
    __table_args__ = (
        UniqueConstraint(
            "server_id", "user_id", name="uq_mcp_server_user_credential"
        ),
    )

    server_id: Mapped[str] = mapped_column(
        String(36),
        sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str | None] = mapped_column(
        String(36),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    env_blob: Mapped[str | None] = mapped_column(Text, nullable=True)
    headers_blob: Mapped[str | None] = mapped_column(Text, nullable=True)

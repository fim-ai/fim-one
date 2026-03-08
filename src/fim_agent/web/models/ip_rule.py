"""IpRule ORM model — IP whitelist/blacklist rules."""

from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from fim_agent.db.base import Base, TimestampMixin, UUIDPKMixin


class IpRule(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "ip_rules"

    ip_address: Mapped[str] = mapped_column(String(45), nullable=False, index=True)
    rule_type: Mapped[str] = mapped_column(String(10), nullable=False)  # "allow" or "deny"
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

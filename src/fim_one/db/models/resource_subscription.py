"""ResourceSubscription ORM model — user subscription to shared resources."""
from __future__ import annotations

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from fim_one.db.base import Base, TimestampMixin, UUIDPKMixin

__all__ = ["ResourceSubscription"]


class ResourceSubscription(UUIDPKMixin, TimestampMixin, Base):
    """Records that a user has subscribed to a resource from an org Market."""

    __tablename__ = "resource_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "resource_type", "resource_id",
            name="uq_resource_subscription",
        ),
    )

    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(30), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    org_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

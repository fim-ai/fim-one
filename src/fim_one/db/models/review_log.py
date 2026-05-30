"""ORM model for the review_log audit trail."""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from fim_one.db import Base


class ReviewLog(Base):
    __tablename__ = "review_log"

    id: Mapped[str] = mapped_column(
        sa.String, primary_key=True, default=lambda: uuid.uuid4().hex
    )
    org_id: Mapped[str] = mapped_column(sa.String, nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(sa.String, nullable=False)
    resource_id: Mapped[str] = mapped_column(sa.String, nullable=False, index=True)
    resource_name: Mapped[str] = mapped_column(sa.String, nullable=False)
    action: Mapped[str] = mapped_column(sa.String, nullable=False)
    actor_id: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    actor_username: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    note: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.text("(CURRENT_TIMESTAMP)")
    )

"""One row that serializes creation of the first account.

Two requests that both read ``count(users) = 0`` would each create an
administrator. Updating this row takes a lock the other request waits on,
on PostgreSQL (row lock) and on SQLite (the database write lock), and the
waiter then sees the row the first request committed.
"""

from __future__ import annotations

from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column

from fim_one.db.base import Base


class InstanceGuard(Base):
    __tablename__ = "instance_guard"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nonce: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

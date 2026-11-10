"""UsageLedger ORM model: what was consumed, by whom, append-only."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from fim_one.db.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class UsageLedger(Base):
    """What one metering scope consumed on one model.

    A scope is a chat turn, a workflow run (each part of one that waited
    and was continued), an evaluation run or a helper endpoint's request.
    A row is written when the scope reaches a boundary, so a turn leaves
    one to three rows (main model, fast model, background work), not one
    per model call.

    Rows are only ever inserted, by :mod:`fim_one.core.usage.ledger`. No
    column is a foreign key: a row outlives the conversation, run, workflow
    and user it names, which is what keeps "used" from shrinking when
    those are deleted. A row belongs to the quota window its ``created_at``
    falls in: when the usage was recorded, not when its source was created.
    """

    __tablename__ = "usage_ledger"
    __table_args__ = (
        Index("ix_usage_ledger_user_created", "user_id", "created_at"),
        Index("ix_usage_ledger_org_created", "org_id", "created_at"),
        Index("ix_usage_ledger_source", "source_type", "source_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=text("(CURRENT_TIMESTAMP)")
    )

    # Whose account it counts against: whoever ran it; the author for a
    # trigger key or a schedule. Empty for the platform's own calls.
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # The organization of the agent or workflow it ran through, if any.
    org_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # ``conversation`` / ``workflow_run`` / ``eval_run`` / ``endpoint``, and
    # the id of that conversation, run or resource (never a foreign key).
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The entry point: ``chat`` / ``dag`` / ``auto`` / ``background``, a
    # workflow run mode, ``ai_assist.<name>`` / ``kb_chat`` / ``db_chat`` / ``eval``.
    entry: Mapped[str] = mapped_column(String(40), nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    workflow_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # The split is empty on backfilled rows: the old columns kept a total only.
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_creation_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # The provider sent no count (a stream cut short): estimated from the text.
    estimated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("FALSE")
    )
    # ``usage`` (metered), ``backfill`` (carried over from the per-row totals
    # when the ledger was introduced) or ``adjustment`` (a correction is a
    # new row, which may be negative; rows are never changed).
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="usage", server_default="usage"
    )
    # Only model tokens are metered today.
    unit: Mapped[str] = mapped_column(
        String(20), nullable=False, default="llm_token", server_default="llm_token"
    )

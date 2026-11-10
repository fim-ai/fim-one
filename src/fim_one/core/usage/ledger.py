"""Rows of ``usage_ledger``: the one place that writes them, and the sums read from them.

Rows are only inserted. A correction is a further row of kind
``adjustment``; nothing here or anywhere else updates or deletes one.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

__all__ = ["UsageEntry", "used_by_users_since", "used_since", "write_entries"]

SOURCE_TYPES = frozenset({"conversation", "workflow_run", "eval_run", "endpoint"})


@dataclass(frozen=True)
class UsageEntry:
    """One row to write: what a scope consumed on one model."""

    user_id: str | None
    org_id: str | None
    source_type: str
    source_id: str | None
    entry: str
    model: str | None
    total_tokens: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    agent_id: str | None = None
    workflow_id: str | None = None
    estimated: bool = False
    kind: str = "usage"


def _fit(value: str | None, length: int) -> str | None:
    return value[:length] if isinstance(value, str) else value


def _own_session() -> Any:
    """A session of the ledger's own: a row never joins a caller's transaction."""
    from fim_one.db import create_session

    return create_session()


def _rows(entries: Iterable[UsageEntry]) -> list[Any]:
    from fim_one.db.models.usage_ledger import UsageLedger

    return [
        UsageLedger(
            user_id=entry.user_id,
            org_id=entry.org_id,
            source_type=_fit(entry.source_type, 20),
            source_id=_fit(entry.source_id, 64),
            entry=_fit(entry.entry, 40),
            agent_id=_fit(entry.agent_id, 36),
            workflow_id=_fit(entry.workflow_id, 36),
            model=_fit(entry.model, 100),
            prompt_tokens=entry.prompt_tokens,
            completion_tokens=entry.completion_tokens,
            cache_read_tokens=entry.cache_read_tokens,
            cache_creation_tokens=entry.cache_creation_tokens,
            total_tokens=int(entry.total_tokens),
            estimated=bool(entry.estimated),
            kind=entry.kind,
        )
        for entry in entries
    ]


async def write_entries(entries: Sequence[UsageEntry]) -> bool:
    """Insert *entries*, in a session and transaction of their own.

    Never raises: recording usage must not fail the turn that consumed it.
    A failed insert is tried once more; if that fails too, every entry is
    written to the log at ERROR with its numbers, so the usage can be
    restored with an ``adjustment`` row. Returns whether the rows were stored.

    Rows of kind ``usage`` are never negative: an entry that is not
    positive is dropped.
    """
    wanted = [
        entry for entry in entries if entry.total_tokens > 0 or entry.kind == "adjustment"
    ]
    if not wanted:
        return True
    failure: Exception | None = None
    for _attempt in range(2):
        try:
            async with _own_session() as session:
                session.add_all(_rows(wanted))
                await session.commit()
            return True
        except Exception as exc:
            failure = exc
    for entry in wanted:
        logger.error(
            "Usage not recorded (%s): user=%s org=%s source=%s:%s entry=%s model=%s "
            "prompt=%s completion=%s total=%s estimated=%s",
            type(failure).__name__,
            entry.user_id,
            entry.org_id,
            entry.source_type,
            entry.source_id,
            entry.entry,
            entry.model,
            entry.prompt_tokens,
            entry.completion_tokens,
            entry.total_tokens,
            entry.estimated,
        )
    return False


async def used_since(db: AsyncSession, user_id: str, since: datetime) -> int:
    """Tokens recorded against *user_id* from *since* on. Never negative."""
    from sqlalchemy import func, select

    from fim_one.db.models.usage_ledger import UsageLedger

    total = (
        await db.execute(
            select(func.coalesce(func.sum(UsageLedger.total_tokens), 0)).where(
                UsageLedger.user_id == user_id,
                UsageLedger.created_at >= since,
            )
        )
    ).scalar_one()
    return max(int(total or 0), 0)


async def used_by_users_since(
    db: AsyncSession,
    since: datetime,
    *,
    user_ids: Sequence[str] | None = None,
) -> dict[str, int]:
    """Tokens recorded from *since* on, per user (only users with any).

    *user_ids* limits the answer to those users; an empty list answers ``{}``.
    """
    from sqlalchemy import func, select

    from fim_one.db.models.usage_ledger import UsageLedger

    if user_ids is not None and not user_ids:
        return {}
    query = (
        select(UsageLedger.user_id, func.coalesce(func.sum(UsageLedger.total_tokens), 0))
        .where(UsageLedger.user_id.is_not(None), UsageLedger.created_at >= since)
        .group_by(UsageLedger.user_id)
    )
    if user_ids is not None:
        query = query.where(UsageLedger.user_id.in_(list(user_ids)))
    rows = (await db.execute(query)).all()
    return {str(user_id): max(int(total or 0), 0) for user_id, total in rows}

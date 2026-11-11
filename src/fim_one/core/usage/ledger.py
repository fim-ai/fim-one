"""Rows of ``usage_ledger``: the one place that writes them, and the sums read from them.

Rows are only inserted. A correction is a further row of kind
``adjustment``; nothing here or anywhere else updates or deletes one.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

__all__ = [
    "UsageBucket",
    "UsageEntry",
    "UsageSlice",
    "usage_per_agent_subquery",
    "usage_per_user_subquery",
    "used_between",
    "used_by",
    "used_by_day",
    "used_by_origin",
    "used_by_user_between",
    "used_by_users_since",
    "used_since",
    "write_entries",
]

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


def _rows(entries: Iterable[tuple[str, UsageEntry]]) -> list[Any]:
    from fim_one.db.models.usage_ledger import UsageLedger

    return [
        UsageLedger(
            id=row_id,
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
        for row_id, entry in entries
    ]


async def write_entries(entries: Sequence[UsageEntry]) -> bool:
    """Insert *entries*, in a session and transaction of their own.

    Never raises: recording usage must not fail the turn that consumed it.
    A failed insert is tried once more; if that fails too, every entry is
    written to the log at ERROR with its numbers, so the usage can be
    restored with an ``adjustment`` row. Returns whether the rows were stored.

    The retry cannot write a row twice: each row's id is chosen here, once,
    and the second attempt leaves out the ids already stored (a commit that
    succeeded but whose acknowledgement was lost).

    Rows of kind ``usage`` are never negative: an entry that is not
    positive is dropped.
    """
    wanted = [
        entry for entry in entries if entry.total_tokens > 0 or entry.kind == "adjustment"
    ]
    if not wanted:
        return True
    from sqlalchemy import select

    from fim_one.db.models.usage_ledger import UsageLedger

    pending = [(str(uuid.uuid4()), entry) for entry in wanted]
    failure: Exception | None = None
    for attempt in range(2):
        try:
            async with _own_session() as session:
                if attempt:
                    stored = set(
                        (
                            await session.execute(
                                select(UsageLedger.id).where(
                                    UsageLedger.id.in_([row_id for row_id, _ in pending])
                                )
                            )
                        ).scalars()
                    )
                    pending = [(row_id, entry) for row_id, entry in pending if row_id not in stored]
                session.add_all(_rows(pending))
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


def usage_per_user_subquery(since: datetime | None = None) -> Any:
    """A subquery of ``(user_id, total_tokens)``: what each user has recorded
    from *since* on (over all time when ``None``), for joining to users."""
    from sqlalchemy import func, select

    from fim_one.db.models.usage_ledger import UsageLedger

    query = (
        select(
            UsageLedger.user_id.label("user_id"),
            func.coalesce(func.sum(UsageLedger.total_tokens), 0).label("total_tokens"),
        )
        .where(UsageLedger.user_id.is_not(None))
        .group_by(UsageLedger.user_id)
    )
    if since is not None:
        query = query.where(UsageLedger.created_at >= since)
    return query.subquery()


async def used_by_day(db: AsyncSession, user_id: str, since: datetime) -> list[tuple[str, int]]:
    """*user_id*'s usage from *since* on per day it was recorded, oldest first.

    The day is the UTC date, as ``YYYY-MM-DD``. A day is the plain sum of
    its rows, negative when its adjustments outweigh its usage, so the days
    add up to the window's total; clamp the total, not the days.
    """
    from sqlalchemy import func, select

    from fim_one.db.models.usage_ledger import UsageLedger

    day = func.date(UsageLedger.created_at)
    rows = (
        await db.execute(
            select(day, func.coalesce(func.sum(UsageLedger.total_tokens), 0))
            .where(UsageLedger.user_id == user_id, UsageLedger.created_at >= since)
            .group_by(day)
            .order_by(day)
        )
    ).all()
    return [(str(found), int(total or 0)) for found, total in rows]


@dataclass(frozen=True)
class UsageBucket:
    """Usage that came about the same way: through one agent, or with none
    through one kind of entry."""

    agent_id: str | None
    source_type: str
    entry: str
    total_tokens: int


async def used_by_origin(db: AsyncSession, user_id: str, since: datetime) -> list[UsageBucket]:
    """*user_id*'s usage from *since* on, by the agent and the entry it came through."""
    from sqlalchemy import func, select

    from fim_one.db.models.usage_ledger import UsageLedger

    rows = (
        await db.execute(
            select(
                UsageLedger.agent_id,
                UsageLedger.source_type,
                UsageLedger.entry,
                func.coalesce(func.sum(UsageLedger.total_tokens), 0),
            )
            .where(UsageLedger.user_id == user_id, UsageLedger.created_at >= since)
            .group_by(UsageLedger.agent_id, UsageLedger.source_type, UsageLedger.entry)
        )
    ).all()
    return [
        UsageBucket(
            agent_id=agent_id,
            source_type=source_type,
            entry=entry,
            total_tokens=max(int(total or 0), 0),
        )
        for agent_id, source_type, entry, total in rows
    ]


# ---------------------------------------------------------------------------
# Platform-wide figures (the statistics pages)
# ---------------------------------------------------------------------------

Dimension = Literal["day", "model", "agent", "background"]
_DIMENSIONS: tuple[Dimension, ...] = ("day", "model", "agent", "background")


@dataclass(frozen=True)
class UsageSlice:
    """Usage that shares the values of the dimensions it was grouped by.

    A dimension not grouped by is ``None``. ``day`` is the date the usage
    was recorded, as ``YYYY-MM-DD`` (``DATE(created_at)``, as the other
    daily figures). ``background`` is whether it was the work done after a
    turn (entry ``background``: the title, and on backfilled rows every
    fast-model token of the conversation, which carry no model).
    """

    total_tokens: int
    day: str | None = None
    model: str | None = None
    agent_id: str | None = None
    background: bool | None = None


def _window(query: Any, since: datetime | None, until: datetime | None) -> Any:
    from fim_one.db.models.usage_ledger import UsageLedger

    if since is not None:
        query = query.where(UsageLedger.created_at >= since)
    if until is not None:
        query = query.where(UsageLedger.created_at < until)
    return query


async def used_between(
    db: AsyncSession,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> int:
    """Tokens recorded by everyone from *since* (inclusive) to *until*
    (exclusive); either bound may be left open. Never negative.

    One user's figure is :func:`used_by_user_between`.
    """
    from sqlalchemy import func, select

    from fim_one.db.models.usage_ledger import UsageLedger

    query = _window(select(func.coalesce(func.sum(UsageLedger.total_tokens), 0)), since, until)
    return max(int((await db.execute(query)).scalar_one() or 0), 0)


async def used_by_user_between(
    db: AsyncSession,
    user_id: str,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> int:
    """Tokens recorded against *user_id* from *since* (inclusive) to *until*
    (exclusive); either bound may be left open. Never negative.

    Raises ``ValueError`` without a user id, so a missing id cannot turn
    into everyone's figure.
    """
    from sqlalchemy import func, select

    from fim_one.db.models.usage_ledger import UsageLedger

    if not user_id:
        raise ValueError("a per-user usage read needs a user id")
    query = _window(
        select(func.coalesce(func.sum(UsageLedger.total_tokens), 0)).where(
            UsageLedger.user_id == user_id
        ),
        since,
        until,
    )
    return max(int((await db.execute(query)).scalar_one() or 0), 0)


async def used_by(
    db: AsyncSession,
    by: Sequence[Dimension],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[UsageSlice]:
    """Tokens recorded by everyone in the window, grouped by the dimensions
    in *by*. Ordered by day when grouped by day.

    A slice is the plain sum of its rows, negative when its adjustments
    outweigh its usage, so the slices add up to :func:`used_between` over
    the same window. Clamp the totals shown, not the slices.
    """
    from sqlalchemy import case, func, select

    from fim_one.db.models.usage_ledger import UsageLedger

    unknown = [name for name in by if name not in _DIMENSIONS]
    if unknown:
        raise ValueError(f"cannot group usage by {unknown}")
    columns: dict[str, Any] = {
        "day": func.date(UsageLedger.created_at),
        "model": UsageLedger.model,
        "agent": UsageLedger.agent_id,
        "background": case((UsageLedger.entry == "background", 1), else_=0),
    }
    keys = [name for name in _DIMENSIONS if name in by]
    grouped = [columns[name] for name in keys]
    query = _window(
        select(*grouped, func.coalesce(func.sum(UsageLedger.total_tokens), 0)),
        since,
        until,
    )
    if grouped:
        query = query.group_by(*grouped)
    if "day" in keys:
        query = query.order_by(columns["day"])
    slices = []
    for row in (await db.execute(query)).all():
        values = dict(zip(keys, row[:-1], strict=True))
        slices.append(
            UsageSlice(
                total_tokens=int(row[-1] or 0),
                day=str(values["day"]) if "day" in values else None,
                model=values.get("model"),
                agent_id=values.get("agent"),
                background=bool(values["background"]) if "background" in values else None,
            )
        )
    return slices


def usage_per_agent_subquery(since: datetime | None = None) -> Any:
    """A subquery of ``(agent_id, total_tokens)``: what was recorded through
    each agent from *since* on (over all time when ``None``), for joining to agents."""
    from sqlalchemy import func, select

    from fim_one.db.models.usage_ledger import UsageLedger

    query = (
        select(
            UsageLedger.agent_id.label("agent_id"),
            func.coalesce(func.sum(UsageLedger.total_tokens), 0).label("total_tokens"),
        )
        .where(UsageLedger.agent_id.is_not(None))
        .group_by(UsageLedger.agent_id)
    )
    if since is not None:
        query = query.where(UsageLedger.created_at >= since)
    return query.subquery()

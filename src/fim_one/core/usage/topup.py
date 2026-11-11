"""Bring the ledger up to the totals kept on conversations and workflow runs.

For one situation only. The ledger normally holds at least what a
conversation's or a run's ``total_tokens`` reports (it also holds turns
that were interrupted and delegated agents). If the release that writes
the ledger is rolled back to one that does not, turns and runs go on
adding to ``total_tokens`` with no ledger row. When the ledger-writing
release is deployed again, that usage is missing from "used".

This command finds every conversation and run whose ledger rows add up to
less than its ``total_tokens`` and records the difference as one row of
kind ``adjustment`` for the same user and source. It only ever adds rows.
Run again, it finds nothing left to add.

    python -m fim_one.core.usage.topup            # report what is missing
    python -m fim_one.core.usage.topup --apply    # and record it

The added usage counts in the quota window in which the command is run:
when it was consumed is not known.

Run it with the application stopped. A turn writes its conversation's
total and its ledger rows in two steps; run against a live application,
the command can see a total whose ledger rows are not written yet and add
the difference, which is then counted twice.

It compares sums per conversation or run, so it can recover less than was
missing. The ledger also holds usage that ``total_tokens`` never reported
(interrupted turns, delegated agents), and that extra hides a gap of the
same size. Example: a conversation reports 180; its ledger rows add up to
170, 20 of them a delegated agent's; 30 are missing, and 10 are recorded.
Which of a conversation's rows the total covers is not recorded, so the
command cannot tell the two apart.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from .ledger import UsageEntry, write_entries

logger = logging.getLogger(__name__)

__all__ = ["TopUp", "top_up"]

# Source rows looked at per query.
BATCH = 1000


@dataclass
class TopUp:
    """What :func:`top_up` found (and, when applying, recorded)."""

    sources: int = 0
    tokens: int = 0
    users: set[str] = field(default_factory=set)
    applied: bool = False


async def _missing(db: Any, model: Any, source_type: str, last: str) -> tuple[list[UsageEntry], str]:
    """The next batch of *model*'s rows after id *last*, and what each is short by."""
    from sqlalchemy import func, select

    from fim_one.db.models.usage_ledger import UsageLedger

    rows = (
        await db.execute(
            select(model)
            .where(model.id > last, model.total_tokens > 0)
            .order_by(model.id)
            .limit(BATCH)
        )
    ).scalars().all()
    if not rows:
        return [], ""
    recorded = dict(
        (
            await db.execute(
                select(UsageLedger.source_id, func.coalesce(func.sum(UsageLedger.total_tokens), 0))
                .where(
                    UsageLedger.source_type == source_type,
                    UsageLedger.source_id.in_([row.id for row in rows]),
                )
                .group_by(UsageLedger.source_id)
            )
        ).all()
    )
    entries = [
        UsageEntry(
            user_id=row.user_id,
            org_id=None,
            source_type=source_type,
            source_id=row.id,
            entry="topup",
            model=None,
            total_tokens=int(row.total_tokens) - int(recorded.get(row.id, 0)),
            agent_id=getattr(row, "agent_id", None),
            workflow_id=getattr(row, "workflow_id", None),
            kind="adjustment",
        )
        for row in rows
        if int(row.total_tokens) > int(recorded.get(row.id, 0))
    ]
    return entries, str(rows[-1].id)


async def top_up(*, apply: bool = False) -> TopUp:
    """Find what the ledger is short of its sources; with *apply*, record it."""
    from fim_one.db import create_session
    from fim_one.db.models import Conversation
    from fim_one.db.models.workflow import WorkflowRun

    found = TopUp(applied=apply)
    for model, source_type in ((Conversation, "conversation"), (WorkflowRun, "workflow_run")):
        last = ""
        while True:
            async with create_session() as db:
                entries, last = await _missing(db, model, source_type, last)
            if not last:
                break
            for entry in entries:
                found.sources += 1
                found.tokens += entry.total_tokens
                if entry.user_id:
                    found.users.add(entry.user_id)
            if apply and entries and not await write_entries(entries):
                raise RuntimeError("the adjustment rows could not be written; nothing more was done")
    return found


async def _main(apply: bool) -> None:
    from fim_one.db import init_db, shutdown_db

    await init_db()
    try:
        found = await top_up(apply=apply)
    finally:
        await shutdown_db()
    verb = "Recorded" if apply else "Missing (not recorded, run with --apply)"
    print(  # noqa: T201 - a command's answer
        f"{verb}: {found.tokens} tokens of {found.sources} conversations and runs,"
        f" {len(found.users)} users"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="record what is missing")
    asyncio.run(_main(parser.parse_args().apply))

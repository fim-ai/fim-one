"""``core/usage/topup.py``: after a rollback, what the ledger is short of its sources."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fim_one.core.usage.topup import top_up
from fim_one.db.models import Conversation, User
from fim_one.db.models.usage_ledger import UsageLedger
from fim_one.db.models.workflow import Workflow, WorkflowRun

from .db import factory  # noqa: F401

Factory = async_sessionmaker[AsyncSession]


async def _seed(factory: Factory) -> None:  # noqa: F811
    async with factory() as db:
        db.add_all([User(id="alice", email="a@x.io"), User(id="bob", email="b@x.io")])
        await db.flush()
        db.add(Workflow(id="wf", user_id="alice", name="wf", blueprint={}))
        await db.flush()
        db.add_all(
            [
                # In step: carried over, then metered.
                Conversation(id="c-even", user_id="alice", mode="react", total_tokens=100),
                UsageLedger(user_id="alice", source_type="conversation", source_id="c-even",
                            entry="react", total_tokens=60, kind="backfill"),
                UsageLedger(user_id="alice", source_type="conversation", source_id="c-even",
                            entry="chat", total_tokens=40),
                # The ledger holds more (an interrupted turn): nothing to add.
                Conversation(id="c-more", user_id="alice", mode="react", total_tokens=50),
                UsageLedger(user_id="alice", source_type="conversation", source_id="c-more",
                            entry="chat", total_tokens=80),
                # Grew by 70 while the ledger was not being written.
                Conversation(id="c-short", user_id="bob", mode="react", total_tokens=170),
                UsageLedger(user_id="bob", source_type="conversation", source_id="c-short",
                            entry="chat", total_tokens=100),
                # Created and used entirely in that time.
                Conversation(id="c-new", user_id="bob", mode="dag", total_tokens=30),
                Conversation(id="c-empty", user_id="bob", mode="dag", total_tokens=0),
                WorkflowRun(id="r-new", workflow_id="wf", user_id="alice", blueprint_snapshot={},
                            status="completed", mode="trigger", total_tokens=9),
            ]
        )  # fmt: skip
        await db.commit()


async def _adjustments(factory: Factory) -> dict[str, tuple[str | None, int]]:  # noqa: F811
    async with factory() as db:
        rows = (
            await db.execute(select(UsageLedger).where(UsageLedger.kind == "adjustment"))
        ).scalars().all()
        return {str(row.source_id): (row.user_id, row.total_tokens) for row in rows}


async def test_it_reports_what_is_missing_and_writes_nothing(factory: Factory) -> None:  # noqa: F811
    await _seed(factory)

    found = await top_up()

    assert (found.sources, found.tokens, found.users, found.applied) == (
        3, 109, {"alice", "bob"}, False,
    )  # fmt: skip
    assert await _adjustments(factory) == {}


async def test_applied_it_adds_the_difference_once(factory: Factory) -> None:  # noqa: F811
    await _seed(factory)

    found = await top_up(apply=True)
    again = await top_up(apply=True)

    assert (found.sources, found.tokens) == (3, 109)
    assert (again.sources, again.tokens) == (0, 0)
    assert await _adjustments(factory) == {
        "c-short": ("bob", 70),
        "c-new": ("bob", 30),
        "r-new": ("alice", 9),
    }
    async with factory() as db:
        run_row = (
            await db.execute(select(UsageLedger).where(UsageLedger.source_id == "r-new"))
        ).scalar_one()
        assert (run_row.source_type, run_row.workflow_id, run_row.entry) == (
            "workflow_run", "wf", "topup",
        )  # fmt: skip
        # Nothing that was there was changed.
        untouched = (
            await db.execute(select(UsageLedger).where(UsageLedger.kind != "adjustment"))
        ).scalars().all()
        assert sorted(row.total_tokens for row in untouched) == [40, 60, 80, 100]

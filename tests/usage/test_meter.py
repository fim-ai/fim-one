"""``core/usage/meter.py``: a metering scope and when it writes.

Named in the design as ``test_meter_nesting``: a model call is recorded on
the innermost open scope and nowhere else.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fim_one.core.assembly.principal import Binding, CallContext, provisional_principal
from fim_one.core.model.usage import current_meter, report_usage
from fim_one.core.usage import ledger
from fim_one.core.usage import meter as usage_meter
from fim_one.core.usage.meter import (
    OpenMeter,
    UsageScope,
    flush_all,
    metered,
    open_meter,
    scope_from_context,
    wait_for_writes,
)
from fim_one.db.models.usage_ledger import UsageLedger

from .db import factory  # noqa: F401

Factory = async_sessionmaker[AsyncSession]
TEN = {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}


def _scope(source: str = "conv-1", **fields: Any) -> UsageScope:
    values: dict[str, Any] = {
        "user_id": "alice",
        "org_id": None,
        "source_type": "conversation",
        "source_id": source,
        "entry": "chat",
        **fields,
    }
    return UsageScope(**values)


async def _rows(factory: Factory) -> list[UsageLedger]:  # noqa: F811
    await wait_for_writes()
    async with factory() as db:
        return list((await db.execute(select(UsageLedger))).scalars().all())


def _by_source(rows: list[UsageLedger]) -> dict[str | None, int]:
    totals: dict[str | None, int] = {}
    for row in rows:
        totals[row.source_id] = totals.get(row.source_id, 0) + row.total_tokens
    return totals


class TestAScope:
    async def test_what_a_block_consumed_is_written_when_it_ends(
        self, factory: Factory  # noqa: F811
    ) -> None:
        async with metered(_scope(org_id="org-1", agent_id="agent-1")) as opened:
            report_usage("main", TEN)
            report_usage("main", TEN)
            report_usage("fast", TEN)
            assert opened.total() == 30
            assert await _rows(factory) == []  # nothing per call

        rows = {row.model: row for row in await _rows(factory)}
        assert {model: row.total_tokens for model, row in rows.items()} == {"main": 20, "fast": 10}
        main = rows["main"]
        assert (main.user_id, main.org_id, main.agent_id) == ("alice", "org-1", "agent-1")
        assert (main.source_type, main.source_id, main.entry) == ("conversation", "conv-1", "chat")
        assert (main.prompt_tokens, main.completion_tokens) == (14, 6)
        assert current_meter.get() is None

    async def test_a_block_that_raises_is_written_all_the_same(
        self, factory: Factory  # noqa: F811
    ) -> None:
        with pytest.raises(RuntimeError):
            async with metered(_scope()):
                report_usage("main", TEN)
                raise RuntimeError("the turn failed")

        assert _by_source(await _rows(factory)) == {"conv-1": 10}

    async def test_a_cancelled_block_is_written_all_the_same(
        self, factory: Factory  # noqa: F811
    ) -> None:
        started = asyncio.Event()

        async def _turn() -> None:
            async with metered(_scope()):
                report_usage("main", TEN)
                started.set()
                await asyncio.sleep(60)

        task = asyncio.create_task(_turn())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert _by_source(await _rows(factory)) == {"conv-1": 10}

    async def test_flushing_twice_writes_once(self, factory: Factory) -> None:  # noqa: F811
        opened = open_meter(_scope())
        report_usage("main", TEN)
        await opened.flush()
        await opened.flush()
        report_usage("main", TEN)
        await opened.close()
        await opened.close()
        opened.leave()

        rows = await _rows(factory)
        assert [row.total_tokens for row in rows] == [10, 10]
        assert opened.total() == 20

    async def test_estimated_usage_is_marked_on_its_row(self, factory: Factory) -> None:  # noqa: F811
        async with metered(_scope()):
            report_usage("main", TEN)
            report_usage("main", {"prompt_tokens": 4, "completion_tokens": 1}, estimated=True)

        assert sorted((row.total_tokens, row.estimated) for row in await _rows(factory)) == [
            (5, True),
            (10, False),
        ]

    async def test_a_scope_nobody_bound_is_recorded_with_no_user(
        self, factory: Factory, caplog: pytest.LogCaptureFixture  # noqa: F811
    ) -> None:
        opened = open_meter()
        report_usage("main", TEN)
        with caplog.at_level(logging.ERROR, logger="fim_one.core.usage.meter"):
            await opened.close()
        opened.leave()

        (row,) = await _rows(factory)
        assert (row.user_id, row.entry, row.total_tokens) == (None, "unbound", 10)
        assert "never bound" in caplog.text

    async def test_bound_later_it_is_the_users(self, factory: Factory) -> None:  # noqa: F811
        opened = open_meter()
        opened.bind(_scope())
        report_usage("main", TEN)
        await opened.close()
        opened.leave()

        (row,) = await _rows(factory)
        assert row.user_id == "alice"

    async def test_a_write_that_fails_does_not_fail_the_block(
        self, factory: Factory, monkeypatch: pytest.MonkeyPatch  # noqa: F811
    ) -> None:
        def _down() -> Any:
            raise ConnectionError("database is gone")

        monkeypatch.setattr(ledger, "_own_session", _down)

        async with metered(_scope()):
            report_usage("main", TEN)

        monkeypatch.undo()
        assert await _rows(factory) == []

    def test_the_scope_of_a_call_context(self) -> None:
        principal = provisional_principal("alice")
        with_agent = CallContext(
            principal=principal,
            binding=Binding(kind="agent", id="agent-1", owner_id="owner", org_id="org-1"),
            entry="chat",
            conversation_id="conv-1",
            agent_id="agent-1",
        )
        bare = CallContext(principal=principal, binding=None, entry="chat_noagent")

        scope = scope_from_context(
            with_agent, source_type="conversation", source_id="conv-1", entry="chat"
        )
        assert (scope.user_id, scope.org_id, scope.agent_id, scope.workflow_id) == (
            "alice", "org-1", "agent-1", None,
        )  # fmt: skip
        scope = scope_from_context(bare, source_type="endpoint", source_id=None, entry="dag")
        assert (scope.user_id, scope.org_id, scope.agent_id, scope.source_type) == (
            "alice", None, None, "endpoint",
        )  # fmt: skip


class TestNesting:
    async def test_a_call_is_recorded_on_the_innermost_scope_only(
        self, factory: Factory  # noqa: F811
    ) -> None:
        async with metered(_scope("conv-1")) as outer:
            report_usage("main", TEN)
            async with metered(_scope("run-1", source_type="workflow_run", entry="tool")) as inner:
                report_usage("main", TEN)
                report_usage("main", TEN)
            report_usage("main", TEN)

        assert (outer.total(), inner.total()) == (20, 20)
        assert _by_source(await _rows(factory)) == {"conv-1": 20, "run-1": 20}

    async def test_a_child_task_reports_to_its_parents_scope(
        self, factory: Factory  # noqa: F811
    ) -> None:
        """A delegated agent, a step title: no scope of their own."""

        async def _delegate() -> None:
            report_usage("main", TEN)

        async with metered(_scope()):
            await asyncio.gather(*(asyncio.create_task(_delegate()) for _ in range(3)))

        assert _by_source(await _rows(factory)) == {"conv-1": 30}

    async def test_a_scope_opened_in_a_task_of_its_own_is_not_its_starters(
        self, factory: Factory  # noqa: F811
    ) -> None:
        """A workflow run an agent starts as a tool: the run's driver task
        opens the run's scope, and the turn that awaits it goes on reporting
        to the conversation."""

        async def _drive_run() -> None:
            opened = open_meter(_scope("run-1", source_type="workflow_run", entry="tool"))
            try:
                report_usage("main", TEN)
            finally:
                await opened.close()

        async with metered(_scope("conv-1")):
            report_usage("main", TEN)
            await asyncio.create_task(_drive_run())
            report_usage("main", TEN)

        assert _by_source(await _rows(factory)) == {"conv-1": 20, "run-1": 10}

    async def test_work_left_running_gets_a_scope_of_its_own(
        self, factory: Factory  # noqa: F811
    ) -> None:
        async def _title() -> None:
            async with metered(_scope(entry="background")):
                report_usage("fast", TEN)

        async with metered(_scope()):
            report_usage("main", TEN)
            background = asyncio.create_task(_title())
        await background

        assert sorted((row.entry, row.total_tokens) for row in await _rows(factory)) == [
            ("background", 10),
            ("chat", 10),
        ]


class TestWritingWithoutBeingAsked:
    async def test_usage_nobody_flushed_is_written_after_the_interval(
        self, factory: Factory, monkeypatch: pytest.MonkeyPatch  # noqa: F811
    ) -> None:
        """A long turn is written as it goes; so is a scope nobody closes
        (a response that was never read). What a killed process loses is
        bounded by the interval."""
        monkeypatch.setattr(usage_meter, "CHECKPOINT_INTERVAL", 0.05)
        opened = open_meter(_scope())
        report_usage("main", TEN)
        report_usage("main", TEN)
        assert await _rows(factory) == []

        await asyncio.sleep(0.15)
        assert _by_source(await _rows(factory)) == {"conv-1": 20}

        # ...and again for what comes after, with nothing written twice.
        report_usage("main", TEN)
        await asyncio.sleep(0.15)
        assert _by_source(await _rows(factory)) == {"conv-1": 30}
        await opened.close()
        opened.leave()
        assert _by_source(await _rows(factory)) == {"conv-1": 30}

    async def test_usage_that_arrives_after_the_scope_ended_is_still_written(
        self, factory: Factory, monkeypatch: pytest.MonkeyPatch  # noqa: F811
    ) -> None:
        """An abandoned stream reports when the event loop closes it, which
        can be after the turn's scope was closed."""
        monkeypatch.setattr(usage_meter, "LATE_FLUSH_DELAY", 0.02)
        async with metered(_scope()) as opened:
            report_usage("main", TEN)
        assert _by_source(await _rows(factory)) == {"conv-1": 10}

        opened.meter.add("main", {"total_tokens": 4}, estimated=True)
        opened.meter.add("main", {"total_tokens": 4}, estimated=True)
        await asyncio.sleep(0.1)

        assert _by_source(await _rows(factory)) == {"conv-1": 18}

    async def test_a_report_from_a_worker_thread_is_written_too(
        self, factory: Factory, monkeypatch: pytest.MonkeyPatch  # noqa: F811
    ) -> None:
        monkeypatch.setattr(usage_meter, "CHECKPOINT_INTERVAL", 0.05)
        opened = open_meter(_scope())

        await asyncio.to_thread(report_usage, "vision", TEN)
        await asyncio.sleep(0.15)

        assert _by_source(await _rows(factory)) == {"conv-1": 10}
        await opened.close()
        opened.leave()

    async def test_shutdown_writes_what_every_open_scope_holds(
        self, factory: Factory  # noqa: F811
    ) -> None:
        first, second = OpenMeter(_scope("conv-1")), OpenMeter(_scope("conv-2"))
        first.meter.add("main", TEN)
        second.meter.add("main", TEN)

        await flush_all()

        assert _by_source(await _rows(factory)) == {"conv-1": 10, "conv-2": 10}

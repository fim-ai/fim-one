"""``core/usage/ledger.py``: the rows it writes and the sums read from them."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import fim_one
from fim_one.core.usage import ledger
from fim_one.core.usage.ledger import UsageEntry, used_by_users_since, used_since, write_entries
from fim_one.db.models.usage_ledger import UsageLedger

from .db import factory  # noqa: F401

Factory = async_sessionmaker[AsyncSession]
EVER = datetime(2000, 1, 1, tzinfo=UTC)


def _entry(user: str = "alice", total: int = 10, **fields: Any) -> UsageEntry:
    values: dict[str, Any] = {
        "user_id": user,
        "org_id": None,
        "source_type": "conversation",
        "source_id": "conv-1",
        "entry": "chat",
        "model": "main-model",
        "total_tokens": total,
        "prompt_tokens": total - 3,
        "completion_tokens": 3,
        **fields,
    }
    return UsageEntry(**values)


async def _rows(factory: Factory) -> list[UsageLedger]:  # noqa: F811
    async with factory() as db:
        return list((await db.execute(select(UsageLedger))).scalars().all())


async def _used(factory: Factory, user: str, since: datetime = EVER) -> int:  # noqa: F811
    async with factory() as db:
        return await used_since(db, user, since)


class TestWriting:
    async def test_an_entry_becomes_a_row(self, factory: Factory) -> None:  # noqa: F811
        stored = await write_entries(
            [
                _entry(
                    org_id="org-1", agent_id="agent-1", cache_read_tokens=4, estimated=True
                ),
                _entry(model="fast-model", total=4, source_type="endpoint", source_id=None),
            ]
        )

        assert stored is True
        main, fast = sorted(await _rows(factory), key=lambda row: -row.total_tokens)
        assert (main.user_id, main.org_id, main.agent_id) == ("alice", "org-1", "agent-1")
        assert (main.source_type, main.source_id, main.entry) == ("conversation", "conv-1", "chat")
        assert (main.prompt_tokens, main.completion_tokens, main.total_tokens) == (7, 3, 10)
        assert (main.cache_read_tokens, main.estimated) == (4, True)
        assert (main.kind, main.unit) == ("usage", "llm_token")
        assert main.created_at is not None and main.id != fast.id
        assert (fast.model, fast.source_type, fast.source_id) == ("fast-model", "endpoint", None)

    async def test_usage_is_never_negative_or_empty(self, factory: Factory) -> None:  # noqa: F811
        assert await write_entries([_entry(total=0), _entry(total=-5)]) is True
        assert await write_entries([]) is True

        assert await _rows(factory) == []

    async def test_a_correction_is_a_further_row_and_may_be_negative(
        self, factory: Factory  # noqa: F811
    ) -> None:
        await write_entries([_entry(total=100)])
        await write_entries([_entry(total=-30, kind="adjustment")])

        assert await _used(factory, "alice") == 70
        assert len(await _rows(factory)) == 2

    async def test_over_long_values_are_cut_not_refused(self, factory: Factory) -> None:  # noqa: F811
        await write_entries([_entry(model="m" * 300, entry="e" * 90, source_id="s" * 200)])

        (row,) = await _rows(factory)
        assert (len(row.model or ""), len(row.entry), len(row.source_id or "")) == (100, 40, 64)

    async def test_a_failed_write_is_tried_again(
        self, factory: Factory, monkeypatch: pytest.MonkeyPatch  # noqa: F811
    ) -> None:
        own_session = ledger._own_session
        attempts = 0

        def _fails_once() -> Any:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ConnectionError("database restarting")
            return own_session()

        monkeypatch.setattr(ledger, "_own_session", _fails_once)

        assert await write_entries([_entry(total=10)]) is True
        assert attempts == 2
        assert await _used(factory, "alice") == 10

    async def test_a_write_whose_acknowledgement_was_lost_is_not_stored_twice(
        self, factory: Factory, monkeypatch: pytest.MonkeyPatch  # noqa: F811
    ) -> None:
        """The first commit went through, then the connection broke before it said so."""
        own_session = ledger._own_session
        attempts = 0

        @asynccontextmanager
        async def _ack_lost() -> AsyncIterator[Any]:
            nonlocal attempts
            attempts += 1
            async with own_session() as session:
                if attempts == 1:
                    commit = session.commit

                    async def _committed_then_lost() -> None:
                        await commit()
                        raise ConnectionError("connection lost after the commit")

                    session.commit = _committed_then_lost
                yield session

        monkeypatch.setattr(ledger, "_own_session", _ack_lost)

        assert await write_entries([_entry(total=10), _entry(total=4, model="fast")]) is True
        assert attempts == 2
        assert await _used(factory, "alice") == 14
        assert len(await _rows(factory)) == 2

    async def test_a_write_that_cannot_be_made_is_logged_with_its_numbers_and_never_raises(
        self,
        factory: Factory,  # noqa: F811
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        def _down() -> Any:
            raise ConnectionError("database is gone")

        monkeypatch.setattr(ledger, "_own_session", _down)

        with caplog.at_level(logging.ERROR, logger="fim_one.core.usage.ledger"):
            stored = await write_entries([_entry(total=1234, source_id="conv-9")])

        assert stored is False
        (record,) = caplog.records
        message = record.getMessage()
        # Enough to restore it by hand with an adjustment row.
        for part in ("user=alice", "source=conversation:conv-9", "model=main-model", "total=1234"):
            assert part in message, message

    async def test_rows_written_at_the_same_moment_are_all_kept(
        self, factory: Factory  # noqa: F811
    ) -> None:
        """Two workers finishing turns of one user at once: plain inserts,
        nothing read and written back."""
        results = await asyncio.gather(
            *(write_entries([_entry(total=7), _entry(model="fast", total=3)]) for _ in range(40))
        )

        assert all(results)
        assert await _used(factory, "alice") == 400
        assert len(await _rows(factory)) == 80

    async def test_used_only_ever_grows_while_rows_are_written(
        self, factory: Factory  # noqa: F811
    ) -> None:
        seen: list[int] = []
        writing = asyncio.gather(*(write_entries([_entry(total=5)]) for _ in range(30)))

        async def _watch() -> None:
            while not writing.done():
                seen.append(await _used(factory, "alice"))
                await asyncio.sleep(0)

        await asyncio.gather(writing, _watch())
        seen.append(await _used(factory, "alice"))

        assert seen == sorted(seen)
        assert seen[-1] == 150


class TestReading:
    async def test_used_since_counts_the_users_rows_in_the_window(
        self, factory: Factory  # noqa: F811
    ) -> None:
        now = datetime.now(UTC)
        async with factory() as db:
            db.add_all(
                [
                    UsageLedger(user_id="alice", source_type="conversation", entry="chat",
                                total_tokens=100, created_at=now - timedelta(days=40)),
                    UsageLedger(user_id="alice", source_type="conversation", entry="chat",
                                total_tokens=30, created_at=now - timedelta(days=2)),
                    UsageLedger(user_id="alice", source_type="workflow_run", entry="trigger",
                                total_tokens=5, kind="backfill", created_at=now - timedelta(days=1)),
                    UsageLedger(user_id="bob", source_type="conversation", entry="chat",
                                total_tokens=999, created_at=now),
                    UsageLedger(user_id=None, source_type="endpoint", entry="unbound",
                                total_tokens=1, created_at=now),
                ]
            )  # fmt: skip
            await db.commit()

        assert await _used(factory, "alice", now - timedelta(days=30)) == 35
        assert await _used(factory, "alice") == 135
        assert await _used(factory, "carol") == 0
        async with factory() as db:
            assert await used_by_users_since(db, now - timedelta(days=30)) == {
                "alice": 35,
                "bob": 999,
            }
            assert await used_by_users_since(db, EVER, user_ids=["alice", "carol"]) == {
                "alice": 135
            }
            assert await used_by_users_since(db, EVER, user_ids=[]) == {}

    async def test_a_row_at_the_instant_the_window_begins_is_in_it(
        self, factory: Factory  # noqa: F811
    ) -> None:
        """The window is ``created_at >= since``: the first instant in, the one before out."""
        from fim_one.core.usage.ledger import used_by_day, used_by_origin, usage_per_user_subquery

        since = datetime(2026, 11, 1, tzinfo=UTC)
        async with factory() as db:
            db.add_all(
                [
                    UsageLedger(user_id="alice", source_type="conversation", entry="chat",
                                total_tokens=7, created_at=since),
                    UsageLedger(user_id="alice", source_type="conversation", entry="chat",
                                total_tokens=100, created_at=since - timedelta(microseconds=1)),
                ]
            )  # fmt: skip
            await db.commit()

        assert await _used(factory, "alice", since) == 7
        assert await _used(factory, "alice", since + timedelta(microseconds=1)) == 0
        async with factory() as db:
            assert await used_by_users_since(db, since) == {"alice": 7}
            assert [total for _day, total in await used_by_day(db, "alice", since)] == [7]
            assert [b.total_tokens for b in await used_by_origin(db, "alice", since)] == [7]
            per_user = usage_per_user_subquery(since)
            assert (await db.execute(select(per_user.c.total_tokens))).scalars().all() == [7]

    async def test_used_is_never_reported_below_zero(self, factory: Factory) -> None:  # noqa: F811
        await write_entries([_entry(total=10), _entry(total=-50, kind="adjustment")])

        assert await _used(factory, "alice") == 0
        async with factory() as db:
            assert await used_by_users_since(db, EVER) == {"alice": 0}

    async def test_the_sum_uses_the_user_and_time_index(self, factory: Factory) -> None:  # noqa: F811
        async with factory() as db:
            if db.get_bind().dialect.name != "sqlite":
                pytest.skip("the plan is read on SQLite; PostgreSQL has the same index")
            plan = (
                await db.execute(
                    text(
                        "EXPLAIN QUERY PLAN SELECT COALESCE(SUM(total_tokens), 0) FROM usage_ledger"
                        " WHERE user_id = 'alice' AND created_at >= '2026-11-01'"
                    )
                )
            ).all()
        assert "ix_usage_ledger_user_created" in str(plan)


class TestPlatformFigures:
    """The reads the statistics pages use: everyone's rows, grouped."""

    async def _seed(self, factory: Factory) -> datetime:  # noqa: F811
        day = datetime(2026, 11, 10, 12, tzinfo=UTC)
        async with factory() as db:
            db.add_all(
                [
                    UsageLedger(user_id="alice", source_type="conversation", entry="chat",
                                agent_id="agent-1", model="main-model", total_tokens=100,
                                created_at=day - timedelta(days=1)),
                    UsageLedger(user_id="alice", source_type="conversation", entry="background",
                                agent_id="agent-1", model=None, kind="backfill",
                                total_tokens=7, created_at=day - timedelta(days=1)),
                    UsageLedger(user_id="bob", source_type="workflow_run", entry="manual",
                                model="main-model", total_tokens=20, created_at=day),
                    UsageLedger(user_id=None, source_type="endpoint", entry="unbound",
                                model="fast-model", total_tokens=3, created_at=day),
                    UsageLedger(user_id="bob", source_type="conversation", entry="chat",
                                agent_id="agent-2", model="main-model", total_tokens=-5,
                                kind="adjustment", created_at=day),
                ]
            )  # fmt: skip
            await db.commit()
        return day

    async def test_totals_in_a_window(self, factory: Factory) -> None:  # noqa: F811
        from fim_one.core.usage.ledger import used_between, used_by_user_between

        day = await self._seed(factory)
        async with factory() as db:
            assert await used_between(db) == 125
            assert await used_between(db, since=day) == 18
            assert await used_between(db, until=day) == 107
            assert await used_by_user_between(db, "alice") == 107
            assert await used_by_user_between(db, "alice", since=day) == 0
            assert await used_by_user_between(db, "bob", until=day) == 0
            assert await used_by_user_between(db, "bob", since=day) == 15

    async def test_a_per_user_read_without_a_user_is_refused(
        self, factory: Factory  # noqa: F811
    ) -> None:
        """Everyone's figure is its own function: a missing id raises
        instead of reading every user's rows."""
        from fim_one.core.usage.ledger import used_between, used_by, used_by_user_between

        await self._seed(factory)
        async with factory() as db:
            for missing in (None, ""):
                with pytest.raises(ValueError):
                    await used_by_user_between(db, missing)  # type: ignore[arg-type]
            with pytest.raises(TypeError):
                await used_between(db, user_id="alice")  # type: ignore[call-arg]
            with pytest.raises(TypeError):
                await used_by(db, ["day"], user_id="alice")  # type: ignore[call-arg]

    async def test_grouped_by_day_model_and_background(self, factory: Factory) -> None:  # noqa: F811
        from fim_one.core.usage.ledger import UsageSlice, used_by

        await self._seed(factory)
        async with factory() as db:
            assert await used_by(db, ["day"]) == [
                UsageSlice(total_tokens=107, day="2026-11-09"),
                UsageSlice(total_tokens=18, day="2026-11-10"),
            ]
            by_model = await used_by(db, ["model", "background"])
            assert sorted(by_model, key=lambda s: (s.model or "", s.background)) == [
                UsageSlice(total_tokens=7, model=None, background=True),
                UsageSlice(total_tokens=3, model="fast-model", background=False),
                UsageSlice(total_tokens=115, model="main-model", background=False),
            ]
            assert await used_by(db, []) == [UsageSlice(total_tokens=125)]
            with pytest.raises(ValueError):
                await used_by(db, ["user"])  # type: ignore[list-item]

    async def test_per_agent(self, factory: Factory) -> None:  # noqa: F811
        from fim_one.core.usage.ledger import usage_per_agent_subquery

        day = await self._seed(factory)
        async with factory() as db:
            every = usage_per_agent_subquery()
            found = dict((await db.execute(select(every.c.agent_id, every.c.total_tokens))).all())
            assert found == {"agent-1": 107, "agent-2": -5}
            recent = usage_per_agent_subquery(day)
            rows = (await db.execute(select(recent.c.agent_id, recent.c.total_tokens))).all()
            assert dict(rows) == {"agent-2": -5}


class TestOnlyTheLedgerWritesAndNothingRewrites:
    SRC = Path(fim_one.__file__).parent

    def _sources(self) -> list[tuple[str, str]]:
        return [
            (str(path.relative_to(self.SRC)), path.read_text(encoding="utf-8"))
            for path in self.SRC.rglob("*.py")
        ]

    def test_rows_are_built_in_one_module(self) -> None:
        builds = re.compile(r"(?<!class )\bUsageLedger\(")
        building = sorted(name for name, source in self._sources() if builds.search(source))
        assert building == ["core/usage/ledger.py"]

    def test_no_code_updates_or_deletes_a_row(self) -> None:
        """Only the migration that fills the table may touch what is in it."""
        rewriting = re.compile(
            r"(?:update|delete)\(\s*UsageLedger\b"
            r"|(?:UPDATE|DELETE\s+FROM)\s+usage_ledger\b"
            r"|\.delete\(\s*\w*ledger\w*\s*\)",
            re.IGNORECASE,
        )
        offenders = sorted(
            name
            for name, source in self._sources()
            if rewriting.search(source) and not name.startswith("migrations/")
        )
        assert offenders == []

"""Audit records are kept for a period, then removed by a daily sweep.

``AUDIT_RETENTION_DAYS`` (audit log, sign-in history, review trail;
default 365, never below 180) and ``CALL_LOG_RETENTION_DAYS`` (call
records; default 180); ``0`` keeps for ever. Every sweep leaves one
``audit.retention_sweep`` event. SQLite always; PostgreSQL as well when
``FIM_TEST_PG_URL`` is set.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.audit import actions
from fim_one.core.audit.retention import (
    AuditRetentionSweeper,
    Retention,
    retention_from_env,
)
from fim_one.db.base import Base
from fim_one.db.models import AuditLog, ConnectorCallLog, LoginHistory, User
from fim_one.db.models.channel import ConfirmationRequest
from fim_one.db.models.review_log import ReviewLog
from fim_one.db.models.usage_ledger import UsageLedger

# The sweep's own record is stamped by the database, so the tests' clock is the real one.
NOW = datetime.now(UTC).replace(microsecond=0)


def _backends() -> list[Any]:
    return [
        pytest.param("sqlite", id="sqlite"),
        pytest.param(
            "postgresql",
            id="postgresql",
            marks=pytest.mark.skipif(
                not os.environ.get("FIM_TEST_PG_URL"), reason="FIM_TEST_PG_URL not set"
            ),
        ),
    ]


Factory = async_sessionmaker[AsyncSession]


@pytest.fixture(params=_backends())
async def factory(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Factory]:
    """An empty database the sweeper's own sessions reach."""
    import fim_one.db.engine as db_engine

    admin = None
    schema = f"audit_retention_{uuid.uuid4().hex[:12]}"
    if request.param == "sqlite":
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'retention.db'}",
            connect_args={"timeout": 30},
        )
    else:
        url = make_url(os.environ["FIM_TEST_PG_URL"]).set(drivername="postgresql+asyncpg")
        admin = create_async_engine(url)
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(
            url, pool_size=10, connect_args={"server_settings": {"search_path": schema}}
        )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        made = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(db_engine, "_engine", engine)
        monkeypatch.setattr(db_engine, "_session_factory", made)
        yield made
    finally:
        await engine.dispose()
        if admin is not None:
            async with admin.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await admin.dispose()


def _ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


def _event(days: float, action: str = "settings.update") -> AuditLog:
    return AuditLog(
        id=str(uuid.uuid4()), actor_id=None, actor_label="root", actor_kind="admin",
        action=action, outcome="success", ip_address="198.51.100.9", created_at=_ago(days),
    )  # fmt: skip


def _login(days: float, **fields: Any) -> LoginHistory:
    return LoginHistory(
        id=str(uuid.uuid4()), username="ann", ip_address="198.51.100.9", success=False,
        created_at=_ago(days), **fields,
    )  # fmt: skip


def _review(days: float) -> ReviewLog:
    return ReviewLog(
        org_id="org-a", resource_type="agent", resource_id="a1", resource_name="Helper",
        action="approved", created_at=_ago(days).replace(tzinfo=None),
    )  # fmt: skip


def _call(days: float) -> ConnectorCallLog:
    return ConnectorCallLog(
        id=str(uuid.uuid4()), connector_name="CRM", action_name="list", request_method="GET",
        request_url="https://crm.example.test/x", success=True, created_at=_ago(days),
    )  # fmt: skip


def _request(days: float, status: str = "approved") -> ConfirmationRequest:
    return ConfirmationRequest(
        id=str(uuid.uuid4()), mode="inline", kind="confirmation", status=status,
        payload={"tool_name": "send_mail", "tool_args": {"to": "ceo@example.test"}},
        decided_by_user_id="u-1", decision_note="fine", created_at=_ago(days),
    )  # fmt: skip


async def _add(factory: Factory, *rows: Any) -> None:
    async with factory() as db:
        db.add_all(rows)
        await db.commit()


async def _count(factory: Factory, model: Any, *where: Any) -> int:
    async with factory() as db:
        return int(await db.scalar(select(func.count()).select_from(model).where(*where)) or 0)


async def _sweeps(factory: Factory) -> list[AuditLog]:
    async with factory() as db:
        rows = await db.execute(
            select(AuditLog).where(AuditLog.action == actions.AUDIT_RETENTION_SWEEP)
        )
        return list(rows.scalars().all())


class TestThePeriods:
    def test_the_defaults(self) -> None:
        assert retention_from_env({}) == Retention(audit_days=365, call_log_days=180)

    def test_the_environment_sets_them(self) -> None:
        env = {"AUDIT_RETENTION_DAYS": "730", "CALL_LOG_RETENTION_DAYS": "30"}
        assert retention_from_env(env) == Retention(audit_days=730, call_log_days=30)

    @pytest.mark.parametrize("asked", ["1", "30", "179"])
    def test_audit_records_are_never_kept_less_than_180_days(
        self, asked: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            kept = retention_from_env({"AUDIT_RETENTION_DAYS": asked})

        assert kept.audit_days == 180
        assert "below the minimum" in caplog.text
        # However the periods are built.
        assert Retention(audit_days=int(asked)).audit_days == 180

    def test_call_records_have_no_floor(self) -> None:
        assert retention_from_env({"CALL_LOG_RETENTION_DAYS": "7"}).call_log_days == 7

    def test_zero_is_for_ever(self) -> None:
        kept = retention_from_env({"AUDIT_RETENTION_DAYS": "0", "CALL_LOG_RETENTION_DAYS": "0"})
        assert (kept.audit_days, kept.call_log_days) == (0, 0) and not kept.sweeps

    @pytest.mark.parametrize("value", ["soon", "-5", "1.5"])
    def test_a_value_that_is_not_a_number_of_days_is_the_default(self, value: str) -> None:
        kept = retention_from_env({"AUDIT_RETENTION_DAYS": value, "CALL_LOG_RETENTION_DAYS": value})
        assert kept == Retention(audit_days=365, call_log_days=180)


class TestASweep:
    async def test_what_is_past_its_period_goes_and_the_rest_stays(self, factory: Factory) -> None:
        old_event, new_event = _event(366), _event(364)
        old_login, new_login = _login(400), _login(10)
        old_review, new_review = _review(366), _review(364)
        # Calls have the shorter period.
        old_call, new_call = _call(181), _call(179)
        await _add(factory, old_event, new_event, old_login, new_login, old_review, new_review,
                   old_call, new_call)  # fmt: skip

        counts = await AuditRetentionSweeper(Retention()).sweep(now=NOW)

        assert counts == {
            "audit_logs": 1, "login_history": 1, "review_log": 1, "connector_call_logs": 1,
            "confirmation_payloads": 0,
        }  # fmt: skip
        async with factory() as db:
            for gone in (old_event, old_login, old_review, old_call):
                assert await db.get(type(gone), gone.id) is None, type(gone).__name__
            for kept in (new_event, new_login, new_review, new_call):
                assert await db.get(type(kept), kept.id) is not None, type(kept).__name__

    async def test_it_leaves_one_record_with_what_went_from_each_table(
        self, factory: Factory
    ) -> None:
        await _add(factory, _event(500), _event(500), _call(200))

        await AuditRetentionSweeper(Retention()).sweep(now=NOW)

        (record,) = await _sweeps(factory)
        assert (record.actor_kind, record.actor_label) == ("system", "audit-retention")
        assert record.actor_id is None and record.ip_address is None
        assert record.detail == (
            "removed: audit_logs=2, login_history=0, review_log=0, connector_call_logs=1, "
            "confirmation_payloads=0; kept: audit 365 days, calls 180 days"
        )

    async def test_a_sweep_with_nothing_to_remove_still_says_it_ran(self, factory: Factory) -> None:
        counts = await AuditRetentionSweeper(Retention()).sweep(now=NOW)

        assert counts is not None and not any(counts.values())
        assert len(await _sweeps(factory)) == 1

    async def test_rows_go_in_batches(self, factory: Factory) -> None:
        await _add(factory, *[_login(400) for _ in range(23)], _login(1))

        counts = await AuditRetentionSweeper(Retention(), batch_size=5).sweep(now=NOW)

        assert counts is not None and counts["login_history"] == 23
        assert await _count(factory, LoginHistory) == 1

    async def test_zero_keeps_that_class_for_ever(self, factory: Factory) -> None:
        await _add(factory, _event(5000), _login(5000), _review(5000), _call(5000))

        counts = await AuditRetentionSweeper(Retention(audit_days=0)).sweep(now=NOW)

        assert counts is not None
        assert (counts["audit_logs"], counts["login_history"], counts["review_log"]) == (0, 0, 0)
        assert counts["connector_call_logs"] == 1
        assert await _count(factory, LoginHistory) == 1 and await _count(factory, ReviewLog) == 1

    async def test_both_zero_and_nothing_runs_and_nothing_is_recorded(
        self, factory: Factory
    ) -> None:
        await _add(factory, _event(5000), _call(5000))
        sweeper = AuditRetentionSweeper(Retention(audit_days=0, call_log_days=0))

        assert await sweeper.sweep(now=NOW) is None
        await asyncio.wait_for(sweeper.run_loop(), timeout=1)  # returns at once

        assert await _count(factory, AuditLog) == 1 and await _count(factory, ConnectorCallLog) == 1

    async def test_a_period_below_the_floor_removes_nothing_younger_than_180_days(
        self, factory: Factory
    ) -> None:
        await _add(factory, _event(100), _login(100), _review(100), _event(181))
        sweeper = AuditRetentionSweeper(retention_from_env({"AUDIT_RETENTION_DAYS": "30"}))

        counts = await sweeper.sweep(now=NOW)

        assert counts is not None
        assert (counts["audit_logs"], counts["login_history"], counts["review_log"]) == (1, 0, 0)

    async def test_the_usage_ledger_is_not_touched(self, factory: Factory) -> None:
        await _add(
            factory,
            UsageLedger(id=str(uuid.uuid4()), user_id="u-1", source_type="conversation",
                        entry="chat", total_tokens=10, created_at=_ago(5000)),
        )  # fmt: skip

        await AuditRetentionSweeper(Retention()).sweep(now=NOW)

        assert await _count(factory, UsageLedger) == 1


class TestApprovalRequests:
    async def test_a_decided_request_keeps_its_decision_and_loses_its_arguments(
        self, factory: Factory
    ) -> None:
        old, recent, waiting = _request(400), _request(10), _request(400, status="pending")
        await _add(factory, old, recent, waiting)

        counts = await AuditRetentionSweeper(Retention()).sweep(now=NOW)

        assert counts is not None and counts["confirmation_payloads"] == 1
        async with factory() as db:
            swept = await db.get(ConfirmationRequest, old.id)
            assert swept is not None and swept.payload is None
            assert (swept.status, swept.decided_by_user_id, swept.decision_note) == (
                "approved", "u-1", "fine",
            )  # fmt: skip
            assert (await db.get(ConfirmationRequest, recent.id)).payload is not None
            # Still to be decided: an approver must be able to see what for.
            assert (await db.get(ConfirmationRequest, waiting.id)).payload is not None

    async def test_a_cleared_request_is_not_counted_again(self, factory: Factory) -> None:
        await _add(factory, _request(400))
        sweeper = AuditRetentionSweeper(Retention())

        await sweeper.sweep(now=NOW)
        again = await sweeper.sweep(now=NOW + timedelta(days=1))

        assert again is not None and again["confirmation_payloads"] == 0


class TestOnceADay:
    async def test_a_second_sweep_within_the_interval_does_nothing(self, factory: Factory) -> None:
        sweeper = AuditRetentionSweeper(Retention())
        assert await sweeper.sweep(now=NOW) is not None
        await _add(factory, _event(900))

        # Another replica, or this process after a restart, an hour later.
        assert await AuditRetentionSweeper(Retention()).sweep(now=NOW + timedelta(hours=1)) is None

        assert len(await _sweeps(factory)) == 1
        assert await _count(factory, AuditLog, AuditLog.action == "settings.update") == 1

    async def test_the_next_day_it_sweeps_again(self, factory: Factory) -> None:
        sweeper = AuditRetentionSweeper(Retention())
        await sweeper.sweep(now=NOW)
        await _add(factory, _event(900))

        counts = await sweeper.sweep(now=NOW + timedelta(hours=23))

        assert counts is not None and counts["audit_logs"] == 1
        assert len(await _sweeps(factory)) == 2

    async def test_replicas_sweeping_at_the_same_moment_remove_each_row_once(
        self, factory: Factory
    ) -> None:
        await _add(factory, *[_login(400) for _ in range(40)])
        sweepers = [AuditRetentionSweeper(Retention(), batch_size=7) for _ in range(4)]

        results = await asyncio.gather(*(s.sweep(now=NOW) for s in sweepers))

        ran = [r for r in results if r is not None]
        assert sum(r["login_history"] for r in ran) == 40
        assert await _count(factory, LoginHistory) == 0
        if factory.kw["bind"].dialect.name == "postgresql":
            # The advisory lock lets exactly one through.
            assert len(ran) == 1 and len(await _sweeps(factory)) == 1

    async def test_the_loop_survives_a_failing_sweep(
        self, factory: Factory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = 0

        async def sweep(self: AuditRetentionSweeper, **_: Any) -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError("database away")

        async def no_wait(_seconds: float) -> None:
            if calls >= 3:
                raise asyncio.CancelledError

        monkeypatch.setattr(AuditRetentionSweeper, "sweep", sweep)
        monkeypatch.setattr("fim_one.core.audit.retention.asyncio.sleep", no_wait)

        await AuditRetentionSweeper(Retention()).run_loop()  # ends on cancellation, quietly

        assert calls == 3


class TestWhatReadsTheseTablesStillWorks:
    async def test_the_two_factor_attempt_limit_counts_rows_no_sweep_removes(
        self, factory: Factory
    ) -> None:
        """The limiter counts failed codes of the last minutes; the shortest
        period a sweep can have is 180 days."""
        from fim_one.web.api import auth as auth_api

        user = User(id=str(uuid.uuid4()), username="ann", email="ann@example.test")
        recent = NOW - timedelta(minutes=auth_api._2FA_FAILURE_WINDOW_MINUTES - 1)
        await _add(
            factory, user,
            *[
                LoginHistory(id=str(uuid.uuid4()), user_id=user.id, success=False,
                             failure_reason="2fa_code_invalid", created_at=recent)
                for _ in range(auth_api._2FA_MAX_FAILURES)
            ],
        )  # fmt: skip
        sweeper = AuditRetentionSweeper(
            retention_from_env({"AUDIT_RETENTION_DAYS": "1", "CALL_LOG_RETENTION_DAYS": "1"})
        )

        await sweeper.sweep(now=NOW)

        window_start = NOW - timedelta(minutes=auth_api._2FA_FAILURE_WINDOW_MINUTES)
        assert await _count(
            factory, LoginHistory,
            LoginHistory.user_id == user.id,
            LoginHistory.failure_reason == "2fa_code_invalid",
            LoginHistory.created_at >= window_start,
        ) == auth_api._2FA_MAX_FAILURES  # fmt: skip
        assert timedelta(days=sweeper.retention.audit_days) > timedelta(
            minutes=auth_api._2FA_FAILURE_WINDOW_MINUTES
        )


class TestTheLockOnPostgres:
    async def test_no_session_of_the_sweep_holds_a_snapshot_while_it_runs(
        self, factory: Factory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An open transaction pins ``backend_xmin`` and VACUUM removes nothing
        database-wide; the lock is held on a connection outside any transaction."""
        if factory.kw["bind"].dialect.name != "postgresql":
            pytest.skip("advisory locks are PostgreSQL's")
        await _add(factory, *[_event(900) for _ in range(3)])
        seen: list[Any] = []
        second: list[Any] = []
        original = AuditRetentionSweeper._delete_older

        async def watched(
            self: AuditRetentionSweeper, model: Any, cutoff: datetime, **kw: Any
        ) -> int:
            if not seen:
                async with factory() as db:
                    rows = await db.execute(
                        text(
                            "SELECT pid, state, backend_xmin FROM pg_stat_activity "
                            "WHERE datname = current_database() AND pid <> pg_backend_pid() "
                            "AND state LIKE 'idle in transaction%'"
                        )
                    )
                    seen.append(list(rows.all()))
                second.append(await AuditRetentionSweeper(Retention()).sweep(now=NOW))
            return await original(self, model, cutoff, **kw)

        monkeypatch.setattr(AuditRetentionSweeper, "_delete_older", watched)

        counts = await AuditRetentionSweeper(Retention()).sweep(now=NOW)

        assert counts is not None and counts["audit_logs"] == 3
        assert seen == [[]]
        assert second == [None]  # the lock was held
        # Released afterwards: the lock can be taken again.
        async with factory() as db:
            assert await db.scalar(
                text("SELECT pg_try_advisory_lock(hashtext('fim_one.audit_retention_sweep'))")
            )
            await db.execute(
                text("SELECT pg_advisory_unlock(hashtext('fim_one.audit_retention_sweep'))")
            )


class TestATableThatFails:
    async def test_the_other_tables_are_swept_and_the_record_names_the_failure(
        self, factory: Factory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _add(factory, *[_login(900) for _ in range(3)], _review(900), _call(900))
        pauses = 0

        async def pause(_seconds: float) -> None:
            # Between batches; the second pause is in the sign-in history,
            # after two of its rows went.
            nonlocal pauses
            pauses += 1
            if pauses == 2:
                raise RuntimeError("database away")

        monkeypatch.setattr("fim_one.core.audit.retention.asyncio.sleep", pause)

        counts = await AuditRetentionSweeper(Retention(), batch_size=1).sweep(now=NOW)

        assert counts is not None
        # What went before the failure is counted; the tables after it ran.
        assert counts["login_history"] == 2 and await _count(factory, LoginHistory) == 1
        assert counts["review_log"] == 1 and counts["connector_call_logs"] == 1
        [record] = await _sweeps(factory)
        assert record.outcome == "failed"
        assert record.detail is not None
        assert "login_history=2" in record.detail and "review_log=1" in record.detail
        assert record.detail.endswith("failed: login_history")

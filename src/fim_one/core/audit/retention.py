"""How long audit records are kept, and the sweep that removes older ones.

Two periods, read from the environment (:func:`retention_from_env`):

- ``AUDIT_RETENTION_DAYS`` (default 365, never below 180): ``audit_logs``,
  ``login_history`` and ``review_log``. At the same age the ``payload`` of
  a decided approval request (the arguments an approver was shown) is
  cleared; the request and its decision stay.
- ``CALL_LOG_RETENTION_DAYS`` (default 180): ``connector_call_logs``.

``0`` keeps the records of that period for ever. The usage ledger is never
swept.

:class:`AuditRetentionSweeper` runs one sweep a day. Rows go in small
batches, each in a transaction of its own, so no lock is held for long.
Every sweep leaves one ``audit.retention_sweep`` event with what it
removed from each table; that event is also how a process knows that
today's sweep was done, by itself before a restart or by another replica.
On PostgreSQL an advisory lock keeps two replicas from sweeping at once.

Besides user deletion, this is the only code that removes or changes an
audit record (``tests/test_architecture.py``).

This module imports nothing from ``fim_one.web``; ORM models are imported
inside functions.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import random
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from . import actions
from . import writer as audit

__all__ = [
    "AUDIT_RETENTION_FLOOR_DAYS",
    "DEFAULT_AUDIT_RETENTION_DAYS",
    "DEFAULT_CALL_LOG_RETENTION_DAYS",
    "AuditRetentionSweeper",
    "Retention",
    "retention_from_env",
]

logger = logging.getLogger(__name__)

DEFAULT_AUDIT_RETENTION_DAYS = 365
DEFAULT_CALL_LOG_RETENTION_DAYS = 180
# Network logs are to be kept for six months or more.
AUDIT_RETENTION_FLOOR_DAYS = 180

# Names the sweep among PostgreSQL advisory locks.
_LOCK_NAME = "fim_one.audit_retention_sweep"


@dataclass(frozen=True)
class Retention:
    """How many days each class of record is kept; ``0`` is for ever."""

    audit_days: int = DEFAULT_AUDIT_RETENTION_DAYS
    call_log_days: int = DEFAULT_CALL_LOG_RETENTION_DAYS

    def __post_init__(self) -> None:
        # Whoever builds one, the floor holds.
        if 0 < self.audit_days < AUDIT_RETENTION_FLOOR_DAYS:
            object.__setattr__(self, "audit_days", AUDIT_RETENTION_FLOOR_DAYS)
        if self.audit_days < 0 or self.call_log_days < 0:
            raise ValueError("a retention period is a number of days, or 0 for ever")

    @property
    def sweeps(self) -> bool:
        """Whether anything is ever removed."""
        return self.audit_days > 0 or self.call_log_days > 0


def _days(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = (environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value < 0:
        logger.warning("%s=%r is not a number of days; using %d", name, raw, default)
        return default
    return value


def retention_from_env(environ: Mapping[str, str] | None = None) -> Retention:
    """The retention periods the environment asks for.

    A value that is not a whole number of days falls back to the default.
    ``AUDIT_RETENTION_DAYS`` between 1 and 179 is raised to 180, and says so
    in the log.
    """
    env = os.environ if environ is None else environ
    audit_days = _days(env, "AUDIT_RETENTION_DAYS", DEFAULT_AUDIT_RETENTION_DAYS)
    if 0 < audit_days < AUDIT_RETENTION_FLOOR_DAYS:
        logger.warning(
            "AUDIT_RETENTION_DAYS=%d is below the minimum; audit records are kept %d days",
            audit_days,
            AUDIT_RETENTION_FLOOR_DAYS,
        )
    return Retention(
        audit_days=audit_days,
        call_log_days=_days(env, "CALL_LOG_RETENTION_DAYS", DEFAULT_CALL_LOG_RETENTION_DAYS),
    )


def _aware(moment: datetime) -> datetime:
    """*moment* in UTC; SQLite hands timestamps back without their zone.

    A moment in another zone is converted: the review trail is compared on
    the UTC wall clock.
    """
    return moment.astimezone(UTC) if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


class AuditRetentionSweeper:
    """Removes audit records older than their retention period, once a day.

    Parameters
    ----------
    retention:
        The two periods. Defaults to what the environment asks for.
    interval_hours:
        How far apart two sweeps are.
    check_interval_seconds:
        How often the loop looks whether a sweep is due. A sweep is due
        when the last ``audit.retention_sweep`` event is older than the
        interval, so a process that restarts every few hours still sweeps
        daily, and several replicas sweep once between them.
    batch_size:
        Rows removed per transaction.
    """

    def __init__(
        self,
        retention: Retention | None = None,
        *,
        interval_hours: float = 24,
        check_interval_seconds: float = 3600,
        batch_size: int = 500,
    ) -> None:
        self.retention = retention if retention is not None else retention_from_env()
        self.interval_hours = interval_hours
        self.check_interval_seconds = check_interval_seconds
        self.batch_size = max(1, batch_size)

    # ------------------------------------------------------------------
    # One sweep
    # ------------------------------------------------------------------

    async def sweep(self, *, now: datetime | None = None) -> dict[str, int] | None:
        """Sweep if one is due. Returns what was removed per table.

        ``None`` when nothing was done: retention is off, another process
        is sweeping, or a sweep already ran within the interval.
        """
        from fim_one.db import create_session

        if not self.retention.sweeps:
            return None
        moment = _aware(now) if now is not None else datetime.now(UTC)

        async with contextlib.AsyncExitStack() as stack:
            async with create_session() as probe:
                bind = probe.bind
            on_postgres = bind is not None and bind.dialect.name == "postgresql"
            if on_postgres and not await self._take_lock(stack, bind):
                logger.debug("Audit retention: another process is sweeping")
                return None
            if not await self._due(moment):
                return None
            counts, failed = await self._remove_expired(moment)
            await self._leave_a_record(counts, failed)
        return counts

    @staticmethod
    async def _take_lock(stack: contextlib.AsyncExitStack, bind: Any) -> bool:
        """Take the sweep's advisory lock for as long as *stack* is open.

        A session-level lock on a connection of its own in autocommit: the
        connection sits idle outside any transaction, so it holds no
        snapshot back from VACUUM. The lock goes with ``pg_advisory_unlock``
        at the end, or with the connection if that drops.
        """
        from sqlalchemy import text

        conn = await stack.enter_async_context(bind.connect())
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        held = await conn.scalar(
            text("SELECT pg_try_advisory_lock(hashtext(:name))"), {"name": _LOCK_NAME}
        )
        if not held:
            return False

        async def release() -> None:
            try:
                await conn.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:name))"), {"name": _LOCK_NAME}
                )
            except Exception:
                # Not back into the pool still holding the lock.
                logger.warning("Audit retention: could not release the lock", exc_info=True)
                await conn.invalidate()

        stack.push_async_callback(release)
        return True

    async def _due(self, moment: datetime) -> bool:
        from sqlalchemy import func, select

        from fim_one.db import create_session
        from fim_one.db.models.audit_log import AuditLog

        async with create_session() as db:
            last = await db.scalar(
                select(func.max(AuditLog.created_at)).where(
                    AuditLog.action == actions.AUDIT_RETENTION_SWEEP
                )
            )
        if last is None:
            return True
        if isinstance(last, str):
            # An aggregate over a SQLite timestamp column comes back as text.
            last = datetime.fromisoformat(last)
        # A little under the interval, so a daily sweep does not drift later
        # by one check interval a day.
        return moment - _aware(last) >= timedelta(hours=self.interval_hours * 0.9)

    async def _remove_expired(self, moment: datetime) -> tuple[dict[str, int], list[str]]:
        """Remove what is past its period. Returns the counts and the tables that failed.

        A table that fails is logged and named; the others are still swept,
        and what that table had already removed stays counted.
        """
        from fim_one.db.models.audit_log import AuditLog
        from fim_one.db.models.connector_call_log import ConnectorCallLog
        from fim_one.db.models.login_history import LoginHistory
        from fim_one.db.models.review_log import ReviewLog

        counts = {
            "audit_logs": 0,
            "login_history": 0,
            "review_log": 0,
            "connector_call_logs": 0,
            "confirmation_payloads": 0,
        }
        failed: list[str] = []

        async def step(table: str, work: Awaitable[int]) -> None:
            try:
                counts[table] = await work
            except Exception:
                logger.exception("Audit retention: sweeping %s failed", table)
                failed.append(table)

        if self.retention.audit_days > 0:
            cutoff = moment - timedelta(days=self.retention.audit_days)
            # ``review_log.created_at`` carries no timezone; it is written in UTC.
            naive = cutoff.replace(tzinfo=None)
            await step("audit_logs", self._delete_older(AuditLog, cutoff, tally=counts))
            await step("login_history", self._delete_older(LoginHistory, cutoff, tally=counts))
            await step("review_log", self._delete_older(ReviewLog, naive, tally=counts))
            await step("confirmation_payloads", self._clear_payloads(cutoff, tally=counts))
        if self.retention.call_log_days > 0:
            cutoff = moment - timedelta(days=self.retention.call_log_days)
            await step(
                "connector_call_logs", self._delete_older(ConnectorCallLog, cutoff, tally=counts)
            )
        return counts, failed

    async def _delete_older(
        self, model: Any, cutoff: datetime, *, tally: dict[str, int] | None = None
    ) -> int:
        """Delete the rows of *model* created before *cutoff*, a batch at a time.

        With *tally*, its entry for the table follows the count batch by batch, so what
        was removed before a failure is still reported.

        The only ``DELETE`` on the four record tables besides user deletion.
        """
        from sqlalchemy import CursorResult, delete, select

        from fim_one.db import create_session

        removed = 0
        while True:
            async with create_session() as db:
                ids = (
                    (
                        await db.execute(
                            select(model.id).where(model.created_at < cutoff).limit(self.batch_size)
                        )
                    )
                    .scalars()
                    .all()
                )
                if not ids:
                    return removed
                result = cast(
                    CursorResult[Any],
                    # Another replica may have removed some already: the
                    # count is what this statement removed.
                    await db.execute(delete(model).where(model.id.in_(ids))),
                )
                await db.commit()
                removed += max(result.rowcount, 0)
                if tally is not None:
                    tally[model.__tablename__] = removed
            if len(ids) < self.batch_size:
                return removed
            await asyncio.sleep(0)  # let requests run between batches

    async def _clear_payloads(
        self, cutoff: datetime, *, tally: dict[str, int] | None = None
    ) -> int:
        """Clear what approvers were shown on requests decided before *cutoff*.

        The payload holds the arguments of the call that asked. The request
        row, its status, who decided and their note stay.
        """
        import sqlalchemy as sa

        from fim_one.db import create_session
        from fim_one.db.models.channel import ConfirmationRequest

        cleared = 0
        while True:
            async with create_session() as db:
                ids = (
                    (
                        await db.execute(
                            sa.select(ConfirmationRequest.id)
                            .where(
                                ConfirmationRequest.created_at < cutoff,
                                ConfirmationRequest.status != "pending",
                                ConfirmationRequest.payload.isnot(None),
                            )
                            .limit(self.batch_size)
                        )
                    )
                    .scalars()
                    .all()
                )
                if not ids:
                    return cleared
                await db.execute(
                    sa.update(ConfirmationRequest)
                    .where(ConfirmationRequest.id.in_(ids))
                    # SQL NULL, not the JSON value ``null``.
                    .values(payload=sa.null())
                )
                await db.commit()
                cleared += len(ids)
                if tally is not None:
                    tally["confirmation_payloads"] = cleared
            if len(ids) < self.batch_size:
                return cleared
            await asyncio.sleep(0)

    async def _leave_a_record(self, counts: dict[str, int], failed: list[str]) -> None:
        kept = (
            f"audit {self.retention.audit_days or 'unlimited'} days, "
            f"calls {self.retention.call_log_days or 'unlimited'} days"
        )
        removed = ", ".join(f"{table}={count}" for table, count in counts.items())
        await audit.record(
            actions.AUDIT_RETENTION_SWEEP,
            actor=audit.system_actor("audit-retention"),
            outcome="failed" if failed else "success",
            detail=f"removed: {removed}; kept: {kept}"
            + (f"; failed: {', '.join(failed)}" if failed else ""),
        )
        if failed:
            logger.warning("Audit retention sweep failed for: %s", ", ".join(failed))
        if any(counts.values()):
            logger.info("Audit retention sweep removed: %s (kept: %s)", removed, kept)
        else:
            logger.debug("Audit retention sweep: nothing to remove")

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    async def run_loop(self) -> None:
        """Sweep when due until the task is cancelled.

        Launched via ``asyncio.create_task`` in the FastAPI lifespan.
        """
        if not self.retention.sweeps:
            logger.info("Audit retention is off: records are kept for ever")
            return
        logger.info(
            "Audit retention sweeper started (audit=%s days, calls=%s days)",
            self.retention.audit_days or "unlimited",
            self.retention.call_log_days or "unlimited",
        )
        try:
            # Not at once: let the process start, and do not have every
            # replica of a deployment ask at the same moment.
            await asyncio.sleep(random.uniform(60, 600))
            while True:
                try:
                    await self.sweep()
                except Exception:
                    logger.exception("Audit retention sweep failed")
                await asyncio.sleep(self.check_interval_seconds)
        except asyncio.CancelledError:
            logger.info("Audit retention sweeper stopped")

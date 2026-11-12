"""Attempt-limited checking of short verification codes.

A six-digit code is only as strong as the number of guesses it allows.  The
limit therefore has to hold when guesses arrive at the same time: every
check **spends an attempt first, in one atomic statement, and compares
afterwards**.  A read-then-write (``if attempts < max: attempts += 1``) lets
every request that read the old value through.
"""

from __future__ import annotations

import hmac
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db.models.email_verification import EmailVerification
from fim_one.web.exceptions import AppError

VERIFICATION_MAX_ATTEMPTS = 5


def codes_match(expected: str | None, submitted: str | None) -> bool:
    """Constant-time comparison of two codes."""
    if not isinstance(expected, str) or not isinstance(submitted, str):
        return False
    return hmac.compare_digest(expected.encode("utf-8"), submitted.encode("utf-8"))


async def spend_code_attempt(db: AsyncSession, verification_id: str) -> bool:
    """Atomically spend one of a code's attempts.

    One ``UPDATE … SET attempts = attempts + 1 WHERE attempts < max`` decides
    it: the database serialises writers of the row (a row lock on
    PostgreSQL, the write lock on SQLite) and each one re-evaluates the
    condition against the committed count, so no more than
    :data:`VERIFICATION_MAX_ATTEMPTS` callers ever get ``True`` for one
    code.  The spend is committed before returning, so it stands whatever
    the caller does next and the lock is not held across the comparison.
    """
    result = await db.execute(
        update(EmailVerification)
        .where(
            EmailVerification.id == verification_id,
            EmailVerification.attempts < VERIFICATION_MAX_ATTEMPTS,
            EmailVerification.verified_at == None,  # noqa: E711
        )
        .values(attempts=EmailVerification.attempts + 1)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return bool(getattr(result, "rowcount", 0) == 1)


async def spend_client_try(
    db: AsyncSession, verification: EmailVerification, client: str, limit: int
) -> bool:
    """Atomically spend one of *client*'s tries of one code.

    A code allows :data:`VERIFICATION_MAX_ATTEMPTS` tries in all; this
    keeps one client from spending all of them, so the holder of the
    address still has tries left for the code that was sent to them.
    ``limit <= 0`` switches the rule off. The counter row lives as long as
    the code. Committed before returning.
    """
    if limit <= 0:
        return True
    row_id = str(uuid.uuid5(_RATE_NAMESPACE, f"code-client|{verification.id}|{client}"))
    dialect = db.get_bind().dialect.name
    insert_stmt: Any = (pg_insert if dialect == "postgresql" else sqlite_insert)(EmailVerification)
    await db.execute(
        insert_stmt.values(
            id=row_id,
            email=f"code-client:{client}"[:255],
            code="-",
            purpose=RATE_PURPOSE,
            attempts=0,
            expires_at=verification.expires_at,
        ).on_conflict_do_nothing(index_elements=["id"])
    )
    taken = await db.execute(
        update(EmailVerification)
        .where(EmailVerification.id == row_id, EmailVerification.attempts < limit)
        .values(attempts=EmailVerification.attempts + 1)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return bool(getattr(taken, "rowcount", 0) == 1)


async def check_emailed_code(
    db: AsyncSession,
    verification: EmailVerification,
    submitted: str | None,
    *,
    reset_token: str | None = None,
    client: str | None = None,
    tries_per_client: int = 0,
) -> None:
    """Check *submitted* against an emailed code, within the attempt limits.

    The code allows :data:`VERIFICATION_MAX_ATTEMPTS` tries; with *client*,
    that client may make at most *tries_per_client* of them.

    On success the code is marked used (single use: of two simultaneous
    correct submissions one wins) in the caller's transaction, not yet
    committed, so a later failure in the same request leaves the code
    usable again; the spent attempt stays spent.

    Raises:
        AppError: ``verification_code_too_many_attempts`` when no attempt is
            left (for the code, or for this client), ``verification_code_invalid``
            on a wrong code, ``verification_code_expired`` when the code was
            used meanwhile.
    """
    verification_id = verification.id
    expected = verification.code

    if client is not None and not await spend_client_try(
        db, verification, client, tries_per_client
    ):
        raise AppError("verification_code_too_many_attempts")
    if not await spend_code_attempt(db, verification_id):
        raise AppError("verification_code_too_many_attempts")
    if not codes_match(expected, submitted):
        raise AppError("verification_code_invalid")

    values: dict[str, object] = {"verified_at": datetime.now(UTC)}
    if reset_token is not None:
        values["reset_token"] = reset_token
    result = await db.execute(
        update(EmailVerification)
        .where(
            EmailVerification.id == verification_id,
            EmailVerification.verified_at == None,  # noqa: E711
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if getattr(result, "rowcount", 0) != 1:
        # Used by a simultaneous request.  Nothing was changed here, and the
        # caller's loaded objects stay usable (a rollback would expire them).
        raise AppError("verification_code_expired")


# ---------------------------------------------------------------------------
# Second-factor failures
# ---------------------------------------------------------------------------
# The limit on wrong second-factor codes is "N failures in the last W
# minutes", read from the sign-in history.  Reading that count and then
# writing the failure is the same read-then-write as above, and the history
# is append-only, so it cannot hold a provisional entry either.  Each account
# therefore has one counter row (in the verification table, under a purpose
# of its own) that mirrors the count for the current stretch of time and is
# only ever moved by conditional single-statement updates.

SECOND_FACTOR_PURPOSE = "2fa_failures"
_COUNTER_NAMESPACE = uuid.UUID("6f1d2a0e-7c55-4f0b-9a55-2fa0c0de0001")


def _counter_id(user_id: str) -> str:
    """The primary key of *user_id*'s counter row: one row per account."""
    return str(uuid.uuid5(_COUNTER_NAMESPACE, f"2fa-failures:{user_id}"))


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


async def _claim(db: AsyncSession, counter_id: str, limit: int) -> bool:
    result = await db.execute(
        update(EmailVerification)
        .where(
            EmailVerification.id == counter_id,
            EmailVerification.expires_at > datetime.now(UTC),
            EmailVerification.attempts < limit,
        )
        .values(attempts=EmailVerification.attempts + 1)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return bool(getattr(result, "rowcount", 0) == 1)


async def claim_second_factor_attempt(
    db: AsyncSession,
    *,
    user_id: str,
    email: str,
    limit: int,
    window: timedelta,
    recent_failures: Sequence[datetime],
) -> bool:
    """Atomically take one of the account's second-factor attempts.

    *recent_failures* are the times of the failures already recorded in the
    last *window* (the caller has refused the request if there are *limit*
    of them).  Returns ``False`` when simultaneous requests have taken the
    rest: of any number of requests in flight together, no more than
    ``limit - len(recent_failures)`` get ``True``.

    The counter runs from the moment it is (re)started until the oldest
    recorded failure leaves the window, which is when the recorded count can
    next drop; it is then restarted from the recorded count.  A correct code
    hands its attempt back with :func:`release_second_factor_attempt`.
    """
    counter_id = _counter_id(user_id)

    # 1. The usual case: a running counter with room left.
    if await _claim(db, counter_id, limit):
        return True

    now = datetime.now(UTC)
    starting = len(recent_failures) + 1
    if starting > limit:
        return False
    ends = min((_aware(t) for t in recent_failures), default=now) + window
    if ends <= now:
        ends = now + window

    # 2. A counter whose stretch is over: restart it.  One request wins.
    restarted = await db.execute(
        update(EmailVerification)
        .where(
            EmailVerification.id == counter_id,
            EmailVerification.expires_at <= now,
        )
        .values(attempts=starting, expires_at=ends)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    if getattr(restarted, "rowcount", 0) == 1:
        return True

    # 3. No counter yet: create it.  The fixed primary key lets one request win.
    dialect = db.get_bind().dialect.name
    insert_stmt: Any = (pg_insert if dialect == "postgresql" else sqlite_insert)(EmailVerification)
    created = await db.execute(
        insert_stmt.values(
            id=counter_id,
            email=email,
            code="-",
            purpose=SECOND_FACTOR_PURPOSE,
            attempts=starting,
            expires_at=ends,
        ).on_conflict_do_nothing(index_elements=["id"])
    )
    await db.commit()
    if getattr(created, "rowcount", 0) == 1:
        return True

    # 4. Someone else restarted or created it meanwhile: take what is left.
    return await _claim(db, counter_id, limit)


async def release_second_factor_attempt(db: AsyncSession, *, user_id: str) -> None:
    """Hand back the attempt of a request whose code was right (not committed)."""
    await db.execute(
        update(EmailVerification)
        .where(
            EmailVerification.id == _counter_id(user_id),
            EmailVerification.attempts > 0,
        )
        .values(attempts=EmailVerification.attempts - 1)
        .execution_options(synchronize_session=False)
    )


# ---------------------------------------------------------------------------
# How often codes are issued and checked
# ---------------------------------------------------------------------------
# A code allows a fixed number of tries, so the number of codes issued for an
# address bounds the number of guesses against it (and the mail sent to it).
# Issuing and checking are therefore counted, per address and per client
# address, in fixed windows.  A window is one row with a primary key derived
# from (scope, key, window start); taking from it is a conditional update.

RATE_PURPOSE = "rate_window"
_RATE_NAMESPACE = uuid.UUID("6f1d2a0e-7c55-4f0b-9a55-2fa0c0de0002")


def window_seconds_left(window: timedelta) -> timedelta:
    """Time until the current clock-aligned *window* (as in :func:`take_from_window`) ends."""
    seconds = max(1, int(window.total_seconds()))
    now = datetime.now(UTC).timestamp()
    return timedelta(seconds=seconds - now % seconds)


async def take_from_window(
    db: AsyncSession, *, scope: str, key: str, limit: int, window: timedelta
) -> bool:
    """Atomically take one unit from the current *window* of ``(scope, key)``.

    Returns ``False`` when *limit* units were already taken in this window.
    ``limit <= 0`` switches the rule off.  Windows are aligned to the clock,
    so two adjacent windows can together admit up to twice *limit* around
    their boundary.  Committed before returning.
    """
    if limit <= 0:
        return True
    seconds = max(1, int(window.total_seconds()))
    now = datetime.now(UTC)
    start = int(now.timestamp()) // seconds * seconds
    row_id = str(uuid.uuid5(_RATE_NAMESPACE, f"{scope}|{key}|{seconds}|{start}"))
    ends = datetime.fromtimestamp(start + seconds, UTC)

    dialect = db.get_bind().dialect.name
    insert_stmt: Any = (pg_insert if dialect == "postgresql" else sqlite_insert)(EmailVerification)
    created = await db.execute(
        insert_stmt.values(
            id=row_id,
            # The row is found by its key; this column only labels it.
            email=f"{scope}:{key}"[:255],
            code="-",
            purpose=RATE_PURPOSE,
            attempts=0,
            expires_at=ends,
        ).on_conflict_do_nothing(index_elements=["id"])
    )
    if getattr(created, "rowcount", 0) == 1:
        # A new window for this key: a good moment to drop windows long over.
        await db.execute(
            delete(EmailVerification).where(
                EmailVerification.purpose == RATE_PURPOSE,
                EmailVerification.expires_at < now - timedelta(days=2),
            )
        )
    taken = await db.execute(
        update(EmailVerification)
        .where(EmailVerification.id == row_id, EmailVerification.attempts < limit)
        .values(attempts=EmailVerification.attempts + 1)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return bool(getattr(taken, "rowcount", 0) == 1)


# ---------------------------------------------------------------------------
# Waiting between codes for one address
# ---------------------------------------------------------------------------
# A hard cap per address would let anyone who knows the address use it up and
# leave the owner without a code until the window ends.  Instead the first few
# codes of a day are issued at once and each later one waits twice as long as
# the one before, up to a ceiling, which bounds the mail sent per day.  The
# wait is shared by everyone asking for the address: requests by others can
# take the next code before the owner does, so the owner may have to wait
# more than once.  The tries of each code are limited per client as well
# (``spend_client_try``), so one client cannot use up a code sent to the owner.

COOLDOWN_STEP = timedelta(seconds=60)
_COOLDOWN_NAMESPACE = uuid.UUID("6f1d2a0e-7c55-4f0b-9a55-2fa0c0de0003")
_DAY_SECONDS = 86_400


def send_cooldown(sent_today: int, *, free: int, longest: timedelta) -> timedelta:
    """The wait after the *sent_today*-th code of the day.

    None for the first *free* codes, then ``COOLDOWN_STEP`` doubling with
    each code, never more than *longest*.
    """
    if sent_today < max(free, 0) or longest <= timedelta(0):
        return timedelta(0)
    return min(COOLDOWN_STEP * 2 ** min(sent_today - max(free, 0), 30), longest)


async def take_after_cooldown(
    db: AsyncSession, *, scope: str, key: str, free: int, longest: timedelta
) -> timedelta | None:
    """Admit one code for ``(scope, key)`` unless its wait is still running.

    Returns ``None`` when admitted, else the time left to wait.  The count
    starts again each day (UTC).  ``longest <= 0`` switches the rule off.
    Committed before returning.
    """
    if longest <= timedelta(0):
        return None
    now = datetime.now(UTC)
    start = int(now.timestamp()) // _DAY_SECONDS * _DAY_SECONDS
    row_id = str(uuid.uuid5(_COOLDOWN_NAMESPACE, f"{scope}|{key}|{start}"))
    dialect = db.get_bind().dialect.name
    insert_stmt: Any = (pg_insert if dialect == "postgresql" else sqlite_insert)(EmailVerification)
    await db.execute(
        insert_stmt.values(
            id=row_id,
            email=f"cooldown:{scope}:{key}"[:255],
            code="-",
            purpose=RATE_PURPOSE,
            attempts=0,
            # Here ``expires_at`` is the earliest time the next code may go.
            expires_at=datetime.fromtimestamp(start, UTC),
        ).on_conflict_do_nothing(index_elements=["id"])
    )
    await db.commit()
    for _ in range(3):
        sent, not_before = (
            await db.execute(
                select(EmailVerification.attempts, EmailVerification.expires_at).where(
                    EmailVerification.id == row_id
                )
            )
        ).one()
        not_before = _aware(not_before)
        if not_before > now:
            await db.commit()
            return not_before - now
        taken = await db.execute(
            update(EmailVerification)
            .where(EmailVerification.id == row_id, EmailVerification.attempts == sent)
            .values(
                attempts=sent + 1,
                expires_at=now + send_cooldown(sent + 1, free=free, longest=longest),
            )
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        if getattr(taken, "rowcount", 0) == 1:
            return None
    # Others are taking codes for this key at this very moment.
    return COOLDOWN_STEP

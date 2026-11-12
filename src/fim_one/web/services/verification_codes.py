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

from sqlalchemy import delete, update
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


async def check_emailed_code(
    db: AsyncSession,
    verification: EmailVerification,
    submitted: str | None,
    *,
    reset_token: str | None = None,
) -> None:
    """Check *submitted* against an emailed code, within the attempt limit.

    On success the code is marked used (single use: of two simultaneous
    correct submissions one wins) in the caller's transaction, not yet
    committed, so a later failure in the same request leaves the code
    usable again; the spent attempt stays spent.

    Raises:
        AppError: ``verification_code_too_many_attempts`` when no attempt is
            left, ``verification_code_invalid`` on a wrong code,
            ``verification_code_expired`` when the code was used meanwhile.
    """
    verification_id = verification.id
    expected = verification.code

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
    return await claim_from_window(db, scope=scope, key=key, limit=limit, window=window) is not None


def _window_row(scope: str, key: str, window: timedelta) -> tuple[str, datetime, datetime]:
    seconds = max(1, int(window.total_seconds()))
    now = datetime.now(UTC)
    start = int(now.timestamp()) // seconds * seconds
    row_id = str(uuid.uuid5(_RATE_NAMESPACE, f"{scope}|{key}|{seconds}|{start}"))
    return row_id, datetime.fromtimestamp(start + seconds, UTC), now


async def claim_from_window(
    db: AsyncSession, *, scope: str, key: str, limit: int, window: timedelta
) -> str | None:
    """Like :func:`take_from_window`; returns the window's row id, or ``None``.

    The id lets the caller hand the unit back with :func:`release_to_window`
    (a request that turned out not to count).  ``limit`` must be positive.
    """
    row_id, ends, now = _window_row(scope, key, window)

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
    return row_id if getattr(taken, "rowcount", 0) == 1 else None


async def release_to_window(db: AsyncSession, row_id: str) -> None:
    """Hand back a unit taken with :func:`claim_from_window` (not committed)."""
    await db.execute(
        update(EmailVerification)
        .where(EmailVerification.id == row_id, EmailVerification.attempts > 0)
        .values(attempts=EmailVerification.attempts - 1)
        .execution_options(synchronize_session=False)
    )


# ---------------------------------------------------------------------------
# Password sign-in failures per account
# ---------------------------------------------------------------------------
# One row per account (in the verification table, under a purpose of its
# own).  ``attempts`` counts the password attempts taken in the current
# stretch, ``expires_at`` ends that stretch, ``verified_at`` holds the end of
# a lock, and ``code`` the number of locks in a row (each lock is twice as
# long as the one before).  Attempts are taken before the password is
# compared, with conditional single-statement updates, so requests arriving
# together cannot share one count.

LOGIN_LOCK_PURPOSE = "login_lock"
_LOGIN_NAMESPACE = uuid.UUID("6f1d2a0e-7c55-4f0b-9a55-2fa0c0de0003")
MAX_LOGIN_LOCK = timedelta(hours=24)


def _login_row_id(user_id: str) -> str:
    return str(uuid.uuid5(_LOGIN_NAMESPACE, f"login-lock:{user_id}"))


def _not_locked(now: datetime) -> Any:
    return (EmailVerification.verified_at == None) | (  # noqa: E711
        EmailVerification.verified_at <= now
    )


async def claim_password_attempt(
    db: AsyncSession, *, user_id: str, email: str, limit: int, window: timedelta
) -> bool:
    """Atomically take one of the account's password attempts.

    Returns ``False`` while the account is locked, and when simultaneous
    requests have taken the attempts left in this stretch.  Committed.
    """
    row_id = _login_row_id(user_id)
    now = datetime.now(UTC)

    async def _take() -> bool:
        result = await db.execute(
            update(EmailVerification)
            .where(
                EmailVerification.id == row_id,
                EmailVerification.expires_at > now,
                EmailVerification.attempts < limit,
                _not_locked(now),
            )
            .values(attempts=EmailVerification.attempts + 1)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        return bool(getattr(result, "rowcount", 0) == 1)

    # 1. A running stretch with attempts left.
    if await _take():
        return True
    # 2. A stretch that is over (and no lock running): start a new one.
    restarted = await db.execute(
        update(EmailVerification)
        .where(
            EmailVerification.id == row_id,
            EmailVerification.expires_at <= now,
            _not_locked(now),
        )
        .values(attempts=1, expires_at=now + window)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    if getattr(restarted, "rowcount", 0) == 1:
        return True
    # 3. No row yet.
    dialect = db.get_bind().dialect.name
    insert_stmt: Any = (pg_insert if dialect == "postgresql" else sqlite_insert)(EmailVerification)
    created = await db.execute(
        insert_stmt.values(
            id=row_id,
            email=email[:255],
            code="0",
            purpose=LOGIN_LOCK_PURPOSE,
            attempts=1,
            expires_at=now + window,
        ).on_conflict_do_nothing(index_elements=["id"])
    )
    await db.commit()
    if getattr(created, "rowcount", 0) == 1:
        return True
    # 4. Someone else started it meanwhile: take what is left.
    return await _take()


async def record_password_failure(
    db: AsyncSession, *, user_id: str, limit: int, lock: timedelta
) -> datetime | None:
    """After a wrong password: lock the account once *limit* attempts failed.

    Returns the end of the lock when this failure started one.  The lock
    doubles with each lock in a row (``lock``, ``2 * lock`` ...) up to
    :data:`MAX_LOGIN_LOCK`; the series starts again once a day has passed
    since the last lock ended.  Committed.
    """
    from sqlalchemy import select

    row_id = _login_row_id(user_id)
    now = datetime.now(UTC)
    row = (
        await db.execute(
            select(EmailVerification.code, EmailVerification.verified_at).where(
                EmailVerification.id == row_id
            )
        )
    ).one_or_none()
    if row is None:
        return None
    previous_locks = int(row.code) if (row.code or "").isdigit() else 0
    last_end = row.verified_at
    if last_end is not None and _aware(last_end) < now - timedelta(days=1):
        previous_locks = 0
    duration = min(lock * (2 ** min(previous_locks, 16)), MAX_LOGIN_LOCK)
    until = now + duration
    locked = await db.execute(
        update(EmailVerification)
        .where(EmailVerification.id == row_id, EmailVerification.attempts >= limit)
        .values(
            attempts=0,
            verified_at=until,
            expires_at=until,
            code=str(min(previous_locks + 1, 99)),
        )
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return until if getattr(locked, "rowcount", 0) == 1 else None


async def clear_password_failures(db: AsyncSession, *, user_id: str) -> None:
    """A successful sign-in: forget the failures and the lock series (not committed)."""
    await db.execute(
        delete(EmailVerification).where(EmailVerification.id == _login_row_id(user_id))
    )

"""Regression tests for force-logout (``tokens_invalidated_at``) comparison.

When an admin force-logs-out a user, every access token issued *before* the
invalidation instant must be rejected. The comparison stores ``iat`` (always
tz-aware UTC) against ``tokens_invalidated_at``, whose tz-awareness depends on
the backend: SQLite returns naive datetimes (read back as naive UTC) while
PostgreSQL ``TIMESTAMP WITH TIME ZONE`` returns tz-aware UTC. ``_as_utc`` must
normalise both to UTC without corrupting a non-UTC offset.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401 — register all models with metadata
from fim_one.db.base import Base
from fim_one.db.models import User
from fim_one.web.auth import _as_utc, create_access_token, get_current_user


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


class TestAsUtc:
    def test_naive_is_stamped_utc(self) -> None:
        naive = datetime(2026, 6, 13, 12, 0, 0)
        result = _as_utc(naive)
        assert result.tzinfo is UTC
        assert result == datetime(2026, 6, 13, 12, 0, 0, tzinfo=UTC)

    def test_aware_utc_unchanged(self) -> None:
        aware = datetime(2026, 6, 13, 12, 0, 0, tzinfo=UTC)
        assert _as_utc(aware) == aware

    def test_aware_non_utc_is_converted_not_overridden(self) -> None:
        # 12:00 in UTC+8 is 04:00 UTC. A naive .replace(tzinfo=UTC) would have
        # wrongly produced 12:00 UTC; astimezone converts correctly.
        plus8 = timezone(timedelta(hours=8))
        aware = datetime(2026, 6, 13, 12, 0, 0, tzinfo=plus8)
        result = _as_utc(aware)
        assert result == datetime(2026, 6, 13, 4, 0, 0, tzinfo=UTC)


@pytest_asyncio.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _make_user(session: AsyncSession, invalidated_at: datetime | None) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:6]}@example.com",
        tokens_invalidated_at=invalidated_at,
    )
    session.add(user)
    await session.commit()
    return user


class TestForceLogoutComparison:
    async def test_token_issued_before_invalidation_rejected(
        self, session: AsyncSession
    ) -> None:
        # Invalidation is in the future relative to the token's iat.
        future = datetime.now(UTC) + timedelta(hours=1)
        user = await _make_user(session, future)
        token = create_access_token(user.id, user.email)
        with pytest.raises(HTTPException) as exc:
            await get_current_user(credentials=_creds(token), db=session)
        assert exc.value.status_code == 401
        assert "invalidated" in str(exc.value.detail).lower()

    async def test_token_issued_after_invalidation_accepted(
        self, session: AsyncSession
    ) -> None:
        past = datetime.now(UTC) - timedelta(hours=1)
        user = await _make_user(session, past)
        token = create_access_token(user.id, user.email)
        result = await get_current_user(credentials=_creds(token), db=session)
        assert result.id == user.id

    async def test_no_invalidation_accepts(self, session: AsyncSession) -> None:
        user = await _make_user(session, None)
        token = create_access_token(user.id, user.email)
        result = await get_current_user(credentials=_creds(token), db=session)
        assert result.id == user.id

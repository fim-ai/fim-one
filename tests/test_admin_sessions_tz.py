"""``GET /api/admin/sessions`` compares a timezone-aware column with an aware "now".

``users.refresh_token_expires_at`` is ``DateTime(timezone=True)``. The
endpoint compared it with ``datetime.utcnow()``, which is naive. SQLite
compares the two as UTC. On PostgreSQL the driver reads a naive value as
the API server's local time, so the cut-off was off by the server's UTC
offset: ahead of UTC, sessions that expired within the last few hours were
still listed; behind UTC, sessions about to expire were left out.

The PostgreSQL case runs when ``FIM_TEST_PG_URL`` points at an empty
throwaway database (and shows the difference only where the test machine's
zone is not UTC); the bound-parameter check fails on the old code anywhere.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import fim_one.db.models  # noqa: F401
from fim_one.db.base import Base
from fim_one.db.models.user import User
from fim_one.web.api.admin_security import list_active_sessions


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


@pytest.fixture(params=_backends())
async def engine(request: pytest.FixtureRequest) -> AsyncIterator[AsyncEngine]:
    """An empty database with the application's tables, per test."""
    if request.param == "sqlite":
        sqlite = create_async_engine(
            "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        async with sqlite.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield sqlite
        await sqlite.dispose()
        return

    url = make_url(os.environ["FIM_TEST_PG_URL"]).set(drivername="postgresql+asyncpg")
    schema = f"admin_sessions_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    pg = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with pg.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield pg
    finally:
        await pg.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


def _user(name: str, *, token: str | None, expires_in: timedelta | None) -> User:
    return User(
        id=str(uuid.uuid4()),
        username=name,
        email=f"{name}@x.io",
        refresh_token=token,
        refresh_token_expires_at=(datetime.now(UTC) + expires_in) if expires_in else None,
    )


async def test_lists_only_sessions_that_have_not_expired(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    admin = _user("admin", token="t-admin", expires_in=timedelta(days=7))
    async with factory() as s:
        s.add_all(
            [
                admin,
                _user("later", token="t-later", expires_in=timedelta(days=30)),
                _user("expired", token="t-old", expires_in=timedelta(minutes=-1)),
                _user("signed-out", token=None, expires_in=None),
            ]
        )
        await s.commit()

    async with factory() as s:
        sessions = await list_active_sessions(current_user=admin, db=s)

    # Longest-lived first; expired and signed-out accounts are not sessions.
    assert [item.username for item in sessions] == ["later", "admin"]


async def test_now_is_bound_as_an_aware_datetime() -> None:
    """Fails on any backend when the comparison value is naive."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock(return_value=result)

    await list_active_sessions(current_user=MagicMock(), db=db)

    statement = db.execute.await_args.args[0]
    bound = [v for v in statement.compile().params.values() if isinstance(v, datetime)]
    assert len(bound) == 1
    assert bound[0].tzinfo is not None
    assert bound[0].utcoffset() == timedelta(0)

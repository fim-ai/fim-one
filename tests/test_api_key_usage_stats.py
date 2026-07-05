"""Regression tests for API-key usage-stat durability.

``_authenticate_api_key`` increments ``total_requests`` / ``last_used_at`` on
every authenticated request. The request session yielded by ``get_session`` is
opened under an ``async with`` that only *closes* (rolls back uncommitted work)
on exit — there is no request-end auto-commit. Every read-only API-key endpoint
(dashboard, files, models, exports, …) never commits, so when the increment was
issued on the *request* session it was silently rolled back on those calls.

The fix records usage stats on an independent, self-committing session. These
tests pin that behaviour: the counter must persist even when the request
session is closed without committing (the read-only-endpoint case).
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select

import fim_one.db.models  # noqa: F401 — register all models with metadata
from fim_one.db import create_session, get_session, init_db, shutdown_db
from fim_one.db.base import Base
from fim_one.db.engine import _engine  # noqa: F401 — presence check only
from fim_one.db.models import ApiKey, User
from fim_one.web.auth import _authenticate_api_key


@pytest_asyncio.fixture()
async def db_env() -> AsyncIterator[None]:
    """Initialise the global engine/session factory against a temp SQLite file.

    Both ``get_session`` (request session) and ``create_session`` (the stats
    session used by the fix) resolve through the same global factory, so they
    must share one on-disk database for the cross-session read-back to be
    meaningful — a temp file, not ``:memory:``.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    prev_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{path}"
    await init_db()

    # Import the engine lazily so create_all runs against the just-built engine.
    from fim_one.db import engine as engine_mod

    assert engine_mod._engine is not None
    async with engine_mod._engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield
    finally:
        await shutdown_db()
        if prev_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev_url
        os.unlink(path)


async def _seed_key(raw_key: str) -> str:
    """Create an active user + API key with ``total_requests=0``; return user id."""
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    uid = str(uuid.uuid4())
    session = create_session()
    try:
        session.add(
            User(id=uid, email="u@example.com", username="u", password_hash="x", is_active=True)
        )
        session.add(
            ApiKey(
                id=str(uuid.uuid4()),
                key_hash=key_hash,
                user_id=uid,
                name="t",
                key_prefix="fim_test",
                is_active=True,
                total_requests=0,
                scopes=None,
            )
        )
        await session.commit()
    finally:
        await session.close()
    return uid


async def _read_key(raw_key: str) -> ApiKey:
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    session = create_session()
    try:
        return (
            await session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        ).scalar_one()
    finally:
        await session.close()


@pytest.mark.asyncio()
async def test_read_only_endpoint_persists_usage_stats(db_env: None) -> None:
    """A read-only endpoint (request session closed without commit) must still
    persist the usage-stat increment."""
    raw_key = "fim_readonly_key"
    uid = await _seed_key(raw_key)

    # Drive get_session like FastAPI's Depends would, then close WITHOUT
    # committing — exactly what a read-only endpoint leaves behind.
    agen = get_session()
    db = await agen.__anext__()
    user, _scopes = await _authenticate_api_key(raw_key, db)
    assert user.id == uid
    await agen.aclose()  # request session closed → uncommitted work rolled back

    api_key = await _read_key(raw_key)
    assert api_key.total_requests == 1
    assert api_key.last_used_at is not None


@pytest.mark.asyncio()
async def test_usage_stats_increment_is_monotonic(db_env: None) -> None:
    """Repeated authentications accumulate the counter across closed sessions."""
    raw_key = "fim_repeat_key"
    await _seed_key(raw_key)

    for _ in range(3):
        agen = get_session()
        db = await agen.__anext__()
        await _authenticate_api_key(raw_key, db)
        await agen.aclose()

    api_key = await _read_key(raw_key)
    assert api_key.total_requests == 3

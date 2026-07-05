"""Regression tests for API-key scope isolation (GitHub issue #22).

Scopes used to be attached to the ORM ``User`` as a transient
``_api_key_scopes`` attribute. Because SQLAlchemy's identity map (and any
future user cache) can hand the same ``User`` instance to a different auth
context, a stale attribute could silently widen access. The fix returns scopes
separately from ``_authenticate_api_key`` and carries them on
``request.state`` — request-scoped by construction.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

import fim_one.db.models  # noqa: F401 — register all models with metadata
from fim_one.db import create_session, get_session, init_db, shutdown_db
from fim_one.db.base import Base
from fim_one.db.models import ApiKey, User
from fim_one.web.auth import (
    _authenticate_api_key,
    create_access_token,
    get_current_user,
    require_scope,
)


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _request() -> Request:
    """Bare HTTP request with a working ``.state`` for direct dependency calls."""
    return Request(scope={"type": "http"})


@pytest_asyncio.fixture()
async def db_env() -> AsyncIterator[None]:
    """Initialise the global engine/session factory against a temp SQLite file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    prev_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{path}"
    await init_db()

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


async def _seed_key(raw_key: str, scopes: str | None) -> str:
    """Create an active user + API key with the given scopes; return user id."""
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    uid = str(uuid.uuid4())
    session = create_session()
    try:
        session.add(
            User(
                id=uid,
                email=f"{uuid.uuid4().hex[:8]}@example.com",
                username=f"u_{uuid.uuid4().hex[:8]}",
                password_hash="x",
                is_active=True,
            )
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
                scopes=scopes,
            )
        )
        await session.commit()
    finally:
        await session.close()
    return uid


@pytest.mark.asyncio()
async def test_authenticate_returns_scopes_without_orm_attribute(db_env: None) -> None:
    """Scopes come back as a separate value; nothing is hung on the User."""
    raw_key = "fim_scoped_key"
    await _seed_key(raw_key, scopes="chat:read,chat:write")

    agen = get_session()
    db = await agen.__anext__()
    try:
        user, scopes = await _authenticate_api_key(raw_key, db)
        assert scopes == {"chat:read", "chat:write"}
        assert not hasattr(user, "_api_key_scopes")
    finally:
        await agen.aclose()


@pytest.mark.asyncio()
async def test_unrestricted_key_returns_none_scopes(db_env: None) -> None:
    raw_key = "fim_unrestricted_key"
    await _seed_key(raw_key, scopes=None)

    agen = get_session()
    db = await agen.__anext__()
    try:
        _user, scopes = await _authenticate_api_key(raw_key, db)
        assert scopes is None
    finally:
        await agen.aclose()


@pytest.mark.asyncio()
async def test_scopes_live_on_request_state_per_request(db_env: None) -> None:
    """Two requests reusing the same User must not share scope state (#22)."""
    raw_key = "fim_state_key"
    uid = await _seed_key(raw_key, scopes="chat:read")

    agen = get_session()
    db = await agen.__anext__()
    try:
        # Request 1: API key auth → restricted scopes on ITS request only.
        req_api = _request()
        user = await get_current_user(req_api, credentials=_creds(raw_key), db=db)
        assert user.id == uid
        assert req_api.state.api_key_scopes == {"chat:read"}

        # Request 2: JWT auth for the same user → unrestricted, and request 1's
        # scope set must not bleed over in either direction.
        req_jwt = _request()
        token = create_access_token(uid, "u@example.com")
        user2 = await get_current_user(req_jwt, credentials=_creds(token), db=db)
        assert user2.id == uid
        assert req_jwt.state.api_key_scopes is None
        assert req_api.state.api_key_scopes == {"chat:read"}
    finally:
        await agen.aclose()


@pytest.mark.asyncio()
async def test_require_scope_enforces_from_request_state(db_env: None) -> None:
    raw_key = "fim_enforce_key"
    uid = await _seed_key(raw_key, scopes="chat:read")

    check = require_scope("admin:write")
    allow = require_scope("chat:read")

    agen = get_session()
    db = await agen.__anext__()
    try:
        req = _request()
        user = await get_current_user(req, credentials=_creds(raw_key), db=db)

        with pytest.raises(HTTPException) as exc:
            await check(req, user=user)
        assert exc.value.status_code == 403

        allowed = await allow(req, user=user)
        assert allowed.id == uid

        # JWT (unrestricted) request passes any scope check.
        req_jwt = _request()
        token = create_access_token(uid, "u@example.com")
        jwt_user = await get_current_user(req_jwt, credentials=_creds(token), db=db)
        assert (await check(req_jwt, user=jwt_user)).id == uid
    finally:
        await agen.aclose()

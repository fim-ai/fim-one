"""Failed password sign-ins are limited per account and per client address.

* An account refuses password sign-in for a while after
  ``AUTH_LOGIN_FAILURES_PER_ACCOUNT`` failures in ``AUTH_LOGIN_LOCK_MINUTES``;
  each lock in a row lasts twice as long, up to a day.  The count holds
  when attempts arrive at the same time.
* A client address is answered 429 after ``AUTH_LOGIN_FAILURES_PER_IP_HOUR``
  failures in the hour.
* From outside, a locked account answers exactly like a wrong password and
  like an address without an account.
* Signing in with an emailed code is not blocked by a password lock.
* Sending a code, and checking one, answer the same whether or not the
  address has an account.
* Malformed input is refused without a server error.

Runs on SQLite, and on PostgreSQL when ``FIM_TEST_PG_URL`` is set.
"""

from __future__ import annotations

import asyncio
import collections
import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import bcrypt
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.db import get_session
from fim_one.db.base import Base
from fim_one.db.models import LoginHistory, User
from fim_one.db.models.email_verification import EmailVerification
from fim_one.web import auth as auth_core
from fim_one.web.api import auth as auth_api
from fim_one.web.app import validation_error_handler
from fim_one.web.auth import get_current_user
from fim_one.web.exceptions import AppError
from fim_one.web.services import verification_codes

_PG = os.environ.get("FIM_TEST_PG_URL")
_BACKENDS = ["sqlite", pytest.param("postgresql", marks=pytest.mark.skipif(not _PG, reason="FIM_TEST_PG_URL not set"))]
_EMAIL = "owner@example.com"
_PASSWORD = "right-password-1"
_CODE = "424242"


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(4)).decode()


class _Env:
    def __init__(self, client: AsyncClient, sessions: async_sessionmaker[AsyncSession], user_id: str) -> None:
        self.client = client
        self.sessions = sessions
        self.user_id = user_id
        self.sent: list[tuple[str, str]] = []

    async def login(self, password: str, email: str = _EMAIL) -> tuple[int, Any]:
        r = await self.client.post("/api/auth/login", json={"email": email, "password": password})
        return r.status_code, r.json()

    async def reasons(self) -> collections.Counter[str]:
        async with self.sessions() as s:
            rows = (await s.execute(select(LoginHistory.failure_reason))).scalars().all()
        return collections.Counter(r or "ok" for r in rows)

    async def lock_row(self) -> Any:
        async with self.sessions() as s:
            return (
                await s.execute(
                    select(EmailVerification).where(
                        EmailVerification.purpose == verification_codes.LOGIN_LOCK_PURPOSE
                    )
                )
            ).scalar_one_or_none()

    async def end_lock(self) -> None:
        """Move the running lock into the past, as time would."""
        past = datetime.now(UTC) - timedelta(seconds=1)
        async with self.sessions() as s:
            await s.execute(
                update(EmailVerification)
                .where(EmailVerification.purpose == verification_codes.LOGIN_LOCK_PURPOSE)
                .values(verified_at=past, expires_at=past)
            )
            await s.commit()

    async def add_code(self, purpose: str, email: str = _EMAIL) -> None:
        async with self.sessions() as s:
            s.add(EmailVerification(email=email, code=_CODE, purpose=purpose,
                                    expires_at=datetime.now(UTC) + timedelta(minutes=5)))
            await s.commit()


@pytest_asyncio.fixture(params=_BACKENDS)
async def env(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Env]:
    engine: AsyncEngine
    if request.param == "sqlite":
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}", pool_size=20, max_overflow=40)

        @event.listens_for(engine.sync_engine, "connect")
        def _pragmas(dbapi_conn: Any, _record: Any) -> None:  # as fim_one.db.engine does
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()
    else:
        url = make_url(_PG or "").set(drivername="postgresql+asyncpg")
        engine = create_async_engine(url, pool_size=20, max_overflow=40)

    async with engine.begin() as conn:
        if request.param == "postgresql":
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    user_id = str(uuid.uuid4())
    async with sessions() as s:
        s.add_all([
            User(id=user_id, email=_EMAIL, username="owner", password_hash=_hash(_PASSWORD), email_verified=True),
            User(id=str(uuid.uuid4()), email="provider-only@example.com", username="prov", password_hash=None),
            User(id=str(uuid.uuid4()), email="odd-hash@example.com", username="odd", password_hash="x"),
        ])
        await s.commit()

    app = FastAPI()
    app.include_router(auth_api.router)
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]

    @app.exception_handler(AppError)
    async def _app_error(_request: Any, exc: AppError) -> JSONResponse:
        return JSONResponse(
            {"detail": exc.detail, "error_code": exc.error_code, "error_args": exc.detail_args},
            status_code=exc.status_code,
        )

    async def _session() -> AsyncIterator[AsyncSession]:
        async with sessions() as s:
            yield s

    async def _me() -> User:
        async with sessions() as s:
            user = await s.get(User, user_id)
            assert user is not None
            return user

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = _me
    monkeypatch.setattr(auth_core, "_DUMMY_HASH", _hash("not-a-password"))
    monkeypatch.setattr(auth_api, "_smtp_configured", lambda: True)
    monkeypatch.setattr(auth_api, "spawn_background", lambda coro: coro.close())
    for name in ("AUTH_LOGIN_FAILURES_PER_ACCOUNT", "AUTH_LOGIN_LOCK_MINUTES", "AUTH_LOGIN_FAILURES_PER_IP_HOUR",
                 "AUTH_CODE_SENDS_PER_ADDRESS_HOUR", "AUTH_CODE_SENDS_PER_ADDRESS_DAY"):
        monkeypatch.delenv(name, raising=False)
    for name in ("AUTH_CODE_CHECKS_PER_IP_HOUR", "AUTH_CODE_SENDS_PER_IP_HOUR"):
        monkeypatch.setenv(name, "0")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t", timeout=120) as client:
        e = _Env(client, sessions, user_id)

        async def _send(email: str, code: str, **_: Any) -> None:
            e.sent.append((email, code))

        monkeypatch.setattr(auth_api, "send_verification_email", _send)
        yield e
    if request.param == "postgresql":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


class TestAccountLimit:
    async def test_five_failures_lock_password_sign_in(self, env: _Env) -> None:
        wrong = [await env.login(f"wrong-{i}") for i in range(5)]
        assert {status for status, _ in wrong} == {401}
        # Locked: even the right password is refused, with the same answer.
        status, body = await env.login(_PASSWORD)
        assert (status, body) == wrong[0]
        reasons = await env.reasons()
        assert reasons["wrong_password"] == 5
        assert reasons["account_locked_now"] == 1
        assert reasons["account_locked"] == 1

        await env.end_lock()
        assert (await env.login(_PASSWORD))[0] == 200

    async def test_a_locked_account_answers_like_a_wrong_password_and_no_account(self, env: _Env) -> None:
        for i in range(5):
            await env.login(f"wrong-{i}")
        locked = await env.login("wrong-again")
        missing = await env.login("whatever", email="nobody@example.com")
        await env.end_lock()
        wrong = await env.login("wrong-later")
        assert locked == missing == wrong
        assert locked[0] == 401 and locked[1]["error_code"] == "invalid_credentials"

    async def test_a_successful_sign_in_resets_the_count(self, env: _Env) -> None:
        for i in range(4):
            await env.login(f"wrong-{i}")
        assert (await env.login(_PASSWORD))[0] == 200
        for i in range(4):
            await env.login(f"wrong-b{i}")
        assert (await env.login(_PASSWORD))[0] == 200
        assert (await env.reasons())["account_locked"] == 0

    async def test_locks_in_a_row_get_longer_up_to_a_day(self, env: _Env) -> None:
        durations = []
        for _round in range(3):
            for i in range(5):
                await env.login(f"wrong-{i}")
            row = await env.lock_row()
            until = row.verified_at if row.verified_at.tzinfo else row.verified_at.replace(tzinfo=UTC)
            durations.append(round((until - datetime.now(UTC)).total_seconds() / 60))
            await env.end_lock()
        assert durations == [15, 30, 60]

        async with env.sessions() as s:
            await s.execute(update(EmailVerification).values(code="40"))
            await s.commit()
        for i in range(5):
            await env.login(f"wrong-{i}")
        row = await env.lock_row()
        until = row.verified_at if row.verified_at.tzinfo else row.verified_at.replace(tzinfo=UTC)
        assert round((until - datetime.now(UTC)).total_seconds() / 3600) == 24

    async def test_simultaneous_attempts_cannot_exceed_the_limit(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTH_LOGIN_FAILURES_PER_IP_HOUR", "0")  # tested below
        results = await asyncio.gather(*[env.login(f"wrong-{i}") for i in range(30)])
        assert {status for status, _ in results} == {401}
        reasons = await env.reasons()
        assert reasons["wrong_password"] <= 5, reasons
        assert reasons["wrong_password"] + reasons["account_locked"] == 30
        assert (await env.login(_PASSWORD))[0] == 401

    async def test_settings(self, env: _Env, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_LOGIN_FAILURES_PER_ACCOUNT", "2")
        monkeypatch.setenv("AUTH_LOGIN_LOCK_MINUTES", "1")
        await env.login("wrong-1")
        await env.login("wrong-2")
        assert (await env.login(_PASSWORD))[0] == 401
        row = await env.lock_row()
        until = row.verified_at if row.verified_at.tzinfo else row.verified_at.replace(tzinfo=UTC)
        assert (until - datetime.now(UTC)) <= timedelta(minutes=1)

        await env.end_lock()
        monkeypatch.setenv("AUTH_LOGIN_FAILURES_PER_ACCOUNT", "0")
        for i in range(10):
            await env.login(f"wrong-{i}")
        assert (await env.login(_PASSWORD))[0] == 200


class TestClientLimit:
    async def test_failures_per_client_address(self, env: _Env, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_LOGIN_FAILURES_PER_IP_HOUR", "3")
        assert (await env.login(_PASSWORD))[0] == 200  # a success is not counted
        statuses = [(await env.login("x", email=f"user{i}@example.com"))[0] for i in range(5)]
        assert statuses == [401, 401, 401, 429, 429]
        status, body = await env.login(_PASSWORD)
        assert status == 429 and body["error_code"] == "too_many_login_attempts"

        # Another client address has its own count.
        monkeypatch.setattr(auth_api, "_client_key", lambda request: "198.51.100.7")
        assert (await env.login(_PASSWORD))[0] == 200


class TestOtherSignInPaths:
    async def test_code_sign_in_is_not_blocked_and_clears_the_lock(self, env: _Env) -> None:
        for i in range(5):
            await env.login(f"wrong-{i}")
        assert (await env.login(_PASSWORD))[0] == 401
        await env.add_code("login")
        r = await env.client.post("/api/auth/login-with-code", json={"email": _EMAIL, "code": _CODE})
        assert r.status_code == 200, r.text
        assert (await env.login(_PASSWORD))[0] == 200

    async def test_a_password_reset_clears_the_lock(self, env: _Env) -> None:
        for i in range(5):
            await env.login(f"wrong-{i}")
        assert (await env.login(_PASSWORD))[0] == 401
        await env.add_code("reset_password")
        r = await env.client.post("/api/auth/verify-forgot-code", json={"email": _EMAIL, "code": _CODE})
        assert r.status_code == 200, r.text
        token = r.json()["data"]["reset_token"]
        r = await env.client.post(
            "/api/auth/forgot-password",
            json={"email": _EMAIL, "reset_token": token, "new_password": "new-password-1"},
        )
        assert r.status_code == 200, r.text
        assert (await env.login("new-password-1"))[0] == 200


class TestNoAccountIsDisclosed:
    @pytest.mark.parametrize("path", ["/api/auth/send-login-code", "/api/auth/send-forgot-code"])
    async def test_sending_a_code(self, env: _Env, path: str) -> None:
        known = await env.client.post(path, json={"email": _EMAIL})
        unknown = await env.client.post(path, json={"email": "nobody@example.com"})
        assert known.status_code == unknown.status_code == 200
        assert known.json() == unknown.json()
        assert [email for email, _ in env.sent] == [_EMAIL]
        # The resend interval applies to both alike.
        again = [
            (await env.client.post(path, json={"email": e})).status_code
            for e in (_EMAIL, "nobody@example.com")
        ]
        assert again == [429, 429]

    @pytest.mark.parametrize(
        ("send", "check"),
        [("/api/auth/send-login-code", "/api/auth/login-with-code"),
         ("/api/auth/send-forgot-code", "/api/auth/verify-forgot-code")],
    )
    async def test_checking_a_code(self, env: _Env, send: str, check: str) -> None:
        answers = []
        for email in (_EMAIL, "nobody@example.com"):
            await env.client.post(send, json={"email": email})
            r = await env.client.post(check, json={"email": email, "code": "000000"})
            answers.append((r.status_code, r.json()))
        assert answers[0] == answers[1]
        assert answers[0][1]["error_code"] == "verification_code_invalid"

    async def test_a_reset_for_an_address_without_an_account(self, env: _Env) -> None:
        r = await env.client.post(
            "/api/auth/forgot-password",
            json={"email": "nobody@example.com", "reset_token": str(uuid.uuid4()), "new_password": "new-password-1"},
        )
        assert r.status_code == 400 and r.json()["error_code"] == "verification_code_expired"


async def _post_raw(env: _Env, body: Any) -> Any:
    """Post *body* as JSON text, escapes included (``\\ud800`` survives)."""
    return await env.client.post(
        "/api/auth/login", content=json.dumps(body), headers={"content-type": "application/json"}
    )


class TestMalformedInput:
    @pytest.mark.parametrize(
        "body",
        [
            {"email": _EMAIL, "password": "密码" * 20},       # longer than 72 bytes
            {"email": "provider-only@example.com", "password": "anything-1"},
            {"email": "odd-hash@example.com", "password": "anything-1"},
            {"email": _EMAIL, "password": ""},
        ],
    )
    async def test_refused_as_wrong_credentials(self, env: _Env, body: dict[str, Any]) -> None:
        r = await _post_raw(env, body)
        assert r.status_code == 401 and r.json()["error_code"] == "invalid_credentials"

    @pytest.mark.parametrize(
        "body",
        [
            {"email": _EMAIL},
            {"email": _EMAIL, "password": None},
            {"email": None, "password": "x"},
            {"email": "a\ud800@example.com", "password": "x"},
            {"email": _EMAIL, "password": "\ud800abc"},
            {"email": "a\x00@example.com", "password": "x"},
            {"email": "a" * 300 + "@example.com", "password": "x"},
            {"email": _EMAIL, "password": "x" * 2000},
            [],
        ],
    )
    async def test_refused_as_invalid_input(self, env: _Env, body: Any) -> None:
        r = await _post_raw(env, body)
        assert r.status_code == 422
        # The submitted values are not echoed back.
        assert "input" not in r.text

    async def test_long_passwords_hash_and_verify(self) -> None:
        long_password = "密码" * 20
        hashed = auth_core.hash_password(long_password)
        assert auth_core.verify_password(long_password, hashed)
        assert not auth_core.verify_password("密码" * 5, hashed)
        assert not auth_core.verify_password(long_password, "")
        assert not auth_core.verify_password(long_password, None)


async def test_lock_rows_are_ordinary_verification_rows(env: _Env) -> None:
    for i in range(5):
        await env.login(f"wrong-{i}")
    async with env.sessions() as s:
        count = await s.scalar(
            select(func.count()).select_from(EmailVerification).where(
                EmailVerification.purpose == verification_codes.LOGIN_LOCK_PURPOSE
            )
        )
    assert count == 1

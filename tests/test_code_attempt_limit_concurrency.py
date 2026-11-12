"""Code attempt limits hold when guesses arrive at the same time.

For every flow that accepts a short code (emailed login code, registration
code, password reset signed in and signed out, the 2FA step with TOTP and
backup codes) the property is the same: however many requests are in flight
together, no more than the limit are ever compared against the secret, and
a code is accepted once.

Each test fires its requests concurrently through the real routes against a
real database: a SQLite file opened the way the application opens it, and
PostgreSQL when ``FIM_TEST_PG_URL`` points at a throwaway server.
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

import pyotp
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.db import get_session
from fim_one.db.base import Base
from fim_one.db.models import LoginHistory, User
from fim_one.db.models.email_verification import EmailVerification
from fim_one.web.api import auth as auth_api
from fim_one.web.auth import get_current_user
from fim_one.web.exceptions import AppError
from fim_one.web.services.verification_codes import VERIFICATION_MAX_ATTEMPTS

_PG = os.environ.get("FIM_TEST_PG_URL")
_BACKENDS = ["sqlite", pytest.param("postgresql", marks=pytest.mark.skipif(not _PG, reason="FIM_TEST_PG_URL not set"))]
_BURST = 60
_EMAIL = "owner@example.com"
_CODE = "424242"


class _Env:
    def __init__(self, client: AsyncClient, sessions: async_sessionmaker[AsyncSession], user_id: str) -> None:
        self.client = client
        self.sessions = sessions
        self.user_id = user_id

    async def add_code(self, purpose: str, email: str = _EMAIL) -> str:
        async with self.sessions() as s:
            row = EmailVerification(
                email=email, code=_CODE, purpose=purpose,
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
            s.add(row)
            await s.commit()
            return row.id

    async def attempts(self, verification_id: str) -> int:
        async with self.sessions() as s:
            return int((await s.execute(
                select(EmailVerification.attempts).where(EmailVerification.id == verification_id)
            )).scalar_one())

    async def burst(self, path: str, bodies: list[dict[str, Any]]) -> collections.Counter[str]:
        """Send all *bodies* at once; tally the outcomes."""
        start = asyncio.Event()

        async def _one(body: dict[str, Any]) -> str:
            await start.wait()
            r = await self.client.post(path, json=body)
            if r.status_code == 200:
                return "accepted"
            return str(r.json().get("error_code") or r.status_code)

        tasks = [asyncio.create_task(_one(b)) for b in bodies]
        await asyncio.sleep(0)
        start.set()
        return collections.Counter(await asyncio.gather(*tasks))


def _wrong_codes(n: int = _BURST) -> list[str]:
    return [f"{i:06d}" for i in range(100000, 100000 + n)]


@pytest_asyncio.fixture(params=_BACKENDS)
async def env(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Env]:
    engine: AsyncEngine
    if request.param == "sqlite":
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}", pool_size=20, max_overflow=40)

        @event.listens_for(engine.sync_engine, "connect")
        def _pragmas(dbapi_conn: Any, _record: Any) -> None:  # as fim_one.db.engine does
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    else:
        url = make_url(_PG or "").set(drivername="postgresql+asyncpg")
        engine = create_async_engine(url, pool_size=20, max_overflow=40)

    async with engine.begin() as conn:
        if request.param == "postgresql":
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    secret = pyotp.random_base32()
    user_id = str(uuid.uuid4())
    async with sessions() as s:
        s.add(User(
            id=user_id, email=_EMAIL, username="owner", password_hash="x", email_verified=True,
            totp_enabled=True, totp_secret=secret,
            totp_backup_codes=json.dumps([auth_api._hash_backup_code("BACKUP-01"), auth_api._hash_backup_code("BACKUP-02")]),
        ))
        await s.commit()

    app = FastAPI()
    app.include_router(auth_api.router)

    @app.exception_handler(AppError)
    async def _app_error(_request: Any, exc: AppError) -> JSONResponse:
        return JSONResponse(
            {"error_code": exc.error_code, "error_args": exc.detail_args},
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

    async def _setting(db: Any, key: str, default: str = "") -> str:
        return {"email_verification_enabled": "true", "registration_mode": "open"}.get(key, default)

    monkeypatch.setattr(auth_api, "get_setting", _setting)
    monkeypatch.setattr(auth_api, "_smtp_configured", lambda: True)
    monkeypatch.setattr(auth_api, "spawn_background", lambda coro: coro.close())
    sent: list[tuple[str, str]] = []

    async def _send(email: str, code: str, **_: Any) -> None:
        sent.append((email, code))

    monkeypatch.setattr(auth_api, "send_verification_email", _send)
    # The attempt tests send many requests from one client; the per-client
    # windows have tests of their own below.
    for name in ("AUTH_CODE_CHECKS_PER_IP_HOUR", "AUTH_CODE_SENDS_PER_IP_HOUR", "AUTH_CODE_TRIES_PER_CLIENT"):
        monkeypatch.setenv(name, "0")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t", timeout=120) as client:
        e = _Env(client, sessions, user_id)
        e.totp_secret = secret  # type: ignore[attr-defined]
        e.sent = sent  # type: ignore[attr-defined]
        e.app = app  # type: ignore[attr-defined]
        yield e
    if request.param == "postgresql":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


_EMAILED_FLOWS: dict[str, tuple[str, str, Any]] = {
    # name: (purpose, path, body builder)
    "login": ("login", "/api/auth/login-with-code", lambda code: {"email": _EMAIL, "code": code}),
    "forgot": ("reset_password", "/api/auth/verify-forgot-code", lambda code: {"email": _EMAIL, "code": code}),
    "reset": ("reset_password", "/api/auth/reset-password", lambda code: {"code": code, "new_password": "new-password-1"}),
}


class TestEmailedCodes:
    @pytest.mark.parametrize("flow", sorted(_EMAILED_FLOWS))
    async def test_no_more_than_the_limit_are_compared(self, env: _Env, flow: str) -> None:
        purpose, path, body = _EMAILED_FLOWS[flow]
        vid = await env.add_code(purpose)

        outcome = await env.burst(path, [body(c) for c in _wrong_codes()])

        assert outcome["accepted"] == 0
        assert outcome["verification_code_invalid"] <= VERIFICATION_MAX_ATTEMPTS, outcome
        assert outcome["verification_code_invalid"] + outcome["verification_code_too_many_attempts"] == _BURST, outcome
        assert await env.attempts(vid) == VERIFICATION_MAX_ATTEMPTS

        # The code is spent: even the right one is refused now.
        r = await env.client.post(path, json=body(_CODE))
        assert r.status_code != 200
        assert r.json()["error_code"] == "verification_code_too_many_attempts"

    @pytest.mark.parametrize("flow", sorted(_EMAILED_FLOWS))
    async def test_right_code_hidden_among_wrong_ones_gets_no_extra_tries(self, env: _Env, flow: str) -> None:
        """The comparison happens only for requests that were granted an attempt."""
        purpose, path, body = _EMAILED_FLOWS[flow]
        vid = await env.add_code(purpose)

        codes = _wrong_codes(_BURST - 1) + [_CODE]
        outcome = await env.burst(path, [body(c) for c in codes])

        compared = outcome["accepted"] + outcome["verification_code_invalid"]
        assert compared <= VERIFICATION_MAX_ATTEMPTS, outcome
        assert outcome["accepted"] <= 1
        assert await env.attempts(vid) <= VERIFICATION_MAX_ATTEMPTS

    @pytest.mark.parametrize("flow", ["login", "forgot"])
    async def test_a_code_is_accepted_once(self, env: _Env, flow: str) -> None:
        purpose, path, body = _EMAILED_FLOWS[flow]
        # The 2FA step would otherwise answer 200 with a temp token for login.
        async with env.sessions() as s:
            user = await s.get(User, env.user_id)
            assert user is not None
            user.totp_enabled = False
            await s.commit()
        await env.add_code(purpose)

        outcome = await env.burst(path, [body(_CODE)] * 20)

        assert outcome["accepted"] == 1, outcome

    async def test_registration_code(self, env: _Env) -> None:
        email = "new@example.com"
        vid = await env.add_code("register", email)

        def body(code: str) -> dict[str, Any]:
            return {"email": email, "password": "a-password-1", "verification_code": code}

        outcome = await env.burst("/api/auth/register", [body(c) for c in _wrong_codes()])
        assert outcome["accepted"] == 0
        assert outcome["verification_code_invalid"] <= VERIFICATION_MAX_ATTEMPTS, outcome
        assert await env.attempts(vid) == VERIFICATION_MAX_ATTEMPTS

        r = await env.client.post("/api/auth/register", json=body(_CODE))
        assert r.status_code != 200
        async with env.sessions() as s:
            assert (await s.execute(select(func.count()).select_from(User).where(User.email == email))).scalar_one() == 0

    async def test_sequential_behaviour_is_unchanged(self, env: _Env) -> None:
        _, path, body = _EMAILED_FLOWS["forgot"]
        vid = await env.add_code("reset_password")
        for i in range(VERIFICATION_MAX_ATTEMPTS - 1):
            r = await env.client.post(path, json=body(f"00000{i}"))
            assert r.json()["error_code"] == "verification_code_invalid"
        r = await env.client.post(path, json=body(_CODE))
        assert r.status_code == 200 and r.json()["data"]["reset_token"]
        assert await env.attempts(vid) == VERIFICATION_MAX_ATTEMPTS


class TestSecondFactor:
    _PATH = "/api/auth/login/verify-2fa"

    def _body(self, env: _Env, code: str) -> dict[str, Any]:
        return {"temp_token": auth_api._create_2fa_temp_token(env.user_id, method="password"), "code": code}

    async def _failure_rows(self, env: _Env) -> int:
        async with env.sessions() as s:
            return int((await s.execute(
                select(func.count()).select_from(LoginHistory).where(
                    LoginHistory.user_id == env.user_id, LoginHistory.failure_reason == "2fa_code_invalid"
                )
            )).scalar_one())

    async def test_no_more_than_the_limit_are_compared(self, env: _Env) -> None:
        outcome = await env.burst(self._PATH, [self._body(env, c) for c in _wrong_codes()])

        limit = auth_api._2FA_MAX_FAILURES
        assert outcome["accepted"] == 0
        assert outcome["2fa_code_invalid"] <= limit, outcome
        assert outcome["2fa_code_invalid"] + outcome["2fa_too_many_attempts"] == _BURST, outcome
        # Refusals leave no failure record, so they do not extend the lock-out.
        assert await self._failure_rows(env) == outcome["2fa_code_invalid"]

        # Requests that all arrive together may all be refused (each sees the
        # others' provisional records).  What must hold is the total: keep
        # guessing one at a time and the window still allows `limit` in all.
        compared = outcome["2fa_code_invalid"]
        for code in _wrong_codes(limit + 3):
            r = await env.client.post(self._PATH, json=self._body(env, "9" + code[1:]))
            if r.status_code == 429:
                break
            assert r.json()["error_code"] == "2fa_code_invalid"
            compared += 1
        assert compared == limit

        # Locked: neither the right TOTP code nor a backup code gets through.
        totp = pyotp.TOTP(env.totp_secret).now()  # type: ignore[attr-defined]
        for code in (totp, "BACKUP-01"):
            r = await env.client.post(self._PATH, json=self._body(env, code))
            assert r.status_code == 429
        assert await self._failure_rows(env) == limit

    async def test_backup_code_guesses_share_the_limit(self, env: _Env) -> None:
        guesses = [f"BACKUP-{i:02d}" for i in range(10, 10 + _BURST)]
        outcome = await env.burst(self._PATH, [self._body(env, c) for c in guesses])
        assert outcome["accepted"] == 0
        assert outcome["2fa_code_invalid"] <= auth_api._2FA_MAX_FAILURES, outcome
        assert outcome["2fa_code_invalid"] + outcome["2fa_too_many_attempts"] == _BURST, outcome

    async def test_a_backup_code_is_accepted_once(self, env: _Env) -> None:
        outcome = await env.burst(self._PATH, [self._body(env, "BACKUP-01")] * 4)
        assert outcome["accepted"] == 1, outcome
        async with env.sessions() as s:
            user = await s.get(User, env.user_id)
            assert user is not None
            assert json.loads(user.totp_backup_codes or "[]") == [auth_api._hash_backup_code("BACKUP-02")]

    async def test_a_right_code_leaves_no_failure_record(self, env: _Env) -> None:
        totp = pyotp.TOTP(env.totp_secret).now()  # type: ignore[attr-defined]
        r = await env.client.post(self._PATH, json=self._body(env, totp))
        assert r.status_code == 200
        assert await self._failure_rows(env) == 0

    async def test_right_codes_do_not_use_up_the_limit(self, env: _Env) -> None:
        limit = auth_api._2FA_MAX_FAILURES
        for _ in range(limit + 3):
            totp = pyotp.TOTP(env.totp_secret).now()  # type: ignore[attr-defined]
            r = await env.client.post(self._PATH, json=self._body(env, totp))
            assert r.status_code == 200
        # ... and simultaneous right codes are all let in.
        totp = pyotp.TOTP(env.totp_secret).now()  # type: ignore[attr-defined]
        outcome = await env.burst(self._PATH, [self._body(env, totp)] * 3)
        assert outcome["accepted"] >= 1 and outcome["2fa_code_invalid"] == 0

    async def test_failures_recorded_before_the_counter_existed_count(self, env: _Env) -> None:
        """An account with failures on record (and no counter yet) gets only the rest."""
        limit = auth_api._2FA_MAX_FAILURES
        async with env.sessions() as s:
            for _ in range(limit - 2):
                s.add(LoginHistory(user_id=env.user_id, email=_EMAIL, success=False,
                                   failure_reason="2fa_code_invalid", method="password"))
            await s.commit()
        outcome = await env.burst(self._PATH, [self._body(env, c) for c in _wrong_codes()])
        assert outcome["2fa_code_invalid"] <= 2, outcome
        assert outcome["accepted"] == 0


class TestIssuanceAndCheckWindows:
    """How often codes are issued for an address, and how many one client may
    request or try in an hour."""

    _SEND = {
        "login": "/api/auth/send-login-code",
        "forgot": "/api/auth/send-forgot-code",
        "register": "/api/auth/send-verification-code",
    }

    async def _age_codes(self, env: _Env) -> None:
        """Move issued codes past the one-per-minute rule."""
        from sqlalchemy import update

        async with env.sessions() as s:
            await s.execute(
                update(EmailVerification)
                .where(EmailVerification.purpose.in_(["login", "reset_password", "register"]))
                .values(created_at=datetime.now(UTC) - timedelta(minutes=5))
            )
            # The per-address count keeps when its last code went.
            await s.execute(
                update(EmailVerification)
                .where(
                    EmailVerification.email.like("cooldown:%"),
                    EmailVerification.verified_at != None,  # noqa: E711
                )
                .values(verified_at=datetime.now(UTC) - timedelta(minutes=5))
            )
            await s.commit()

    async def _send(self, env: _Env, path: str, email: str = _EMAIL) -> int:
        r = await env.client.post(path, json={"email": email})
        await self._age_codes(env)
        return r.status_code

    async def _end_cooldowns(self, env: _Env) -> None:
        """Let the running per-address waits end now."""
        from sqlalchemy import update

        async with env.sessions() as s:
            await s.execute(
                update(EmailVerification)
                .where(EmailVerification.email.like("cooldown:%"))
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await s.commit()

    async def _wait_left(self, env: _Env) -> timedelta:
        from sqlalchemy import select

        async with env.sessions() as s:
            until = (
                await s.execute(
                    select(EmailVerification.expires_at).where(
                        EmailVerification.email.like("cooldown:%login%")
                    )
                )
            ).scalar_one()
        until = until if until.tzinfo else until.replace(tzinfo=UTC)
        return until - datetime.now(UTC)

    @pytest.mark.parametrize("flow", ["login", "forgot"])
    async def test_codes_per_address_wait_after_the_first_few(self, env: _Env, flow: str) -> None:
        statuses = [await self._send(env, self._SEND[flow]) for _ in range(6)]
        assert statuses == [200] * 3 + [429] * 3
        assert len(env.sent) == 3  # type: ignore[attr-defined]
        r = await env.client.post(self._SEND[flow], json={"email": _EMAIL})
        assert r.json()["error_code"] == "verification_rate_limited"
        assert 0 < r.json()["error_args"]["seconds"] <= 60

    async def test_each_wait_doubles_up_to_the_ceiling(self, env: _Env) -> None:
        waits = []
        for _ in range(8):
            await self._end_cooldowns(env)
            assert await self._send(env, self._SEND["login"]) == 200
            waits.append(await self._wait_left(env))
        minutes = [round(w.total_seconds() / 60) for w in waits]
        assert minutes == [0, 0, 1, 2, 4, 8, 16, 32]
        await self._end_cooldowns(env)
        await self._send(env, self._SEND["login"])
        assert round((await self._wait_left(env)).total_seconds() / 60) == 60

    async def test_the_wait_never_exceeds_the_ceiling_whatever_others_ask(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTH_CODE_SEND_COOLDOWN_MAX_MINUTES", "10")
        assert [await self._send(env, self._SEND["forgot"]) for _ in range(3)] == [200] * 3
        for _ in range(30):
            await self._end_cooldowns(env)
            await self._send(env, self._SEND["forgot"])
            for _ in range(5):
                assert await self._send(env, self._SEND["forgot"]) == 429
        # Even after a day's worth of requests the wait never exceeds the ceiling.
        assert (await self._wait_left_any(env)) <= timedelta(minutes=10)
        await self._end_cooldowns(env)
        assert await self._send(env, self._SEND["forgot"]) == 200

    async def _wait_left_any(self, env: _Env) -> timedelta:
        from sqlalchemy import select

        async with env.sessions() as s:
            until = (
                await s.execute(
                    select(EmailVerification.expires_at).where(
                        EmailVerification.email.like("cooldown:%")
                    )
                )
            ).scalar_one()
        until = until if until.tzinfo else until.replace(tzinfo=UTC)
        return until - datetime.now(UTC)

    def test_the_ceiling_bounds_the_codes_issued_per_day(self) -> None:
        from fim_one.web.services.verification_codes import send_cooldown

        spent, sent = timedelta(0), 0
        while spent < timedelta(days=1):
            sent += 1
            spent += send_cooldown(sent, free=3, longest=timedelta(minutes=60))
        assert sent <= 32

    async def test_simultaneous_requests_cannot_exceed_the_cap(self, env: _Env) -> None:
        outcome = await env.burst(self._SEND["login"], [{"email": _EMAIL}] * 40)
        assert outcome["accepted"] <= 3, outcome
        assert len(env.sent) <= 3  # type: ignore[attr-defined]

    @pytest.mark.parametrize("flow", ["login", "forgot", "register"])
    async def test_asking_again_within_the_minute_does_not_use_up_the_free_codes(
        self, env: _Env, flow: str
    ) -> None:
        """Four requests in a minute issue one code and count one."""
        email = "someone@example.com"
        within_a_minute = [
            (await env.client.post(self._SEND[flow], json={"email": email})).status_code
            for _ in range(4)
        ]
        assert within_a_minute == [200, 429, 429, 429]
        # The two other free codes of the day are still there, a minute apart.
        await self._age_codes(env)
        later = [await self._send(env, self._SEND[flow], email) for _ in range(3)]
        assert later == [200, 200, 429]

    @pytest.mark.parametrize("flow", ["login", "forgot"])
    async def test_refusals_within_the_minute_answer_alike_for_any_address(
        self, env: _Env, flow: str
    ) -> None:
        async def statuses(email: str) -> list[int]:
            first = [
                (await env.client.post(self._SEND[flow], json={"email": email})).status_code
                for _ in range(4)
            ]
            await self._age_codes(env)
            return first + [await self._send(env, self._SEND[flow], email) for _ in range(3)]

        known, unknown = await statuses(_EMAIL), await statuses("nobody@example.com")
        assert known == unknown == [200, 429, 429, 429, 200, 200, 429]
        assert len(env.sent) == 3  # type: ignore[attr-defined]

    @pytest.mark.parametrize("flow", ["login", "forgot"])
    async def test_simultaneous_requests_issue_one_code_and_count_one(
        self, env: _Env, flow: str
    ) -> None:
        outcome = await env.burst(self._SEND[flow], [{"email": _EMAIL}] * 40)
        assert outcome["accepted"] == 1, outcome
        assert outcome["verification_rate_limited"] == 39, outcome
        assert len(env.sent) == 1  # type: ignore[attr-defined]
        await self._age_codes(env)
        assert [await self._send(env, self._SEND[flow]) for _ in range(3)] == [200, 200, 429]

    async def test_the_minute_holds_across_midnight(self, env: _Env) -> None:
        from sqlalchemy import update

        from fim_one.web.services.verification_codes import _DAY_SECONDS, _cooldown_row_id

        assert await self._send(env, self._SEND["login"]) == 200
        # Make today's row yesterday's, its code sent a few seconds ago.
        today = int(datetime.now(UTC).timestamp()) // _DAY_SECONDS * _DAY_SECONDS
        key = f"login|{_EMAIL}"
        async with env.sessions() as s:
            await s.execute(
                update(EmailVerification)
                .where(EmailVerification.id == _cooldown_row_id("send-address", key, today))
                .values(
                    id=_cooldown_row_id("send-address", key, today - _DAY_SECONDS),
                    verified_at=datetime.now(UTC) - timedelta(seconds=5),
                )
            )
            await s.commit()
        r = await env.client.post(self._SEND["login"], json={"email": _EMAIL})
        assert r.status_code == 429
        assert 50 <= r.json()["error_args"]["seconds"] <= 60

    async def test_the_window_itself_is_atomic(self, env: _Env) -> None:
        from fim_one.web.services.verification_codes import take_from_window

        async def _one() -> bool:
            async with env.sessions() as s:
                return await take_from_window(
                    s, scope="t", key="k", limit=7, window=timedelta(hours=1)
                )

        assert sum(await asyncio.gather(*[_one() for _ in range(_BURST)])) == 7

    @pytest.mark.parametrize("flow", ["login", "forgot"])
    async def test_the_answer_does_not_depend_on_the_address_existing(
        self, env: _Env, flow: str
    ) -> None:
        known = [await self._send(env, self._SEND[flow]) for _ in range(5)]
        unknown = [await self._send(env, self._SEND[flow], "nobody@example.com") for _ in range(5)]
        assert known == unknown == [200] * 3 + [429] * 2
        # Nothing is mailed to an address without an account.
        assert len(env.sent) == 3  # type: ignore[attr-defined]

    async def test_purposes_and_the_signed_in_path_are_counted_apart(self, env: _Env) -> None:
        assert [await self._send(env, self._SEND["forgot"]) for _ in range(6)][-1] == 429
        # Sign-in codes for the same address are a different count ...
        assert await self._send(env, self._SEND["login"]) == 200
        # ... and so is the owner's own, signed-in request for a reset code.
        r = await env.client.post("/api/auth/send-reset-code", json={})
        assert r.status_code == 200

    async def test_requests_per_client(self, env: _Env, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_CODE_SENDS_PER_IP_HOUR", "4")
        statuses = [
            await self._send(env, self._SEND["register"], f"new{i}@example.com") for i in range(6)
        ]
        assert statuses == [200, 200, 200, 200, 429, 429]

    async def test_every_refusal_says_how_long_to_wait(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The message the user sees names the seconds left, whichever limit was hit."""
        monkeypatch.setenv("AUTH_CODE_SENDS_PER_IP_HOUR", "1")
        monkeypatch.setenv("AUTH_CODE_CHECKS_PER_IP_HOUR", "1")
        await env.client.post(self._SEND["register"], json={"email": "a@example.com"})
        per_client_send = await env.client.post(self._SEND["register"], json={"email": "b@example.com"})
        await env.add_code("login")
        await env.client.post("/api/auth/login-with-code", json={"email": _EMAIL, "code": "000001"})
        per_client_check = await env.client.post(
            "/api/auth/login-with-code", json={"email": _EMAIL, "code": "000002"}
        )
        monkeypatch.setenv("AUTH_CODE_SENDS_PER_IP_HOUR", "0")
        await env.client.post(self._SEND["login"], json={"email": _EMAIL})
        one_a_minute = await env.client.post(self._SEND["login"], json={"email": _EMAIL})
        for r, ceiling in ((per_client_send, 3600), (per_client_check, 3600), (one_a_minute, 60)):
            assert r.status_code == 429
            assert r.json()["error_code"] == "verification_rate_limited"
            assert 0 < r.json()["error_args"]["seconds"] <= ceiling, r.json()

    async def test_checks_per_client_do_not_spend_a_codes_attempts(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTH_CODE_CHECKS_PER_IP_HOUR", "3")
        vid = await env.add_code("reset_password")
        codes = []
        for i in range(6):
            r = await env.client.post(
                "/api/auth/verify-forgot-code", json={"email": _EMAIL, "code": f"00000{i}"}
            )
            codes.append(r.json().get("error_code"))
        assert codes == ["verification_code_invalid"] * 3 + ["verification_rate_limited"] * 3
        assert await env.attempts(vid) == 3

    async def test_one_client_cannot_spend_every_try_of_a_code(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTH_CODE_TRIES_PER_CLIENT", "3")
        vid = await env.add_code("login")
        outcomes = []
        for i in range(5):
            r = await env.client.post(
                "/api/auth/login-with-code", json={"email": _EMAIL, "code": f"00000{i}"}
            )
            outcomes.append(r.json().get("error_code"))
        assert outcomes == ["verification_code_invalid"] * 3 + ["verification_code_too_many_attempts"] * 2
        # The code's own count holds what this client spent, and no more.
        assert await env.attempts(vid) == 3
        app = env.app  # type: ignore[attr-defined]
        transport = ASGITransport(app=app, client=("203.0.113.9", 4000))
        async with AsyncClient(transport=transport, base_url="http://t") as other:
            r = await other.post("/api/auth/login-with-code", json={"email": _EMAIL, "code": _CODE})
        assert r.status_code == 200, r.text

    async def test_simultaneous_tries_of_one_client_stay_within_its_share(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTH_CODE_TRIES_PER_CLIENT", "3")
        vid = await env.add_code("login")
        outcome = await env.burst(
            "/api/auth/login-with-code", [{"email": _EMAIL, "code": c} for c in _wrong_codes()]
        )
        assert outcome["verification_code_invalid"] == 3, outcome
        assert await env.attempts(vid) == 3

    async def test_an_address_with_a_trailing_dot_is_the_same_address(self, env: _Env) -> None:
        variants = ["owner@example.com.", "OWNER@Example.COM..", "owner@example.com"]
        statuses = [await self._send(env, self._SEND["login"], v) for v in variants]
        assert statuses == [200, 200, 200]
        assert {email for email, _code in env.sent} == {_EMAIL}  # type: ignore[attr-defined]
        assert await self._send(env, self._SEND["login"], "owner@example.com.") == 429
        r = await env.client.post("/api/auth/send-login-code", json={"email": "owner@com."})
        assert r.status_code == 422

    async def test_limits_can_be_switched_off(self, env: _Env, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_CODE_SEND_COOLDOWN_MAX_MINUTES", "0")
        assert [await self._send(env, self._SEND["login"]) for _ in range(8)] == [200] * 8

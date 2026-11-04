"""Every sign-in path asks for the second factor when the account has 2FA.

Before this, only password login did: the email-code login and the OAuth
login issued session tokens directly to an account with TOTP enabled.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request

import fim_one.db.models  # noqa: F401
from fim_one.db.base import Base
from fim_one.db.models.email_verification import EmailVerification
from fim_one.db.models.user import User
from fim_one.web.api.auth import _decode_2fa_temp_token, login_with_code
from fim_one.web.api.oauth import _handle_login
from fim_one.web.oauth import OAuthUserInfo
from fim_one.web.schemas.auth import LoginWithCodeRequest, TokenResponse


@pytest_asyncio.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _user(session: AsyncSession, *, totp: bool) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        email_verified=True,
        totp_enabled=totp,
        totp_secret="JBSWY3DPEHPK3PXP" if totp else None,
    )
    session.add(user)
    await session.commit()
    return user


def _request() -> Request:
    return Request({"type": "http", "headers": [], "client": ("198.51.100.7", 1234)})


async def _code_login(session: AsyncSession, user: User) -> object:
    session.add(
        EmailVerification(
            email=user.email,
            code="123456",
            purpose="login",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    await session.commit()
    return await login_with_code(
        LoginWithCodeRequest(email=user.email, code="123456"), _request(), session
    )


class TestEmailCodeLogin:
    @pytest.mark.asyncio
    async def test_totp_account_gets_challenge_only(self, session: AsyncSession) -> None:
        user = await _user(session, totp=True)
        resp = await _code_login(session, user)

        assert isinstance(resp, JSONResponse)
        body = json.loads(bytes(resp.body))
        assert body["requires_2fa"] is True
        assert "access_token" not in body
        assert _decode_2fa_temp_token(body["temp_token"]) == user.id

    @pytest.mark.asyncio
    async def test_account_without_totp_gets_tokens(self, session: AsyncSession) -> None:
        user = await _user(session, totp=False)
        resp = await _code_login(session, user)

        assert isinstance(resp, TokenResponse)
        assert resp.access_token


def _fragment(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).fragment)


class TestOAuthLogin:
    def _info(self, user: User) -> OAuthUserInfo:
        return OAuthUserInfo(
            provider="github",
            id=f"gh-{uuid.uuid4().hex[:6]}",
            username="u",
            email=user.email,
            display_name="U",
            email_verified=True,
        )

    @pytest.mark.asyncio
    async def test_totp_account_gets_challenge_only(self, session: AsyncSession) -> None:
        user = await _user(session, totp=True)
        resp = await _handle_login(session, self._info(user), "github", "http://frontend")

        fragment = _fragment(resp.headers["location"])
        assert fragment["requires_2fa"] == ["1"]
        assert "access_token" not in fragment
        assert _decode_2fa_temp_token(fragment["temp_token"][0]) == user.id
        refreshed = await session.get(User, user.id)
        assert refreshed is not None
        assert refreshed.refresh_token is None

    @pytest.mark.asyncio
    async def test_account_without_totp_gets_tokens(self, session: AsyncSession) -> None:
        user = await _user(session, totp=False)
        resp = await _handle_login(session, self._info(user), "github", "http://frontend")

        assert "access_token" in _fragment(resp.headers["location"])

"""Regression tests for JWT token-type confinement.

Every FIM JWT (access / refresh / sse_ticket / bind_ticket / 2fa temp token) is
signed with the same key and carries a ``sub`` claim. Authentication entry
points must therefore assert the token *type* — otherwise any of them could be
replayed as an access token. The most critical case is the 2FA temp token
returned by the password step: without a type check it authenticates fully,
bypassing the second factor entirely.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401 — register all models with metadata
from fim_one.db.base import Base
from fim_one.web.api.auth import _create_2fa_temp_token
from fim_one.web.api.chat import _resolve_user
from fim_one.web.auth import (
    create_access_token,
    create_bind_ticket,
    create_refresh_token,
    create_sse_ticket,
    get_current_user,
    get_current_user_optional,
)
from fim_one.web.exceptions import AppError
from fim_one.db.models import User


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _non_access_tokens(user_id: str) -> dict[str, str]:
    """Tokens that all carry a valid ``sub`` but must NOT authenticate as access."""
    return {
        "refresh": create_refresh_token(user_id, "u@example.com"),
        "sse_ticket": create_sse_ticket(user_id),
        "bind_ticket": create_bind_ticket(user_id),
        "2fa_temp": _create_2fa_temp_token(user_id),
    }


@pytest_asyncio.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture()
async def active_user(session: AsyncSession) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:6]}@example.com",
    )
    session.add(user)
    await session.commit()
    return user


class TestGetCurrentUserTokenType:
    @pytest.mark.parametrize("kind", ["refresh", "sse_ticket", "bind_ticket", "2fa_temp"])
    async def test_non_access_token_rejected_before_db(self, kind: str) -> None:
        token = _non_access_tokens("user-123")[kind]
        db = MagicMock()
        db.execute = AsyncMock()
        with pytest.raises(HTTPException) as exc:
            await get_current_user(credentials=_creds(token), db=db)
        assert exc.value.status_code == 401
        assert "type" in str(exc.value.detail).lower()
        # The type check must precede any DB lookup.
        db.execute.assert_not_called()

    async def test_access_token_accepted(
        self, session: AsyncSession, active_user: User
    ) -> None:
        token = create_access_token(active_user.id, active_user.email)
        user = await get_current_user(credentials=_creds(token), db=session)
        assert user.id == active_user.id


class TestGetCurrentUserOptionalTokenType:
    @pytest.mark.parametrize("kind", ["refresh", "2fa_temp", "bind_ticket"])
    async def test_non_access_returns_none(self, kind: str) -> None:
        token = _non_access_tokens("user-123")[kind]
        db = MagicMock()
        db.execute = AsyncMock()
        result = await get_current_user_optional(credentials=_creds(token), db=db)
        assert result is None
        db.execute.assert_not_called()

    async def test_access_token_accepted(
        self, session: AsyncSession, active_user: User
    ) -> None:
        token = create_access_token(active_user.id, active_user.email)
        user = await get_current_user_optional(credentials=_creds(token), db=session)
        assert user is not None
        assert user.id == active_user.id


class TestResolveUserTokenType:
    """``chat._resolve_user`` accepts access tokens and sse_tickets only.

    The disallowed-type check runs before any DB session is opened, so these
    cases need no database.
    """

    @pytest.mark.parametrize("kind", ["refresh", "bind_ticket", "2fa_temp"])
    async def test_disallowed_type_rejected(self, kind: str) -> None:
        token = _non_access_tokens("user-123")[kind]
        with pytest.raises(AppError) as exc:
            await _resolve_user(token)
        assert exc.value.status_code == 401

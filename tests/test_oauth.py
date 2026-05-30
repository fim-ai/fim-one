"""Regression tests for the OAuth login flow.

Covers two security/correctness fixes in ``_handle_login``:

1. Email-based auto-bind is gated on ``email_verified`` — matching an existing
   local account by an *unverified* provider email would let an attacker take
   over that account by setting their third-party email to the victim's.
2. The OAuth-issued refresh token is stored as a SHA-256 digest (matching the
   ``/refresh`` comparison), never as the raw long-lived JWT.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401 — register all models with metadata
from fim_one.db.base import Base
from fim_one.web.api.oauth import _handle_login
from fim_one.db.models import User, UserOAuthBinding
from fim_one.web.oauth import OAuthUserInfo


@pytest_asyncio.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _seed_user(session: AsyncSession, email: str) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=email,
    )
    session.add(user)
    await session.commit()
    return user


async def _binding_for(session: AsyncSession, provider: str, oauth_id: str) -> UserOAuthBinding | None:
    result = await session.execute(
        select(UserOAuthBinding).where(
            UserOAuthBinding.provider == provider,
            UserOAuthBinding.oauth_id == oauth_id,
        )
    )
    return result.scalar_one_or_none()


class TestEmailVerifiedGating:
    async def test_verified_email_auto_binds_existing_user(
        self, session: AsyncSession
    ) -> None:
        existing = await _seed_user(session, "victim@example.com")
        info = OAuthUserInfo(
            provider="github",
            id="gh-verified",
            username="attacker",
            email="victim@example.com",
            display_name="A",
            email_verified=True,
        )

        await _handle_login(session, info, "github", "http://frontend")

        binding = await _binding_for(session, "github", "gh-verified")
        assert binding is not None
        # Verified email → logs in as the existing local account.
        assert binding.user_id == existing.id

    async def test_unverified_email_does_not_hijack_existing_user(
        self, session: AsyncSession
    ) -> None:
        existing = await _seed_user(session, "victim@example.com")
        info = OAuthUserInfo(
            provider="github",
            id="gh-unverified",
            username="attacker",
            email="victim@example.com",  # same address, but unverified
            display_name="A",
            email_verified=False,
        )

        await _handle_login(session, info, "github", "http://frontend")

        binding = await _binding_for(session, "github", "gh-unverified")
        assert binding is not None
        # Unverified email must NOT match the existing account — a fresh user
        # is created instead, so the victim's account is never taken over.
        assert binding.user_id != existing.id


class TestRefreshTokenHashed:
    async def test_oauth_refresh_token_stored_as_digest(
        self, session: AsyncSession
    ) -> None:
        existing = await _seed_user(session, "user@example.com")
        info = OAuthUserInfo(
            provider="github",
            id="gh-1",
            username="user",
            email="user@example.com",
            display_name="U",
            email_verified=True,
        )

        await _handle_login(session, info, "github", "http://frontend")

        refreshed = await session.get(User, existing.id)
        assert refreshed is not None
        assert refreshed.refresh_token is not None
        # A SHA-256 hex digest: 64 hex chars, and never a raw JWT (which has dots).
        assert len(refreshed.refresh_token) == 64
        assert "." not in refreshed.refresh_token

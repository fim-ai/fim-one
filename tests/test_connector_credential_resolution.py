"""Tests for the shared connector credential resolver.

Covers the six distinct states of the lookup:

1. Per-user credential present → returns per-user credential (ignoring default).
2. Only default credential + caller is owner + ``allow_fallback=False``
   → returns default (owner exemption — the historical regression).
3. Only default credential + caller is owner + ``allow_fallback=True``
   → returns default.
4. Only default credential + caller is *not* owner + ``allow_fallback=True``
   → returns default (opt-in sharing).
5. Only default credential + caller is *not* owner + ``allow_fallback=False``
   → returns empty dict (the flag is working as intended to block sharing).
6. No credential rows at all → returns empty dict.
7. ``calling_user_id=None`` (system / anonymous) + default exists + fallback
   allowed → returns default, because the caller has no identity but the
   default row is not user-scoped.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.core.security.connector_credentials import resolve_connector_credentials
from fim_one.core.security.encryption import encrypt_credential
from fim_one.db.base import Base
from fim_one.db.models.connector_credential import ConnectorCredential


@pytest.fixture(autouse=True)
def _stable_cred_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force a deterministic Fernet key so encrypt/decrypt roundtrips."""
    import fim_one.core.security.encryption as enc

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "test-api-key-01234567890abcdef")
    enc._CREDENTIAL_KEY_RAW = "test-api-key-01234567890abcdef"
    enc._cred_fernet_instance = None


@pytest.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


class _FakeConn:
    """Minimal stand-in for the ORM Connector row.

    The resolver only reads ``id``, ``user_id``, and ``allow_fallback``; using
    a real ORM row would force us to seed the full ``connectors`` table and
    fight FK constraints for no diagnostic value.
    """

    def __init__(self, *, id: str, user_id: str, allow_fallback: bool) -> None:
        self.id = id
        self.user_id = user_id
        self.allow_fallback = allow_fallback


async def _seed_cred(
    session: AsyncSession,
    *,
    connector_id: str,
    user_id: str | None,
    payload: dict[str, Any],
) -> None:
    session.add(
        ConnectorCredential(
            connector_id=connector_id,
            user_id=user_id,
            credentials_blob=encrypt_credential(payload),
        )
    )
    await session.commit()


OWNER = "owner-user-id"
OTHER = "other-user-id"
CID = "connector-id-1"


class TestPerUserCredentialPreferred:
    @pytest.mark.asyncio
    async def test_per_user_cred_overrides_default(
        self, session: AsyncSession
    ) -> None:
        await _seed_cred(
            session, connector_id=CID, user_id=None, payload={"default_token": "DEFAULT"}
        )
        await _seed_cred(
            session, connector_id=CID, user_id=OTHER, payload={"default_token": "PERUSER"}
        )
        conn = _FakeConn(id=CID, user_id=OWNER, allow_fallback=True)

        result = await resolve_connector_credentials(conn, OTHER, session)

        assert result == {"default_token": "PERUSER"}


class TestOwnerExemption:
    """The historical regression: owner calling own connector with fallback OFF."""

    @pytest.mark.asyncio
    async def test_owner_gets_default_when_fallback_disabled(
        self, session: AsyncSession
    ) -> None:
        await _seed_cred(
            session, connector_id=CID, user_id=None, payload={"default_token": "ghp_X"}
        )
        conn = _FakeConn(id=CID, user_id=OWNER, allow_fallback=False)

        result = await resolve_connector_credentials(conn, OWNER, session)

        assert result == {"default_token": "ghp_X"}, (
            "owner MUST be able to use their own default credential regardless "
            "of allow_fallback — that flag only gates sharing with OTHER users"
        )

    @pytest.mark.asyncio
    async def test_owner_gets_default_when_fallback_enabled(
        self, session: AsyncSession
    ) -> None:
        await _seed_cred(
            session, connector_id=CID, user_id=None, payload={"default_token": "ghp_X"}
        )
        conn = _FakeConn(id=CID, user_id=OWNER, allow_fallback=True)

        result = await resolve_connector_credentials(conn, OWNER, session)

        assert result == {"default_token": "ghp_X"}


class TestFallbackGate:
    @pytest.mark.asyncio
    async def test_other_user_with_fallback_on_gets_default(
        self, session: AsyncSession
    ) -> None:
        await _seed_cred(
            session, connector_id=CID, user_id=None, payload={"default_token": "shared"}
        )
        conn = _FakeConn(id=CID, user_id=OWNER, allow_fallback=True)

        result = await resolve_connector_credentials(conn, OTHER, session)

        assert result == {"default_token": "shared"}

    @pytest.mark.asyncio
    async def test_other_user_with_fallback_off_is_blocked(
        self, session: AsyncSession
    ) -> None:
        await _seed_cred(
            session, connector_id=CID, user_id=None, payload={"default_token": "shared"}
        )
        conn = _FakeConn(id=CID, user_id=OWNER, allow_fallback=False)

        result = await resolve_connector_credentials(conn, OTHER, session)

        assert result == {}, (
            "with allow_fallback=False, non-owner users must NOT see the "
            "owner's default credential"
        )


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_no_credentials_returns_empty(self, session: AsyncSession) -> None:
        conn = _FakeConn(id=CID, user_id=OWNER, allow_fallback=True)

        result = await resolve_connector_credentials(conn, OWNER, session)

        assert result == {}

    @pytest.mark.asyncio
    async def test_anonymous_caller_can_use_default_when_fallback_on(
        self, session: AsyncSession
    ) -> None:
        # System / background executions pass calling_user_id=None.  The
        # default row is user-agnostic so this must work as long as the
        # owner has opted into sharing.
        await _seed_cred(
            session, connector_id=CID, user_id=None, payload={"default_token": "sys"}
        )
        conn = _FakeConn(id=CID, user_id=OWNER, allow_fallback=True)

        result = await resolve_connector_credentials(conn, None, session)

        assert result == {"default_token": "sys"}

    @pytest.mark.asyncio
    async def test_anonymous_caller_blocked_when_fallback_off(
        self, session: AsyncSession
    ) -> None:
        await _seed_cred(
            session, connector_id=CID, user_id=None, payload={"default_token": "sys"}
        )
        conn = _FakeConn(id=CID, user_id=OWNER, allow_fallback=False)

        result = await resolve_connector_credentials(conn, None, session)

        # Anonymous caller is not the owner, and fallback is closed.
        assert result == {}

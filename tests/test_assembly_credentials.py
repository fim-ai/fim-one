"""Which credential a call goes out with (``core/assembly/credentials.py``)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.assembly.credentials import CredentialSource, resolve_credential
from fim_one.core.assembly.errors import AssemblyRefusal
from fim_one.core.assembly.principal import provisional_principal
from fim_one.core.security.encryption import encrypt_credential, encrypt_db_config
from fim_one.db.base import Base
from fim_one.db.models.connector_credential import ConnectorCredential

OWNER = "u-owner"
OTHER = "u-other"
CID = "conn-1"


@pytest.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _connector(*, allow_fallback: bool, auth_type: str = "bearer") -> SimpleNamespace:
    return SimpleNamespace(
        id=CID,
        name="crm",
        type="api",
        user_id=OWNER,
        auth_type=auth_type,
        auth_config=None,
        allow_fallback=allow_fallback,
    )


async def _store(session: AsyncSession, user_id: str | None, payload: dict[str, Any]) -> None:
    session.add(
        ConnectorCredential(
            connector_id=CID, user_id=user_id, credentials_blob=encrypt_credential(payload)
        )
    )
    await session.commit()


async def _refusal(session: AsyncSession, user_id: str, resource: Any) -> AssemblyRefusal:
    with pytest.raises(AssemblyRefusal) as caught:
        await resolve_credential(session, provisional_principal(user_id), resource)
    return caught.value


class TestApiConnector:
    async def test_own_credential_is_used_alone(self, session: AsyncSession) -> None:
        await _store(session, None, {"default_token": "owner"})
        await _store(session, OTHER, {"token": "mine"})

        got = await resolve_credential(
            session, provisional_principal(OTHER), _connector(allow_fallback=True)
        )

        assert got.source is CredentialSource.PERSONAL
        assert got.connector == {"token": "mine"}

    @pytest.mark.parametrize("allow_fallback", [True, False])
    async def test_owner_uses_the_stored_credential_whatever_the_switch(
        self, session: AsyncSession, allow_fallback: bool
    ) -> None:
        await _store(session, None, {"default_token": "owner"})

        got = await resolve_credential(
            session, provisional_principal(OWNER), _connector(allow_fallback=allow_fallback)
        )

        assert got.source is CredentialSource.OWNER
        assert got.connector == {"default_token": "owner"}

    async def test_someone_else_borrows_when_fallback_is_on(self, session: AsyncSession) -> None:
        await _store(session, None, {"default_token": "owner"})

        got = await resolve_credential(
            session, provisional_principal(OTHER), _connector(allow_fallback=True)
        )

        assert got.source is CredentialSource.OWNER_FALLBACK
        assert got.connector == {"default_token": "owner"}

    async def test_someone_else_is_refused_when_fallback_is_off(
        self, session: AsyncSession
    ) -> None:
        await _store(session, None, {"default_token": "owner"})

        refusal = await _refusal(session, OTHER, _connector(allow_fallback=False))

        assert (refusal.error_code, refusal.status_code) == ("credential_required", 403)
        assert "requires your own credentials" in refusal.detail

    async def test_a_caller_with_no_identity_is_not_the_owner(self, session: AsyncSession) -> None:
        await _store(session, None, {"default_token": "owner"})

        refusal = await _refusal(session, "", _connector(allow_fallback=False))
        assert refusal.error_code == "credential_required"

        got = await resolve_credential(
            session, provisional_principal(""), _connector(allow_fallback=True)
        )
        assert got.source is CredentialSource.OWNER_FALLBACK

    async def test_nothing_stored_means_nothing_sent(self, session: AsyncSession) -> None:
        got = await resolve_credential(
            session, provisional_principal(OWNER), _connector(allow_fallback=False)
        )

        assert got.source is CredentialSource.NONE
        assert got.connector == {}

    async def test_an_unreadable_row_of_ones_own_does_not_borrow_the_owners(
        self, session: AsyncSession
    ) -> None:
        """A personal row that reads back empty is not replaced by the owner's."""
        await _store(session, None, {"default_token": "owner"})
        session.add(
            ConnectorCredential(connector_id=CID, user_id=OTHER, credentials_blob="not-a-token")
        )
        await session.commit()

        got = await resolve_credential(
            session, provisional_principal(OTHER), _connector(allow_fallback=True)
        )
        assert got.source is CredentialSource.NONE
        assert got.connector == {}

        refusal = await _refusal(session, OTHER, _connector(allow_fallback=False))
        assert refusal.error_code == "credential_required"

    async def test_a_failed_lookup_is_refused_not_downgraded(self) -> None:
        broken = AsyncMock()
        broken.execute = AsyncMock(side_effect=RuntimeError("db down"))

        with pytest.raises(AssemblyRefusal) as caught:
            await resolve_credential(
                broken, provisional_principal(OTHER), _connector(allow_fallback=True)
            )

        assert (caught.value.error_code, caught.value.status_code) == (
            "credential_unavailable",
            503,
        )


class TestDatabaseConnector:
    def _db(self, *, allow_fallback: bool = True) -> SimpleNamespace:
        return SimpleNamespace(
            id="db-1",
            name="warehouse",
            type="database",
            user_id=OWNER,
            allow_fallback=allow_fallback,
            db_config=encrypt_db_config({"host": "h", "username": "u", "password": "p"}),
        )

    async def test_owner_gets_the_decrypted_config(self, session: AsyncSession) -> None:
        got = await resolve_credential(session, provisional_principal(OWNER), self._db())

        assert got.source is CredentialSource.OWNER
        assert got.db_config == {"host": "h", "username": "u", "password": "p"}

    async def test_nobody_else_does_even_with_fallback_on(self, session: AsyncSession) -> None:
        refusal = await _refusal(session, OTHER, self._db(allow_fallback=True))

        assert (refusal.error_code, refusal.status_code) == ("owner_only", 403)

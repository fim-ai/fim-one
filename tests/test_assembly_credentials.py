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


class TestSecretLeftInAuthConfig:
    """A connector row may still carry the owner's secret in ``auth_config``."""

    def _legacy(self, *, allow_fallback: bool) -> SimpleNamespace:
        conn = _connector(allow_fallback=allow_fallback)
        conn.auth_config = {"token_prefix": "Bearer", "default_token": "in-config"}
        return conn

    async def test_it_is_the_owners_stored_credential(self, session: AsyncSession) -> None:
        got = await resolve_credential(
            session, provisional_principal(OWNER), self._legacy(allow_fallback=False)
        )

        assert got.source is CredentialSource.OWNER
        assert got.connector == {"default_token": "in-config"}

    async def test_it_is_lent_only_when_fallback_is_on(self, session: AsyncSession) -> None:
        got = await resolve_credential(
            session, provisional_principal(OTHER), self._legacy(allow_fallback=True)
        )
        assert got.source is CredentialSource.OWNER_FALLBACK
        assert got.connector == {"default_token": "in-config"}

        refusal = await _refusal(session, OTHER, self._legacy(allow_fallback=False))
        assert refusal.error_code == "credential_required"

    async def test_the_default_row_wins_over_it(self, session: AsyncSession) -> None:
        await _store(session, None, {"default_token": "in-row"})

        got = await resolve_credential(
            session, provisional_principal(OWNER), self._legacy(allow_fallback=False)
        )

        assert got.connector == {"default_token": "in-row"}

    async def test_it_does_not_fill_in_for_a_users_own_credential(
        self, session: AsyncSession
    ) -> None:
        await _store(session, OTHER, {"token": "mine"})

        got = await resolve_credential(
            session, provisional_principal(OTHER), self._legacy(allow_fallback=True)
        )

        assert got.connector == {"token": "mine"}


class TestConnectorThatTakesNoCredential:
    """B6: such a connector was hidden from a non-owner unless fallback was on."""

    @pytest.mark.parametrize("allow_fallback", [True, False])
    @pytest.mark.parametrize("user_id", [OWNER, OTHER, ""])
    async def test_anyone_who_may_use_it_calls_it_with_nothing(
        self, session: AsyncSession, allow_fallback: bool, user_id: str
    ) -> None:
        got = await resolve_credential(
            session,
            provisional_principal(user_id),
            _connector(allow_fallback=allow_fallback, auth_type="none"),
        )

        assert got.source is CredentialSource.NONE
        assert got.connector == {}

    async def test_a_stored_credential_is_not_sent_either(self, session: AsyncSession) -> None:
        await _store(session, None, {"default_token": "left-over"})
        await _store(session, OTHER, {"token": "mine"})

        got = await resolve_credential(
            session, provisional_principal(OTHER), _connector(allow_fallback=True, auth_type="none")
        )

        assert got.source is CredentialSource.NONE
        assert got.connector == {}


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


# ---------------------------------------------------------------------------
# MCP servers
# ---------------------------------------------------------------------------

OWNER_ENV = {"API_KEY": "owner-key", "REGION": "owner-region"}
OWNER_HEADERS = {"Authorization": "Bearer owner-token", "X-Tenant": "owner-tenant"}


def _mcp(*, transport: str, allow_fallback: bool) -> SimpleNamespace:
    return SimpleNamespace(
        id="srv-1",
        name="shared-server",
        transport=transport,
        env=dict(OWNER_ENV),
        headers=dict(OWNER_HEADERS),
        allow_fallback=allow_fallback,
        user_id=OWNER,
    )


async def _save_mcp(
    session: AsyncSession,
    user_id: str,
    *,
    env: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> None:
    from fim_one.db.models.mcp_server_credential import MCPServerCredential

    session.add(
        MCPServerCredential(server_id="srv-1", user_id=user_id, env_blob=env, headers_blob=headers)
    )
    await session.commit()


class TestMcpServer:
    @pytest.mark.parametrize("allow_fallback", [True, False])
    @pytest.mark.parametrize("user_id", [OWNER, OTHER])
    async def test_own_credential_that_fits_is_used_alone(
        self, session: AsyncSession, allow_fallback: bool, user_id: str
    ) -> None:
        """Never merged: a key the owner set and the user did not is not sent."""
        await _save_mcp(session, user_id, env={"API_KEY": "mine"})

        got = await resolve_credential(
            session,
            provisional_principal(user_id),
            _mcp(transport="stdio", allow_fallback=allow_fallback),
        )

        assert got.source is CredentialSource.PERSONAL
        assert got.env == {"API_KEY": "mine"}
        assert got.headers is None

    async def test_own_headers_fit_an_http_server(self, session: AsyncSession) -> None:
        await _save_mcp(session, OTHER, headers={"Authorization": "Bearer mine"})

        got = await resolve_credential(
            session,
            provisional_principal(OTHER),
            _mcp(transport="streamable_http", allow_fallback=False),
        )

        assert got.source is CredentialSource.PERSONAL
        assert got.headers == {"Authorization": "Bearer mine"}

    @pytest.mark.parametrize("transport", ["sse", "streamable_http"])
    async def test_env_only_credential_on_an_http_server_does_not_borrow_the_owners_headers(
        self, session: AsyncSession, transport: str
    ) -> None:
        """N1: an env-only credential passed the gate and the owner's headers went out."""
        await _save_mcp(session, OTHER, env={"ANYTHING": "x"})

        refusal = await _refusal(session, OTHER, _mcp(transport=transport, allow_fallback=False))

        assert (refusal.error_code, refusal.status_code) == ("credential_required", 403)
        assert "Save your own HTTP headers" in refusal.detail

    async def test_headers_only_credential_on_a_stdio_server_is_refused(
        self, session: AsyncSession
    ) -> None:
        await _save_mcp(session, OTHER, headers={"Authorization": "Bearer mine"})

        refusal = await _refusal(session, OTHER, _mcp(transport="stdio", allow_fallback=False))

        assert refusal.error_code == "credential_required"
        assert "Save your own environment variables" in refusal.detail

    async def test_owner_uses_what_is_stored_on_the_server(self, session: AsyncSession) -> None:
        got = await resolve_credential(
            session, provisional_principal(OWNER), _mcp(transport="stdio", allow_fallback=False)
        )

        assert got.source is CredentialSource.OWNER
        assert (got.env, got.headers) == (OWNER_ENV, OWNER_HEADERS)

    async def test_someone_else_borrows_when_fallback_is_on(self, session: AsyncSession) -> None:
        # A credential that does not fit the transport does not count.
        await _save_mcp(session, OTHER, env={"ANYTHING": "x"})

        got = await resolve_credential(
            session,
            provisional_principal(OTHER),
            _mcp(transport="streamable_http", allow_fallback=True),
        )

        assert got.source is CredentialSource.OWNER_FALLBACK
        assert got.headers == OWNER_HEADERS

    async def test_no_identity_and_fallback_off_is_refused(self, session: AsyncSession) -> None:
        refusal = await _refusal(session, "", _mcp(transport="stdio", allow_fallback=False))

        assert refusal.error_code == "credential_required"

    @pytest.mark.parametrize("allow_fallback", [True, False])
    @pytest.mark.parametrize("user_id", [OWNER, OTHER])
    async def test_a_failed_lookup_is_refused_not_downgraded(
        self, allow_fallback: bool, user_id: str
    ) -> None:
        """B4: the error used to fall through to the server's own credentials."""
        broken = AsyncMock()
        broken.execute = AsyncMock(side_effect=RuntimeError("db down"))

        with pytest.raises(AssemblyRefusal) as caught:
            await resolve_credential(
                broken,
                provisional_principal(user_id),
                _mcp(transport="streamable_http", allow_fallback=allow_fallback),
            )

        assert (caught.value.error_code, caught.value.status_code) == (
            "credential_unavailable",
            503,
        )


class TestMcpServerThatStoresNothing:
    """B6: a server with no env and no headers was gated by the fallback switch."""

    def _bare(self, *, allow_fallback: bool) -> SimpleNamespace:
        server = _mcp(transport="stdio", allow_fallback=allow_fallback)
        server.env, server.headers = None, {}
        return server

    @pytest.mark.parametrize("allow_fallback", [True, False])
    @pytest.mark.parametrize("user_id", [OWNER, OTHER])
    async def test_anyone_who_may_use_it_opens_it_with_nothing(
        self, session: AsyncSession, allow_fallback: bool, user_id: str
    ) -> None:
        got = await resolve_credential(
            session, provisional_principal(user_id), self._bare(allow_fallback=allow_fallback)
        )

        assert got.source is CredentialSource.NONE
        assert (got.env, got.headers) == (None, None)

    async def test_a_key_the_user_brought_is_still_used(self, session: AsyncSession) -> None:
        await _save_mcp(session, OTHER, env={"API_KEY": "mine"})

        got = await resolve_credential(
            session, provisional_principal(OTHER), self._bare(allow_fallback=False)
        )

        assert got.source is CredentialSource.PERSONAL
        assert got.env == {"API_KEY": "mine"}

"""Which credential a call goes out with (``core/assembly/credentials.py``)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.assembly.credentials import (
    CredentialSource,
    resolve_credential,
    resolve_credentials,
)
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

    async def test_a_blank_row_of_ones_own_does_not_borrow_the_owners(
        self, session: AsyncSession
    ) -> None:
        """A personal row decides even when it holds nothing."""
        await _store(session, None, {"default_token": "owner"})
        await _store(session, OTHER, {})

        got = await resolve_credential(
            session, provisional_principal(OTHER), _connector(allow_fallback=True)
        )
        assert got.source is CredentialSource.NONE
        assert got.connector == {}

        refusal = await _refusal(session, OTHER, _connector(allow_fallback=False))
        assert refusal.error_code == "credential_required"

    @pytest.mark.parametrize("allow_fallback", [True, False])
    async def test_a_row_of_ones_own_that_cannot_be_decrypted_is_refused(
        self, session: AsyncSession, allow_fallback: bool
    ) -> None:
        """It read as empty: the call went out bare with fallback on, and was
        reported as "save your own credential" with it off."""
        await _store(session, None, {"default_token": "owner"})
        session.add(
            ConnectorCredential(connector_id=CID, user_id=OTHER, credentials_blob="not-a-token")
        )
        await session.commit()

        refusal = await _refusal(session, OTHER, _connector(allow_fallback=allow_fallback))

        assert (refusal.error_code, refusal.status_code) == ("credential_unavailable", 503)

    async def test_an_owners_credential_that_cannot_be_decrypted_is_refused(
        self, session: AsyncSession
    ) -> None:
        """It read as empty and the owner's call went out with no credential."""
        session.add(
            ConnectorCredential(connector_id=CID, user_id=None, credentials_blob="not-a-token")
        )
        await session.commit()

        for user_id in (OWNER, OTHER):
            refusal = await _refusal(session, user_id, _connector(allow_fallback=True))
            assert refusal.error_code == "credential_unavailable"

    async def test_a_plaintext_row_from_before_encryption_is_still_read(
        self, session: AsyncSession
    ) -> None:
        session.add(
            ConnectorCredential(
                connector_id=CID, user_id=None, credentials_blob='{"default_token": "legacy"}'
            )
        )
        await session.commit()

        got = await resolve_credential(
            session, provisional_principal(OWNER), _connector(allow_fallback=False)
        )

        assert got.connector == {"default_token": "legacy"}

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
    """No env and no headers stored is not "takes no credential".

    The server's URL and command arguments are the owner's too and can carry
    a token, so the fallback switch gates the server whatever it stores.
    """

    def _bare(self, *, allow_fallback: bool) -> SimpleNamespace:
        server = _mcp(transport="stdio", allow_fallback=allow_fallback)
        server.env, server.headers = None, {}
        return server

    @pytest.mark.parametrize(
        ("user_id", "allow_fallback"), [(OWNER, True), (OWNER, False), (OTHER, True)]
    )
    async def test_the_owner_and_those_lent_to_open_it_with_nothing(
        self, session: AsyncSession, user_id: str, allow_fallback: bool
    ) -> None:
        got = await resolve_credential(
            session, provisional_principal(user_id), self._bare(allow_fallback=allow_fallback)
        )

        assert got.source is CredentialSource.NONE
        assert (got.env, got.headers) == (None, None)

    @pytest.mark.parametrize("user_id", [OTHER, ""])
    async def test_someone_else_is_still_refused_when_fallback_is_off(
        self, session: AsyncSession, user_id: str
    ) -> None:
        """It was opened to them with no credential at all (source ``none``)."""
        refusal = await _refusal(session, user_id, self._bare(allow_fallback=False))

        assert refusal.error_code == "credential_required"

    async def test_a_key_the_user_brought_is_still_used(self, session: AsyncSession) -> None:
        """Over HTTP. (A stdio server takes only the variables its owner declared.)"""
        await _save_mcp(session, OTHER, headers={"Authorization": "Bearer mine"})
        server = self._bare(allow_fallback=False)
        server.transport = "streamable_http"

        got = await resolve_credential(session, provisional_principal(OTHER), server)

        assert got.source is CredentialSource.PERSONAL
        assert got.headers == {"Authorization": "Bearer mine"}

    async def test_a_variable_the_stdio_server_did_not_declare_is_refused(
        self, session: AsyncSession
    ) -> None:
        """The environment decides what the command loads: it is the owner's to list."""
        await _save_mcp(session, OTHER, env={"API_KEY": "mine"})

        refusal = await _refusal(session, OTHER, self._bare(allow_fallback=False))

        assert refusal.error_code == "stdio_mcp_env_not_allowed"
        assert "API_KEY" in refusal.detail


class TestMcpServerWhoseAddressCarriesACredential:
    """The URL and the launch arguments are the owner's, whoever connects.

    Where they hold a credential, a personal ``env`` or ``headers`` does not
    replace it: with fallback off the server is its owner's alone.
    """

    def _flagged(self, transport: str, *, allow_fallback: bool) -> SimpleNamespace:
        server = _mcp(transport=transport, allow_fallback=allow_fallback)
        if transport == "stdio":
            server.args = ["-y", "pkg", "--api-key", "sk-owner-0123456789"]
            server.url = None
        else:
            server.args = None
            server.url = "https://mcp.example.com/mcp?token=owner-token"
        return server

    async def _save_fitting(self, session: AsyncSession, user_id: str, transport: str) -> None:
        if transport == "stdio":
            await _save_mcp(session, user_id, env={"API_KEY": "mine"})
        else:
            await _save_mcp(session, user_id, headers={"Authorization": "Bearer mine"})

    @pytest.mark.parametrize("transport", ["stdio", "sse", "streamable_http"])
    @pytest.mark.parametrize("has_personal", [True, False])
    @pytest.mark.parametrize("user_id", [OTHER, ""])
    async def test_someone_else_is_refused_when_fallback_is_off_whatever_they_saved(
        self, session: AsyncSession, transport: str, has_personal: bool, user_id: str
    ) -> None:
        """A saved credential of their own let them in, on the owner's URL or arguments."""
        if has_personal and user_id:
            await self._save_fitting(session, user_id, transport)

        refusal = await _refusal(session, user_id, self._flagged(transport, allow_fallback=False))

        assert (refusal.error_code, refusal.status_code) == ("mcp_server_address_private", 403)
        # The refusal says where the credential is, never what it is.
        assert "sk-owner" not in refusal.detail and "owner-token" not in refusal.detail
        assert ("launch arguments" if transport == "stdio" else "address") in refusal.detail

    @pytest.mark.parametrize("transport", ["stdio", "streamable_http"])
    @pytest.mark.parametrize("allow_fallback", [True, False])
    async def test_the_owner_is_unaffected(
        self, session: AsyncSession, transport: str, allow_fallback: bool
    ) -> None:
        got = await resolve_credential(
            session,
            provisional_principal(OWNER),
            self._flagged(transport, allow_fallback=allow_fallback),
        )

        assert got.source is CredentialSource.OWNER

    @pytest.mark.parametrize("transport", ["stdio", "streamable_http"])
    async def test_with_fallback_on_nothing_changes(
        self, session: AsyncSession, transport: str
    ) -> None:
        """The owner has agreed to lend the server as configured."""
        server = self._flagged(transport, allow_fallback=True)

        borrowed = await resolve_credential(session, provisional_principal(OTHER), server)
        assert borrowed.source is CredentialSource.OWNER_FALLBACK

        await self._save_fitting(session, OTHER, transport)
        own = await resolve_credential(session, provisional_principal(OTHER), server)
        assert own.source is CredentialSource.PERSONAL

    async def test_it_is_answered_without_looking_anything_up(self) -> None:
        """No credential of the caller's could change the answer."""
        db = AsyncMock()

        (answer,) = await resolve_credentials(
            db, provisional_principal(OTHER), [self._flagged("stdio", allow_fallback=False)]
        )

        assert isinstance(answer, AssemblyRefusal)
        assert answer.error_code == "mcp_server_address_private"
        db.execute.assert_not_awaited()

    async def test_it_does_not_cost_the_other_servers_their_answer(
        self, session: AsyncSession
    ) -> None:
        plain = _mcp(transport="stdio", allow_fallback=True)
        plain.id = "srv-2"

        flagged, lent = await resolve_credentials(
            session,
            provisional_principal(OTHER),
            [self._flagged("stdio", allow_fallback=False), plain],
        )

        assert isinstance(flagged, AssemblyRefusal)
        assert not isinstance(lent, AssemblyRefusal)
        assert lent.source is CredentialSource.OWNER_FALLBACK

    def test_the_code_has_a_message_for_the_interface(self) -> None:
        import json
        from pathlib import Path

        messages = json.loads(
            (Path(__file__).resolve().parents[1] / "frontend/messages/en/errors.json").read_text()
        )
        assert "mcp_server_address_private" in messages


class TestManyResourcesOneLookup:
    """A chat with no agent resolves every connector and server the user can
    see on every turn: the lookups must not grow with their number."""

    async def test_connectors_and_servers_take_one_query_each(self, session: AsyncSession) -> None:
        from sqlalchemy import event

        connectors = []
        for n in range(6):
            conn = _connector(allow_fallback=n % 2 == 0)
            conn.id = f"conn-{n}"
            connectors.append(conn)
            session.add(
                ConnectorCredential(
                    connector_id=conn.id,
                    user_id=None,
                    credentials_blob=encrypt_credential({"default_token": f"owner-{n}"}),
                )
            )
        session.add(
            ConnectorCredential(
                connector_id="conn-1",
                user_id=OTHER,
                credentials_blob=encrypt_credential({"token": "mine"}),
            )
        )
        servers = []
        for n in range(4):
            server = _mcp(transport="stdio", allow_fallback=True)
            server.id = f"srv-{n}"
            servers.append(server)
        await session.commit()

        statements: list[str] = []
        engine = session.get_bind()

        def _count(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", _count)
        try:
            answers = await resolve_credentials(
                session, provisional_principal(OTHER), [*connectors, *servers]
            )
        finally:
            event.remove(engine, "before_cursor_execute", _count)

        assert len(statements) == 2, statements
        sources = [
            a.error_code if isinstance(a, AssemblyRefusal) else str(a.source) for a in answers
        ]
        assert sources == [
            "owner_fallback",  # conn-0: fallback on
            "personal",  # conn-1: fallback off, but a credential of their own
            "owner_fallback",
            "credential_required",  # conn-3: fallback off
            "owner_fallback",
            "credential_required",
            *["owner_fallback"] * 4,  # the servers
        ]
        first = answers[0]
        assert not isinstance(first, AssemblyRefusal)
        assert first.connector == {"default_token": "owner-0"}


class TestMcpSecretThatCannotBeDecrypted:
    """An unreadable blob reads as ``{}`` through the column type."""

    async def _damage(self, session: AsyncSession, table: str, column: str) -> None:
        from sqlalchemy import text

        await session.execute(text(f"UPDATE {table} SET {column} = 'not-a-token'"))
        await session.commit()
        session.expire_all()

    async def test_ones_own_unreadable_credential_does_not_become_the_owners(
        self, session: AsyncSession
    ) -> None:
        """With fallback on it silently became ``owner_fallback``."""
        await _save_mcp(session, OTHER, env={"API_KEY": "mine"})
        await self._damage(session, "mcp_server_credentials", "env_blob")

        refusal = await _refusal(session, OTHER, _mcp(transport="stdio", allow_fallback=True))

        assert (refusal.error_code, refusal.status_code) == ("credential_unavailable", 503)

    @pytest.mark.parametrize("user_id", [OWNER, OTHER])
    async def test_the_owners_unreadable_secret_does_not_become_no_secret(
        self, session: AsyncSession, user_id: str
    ) -> None:
        """The server was opened with nothing, recorded as ``none``."""
        from fim_one.db.models.mcp_server import MCPServer

        session.add(
            MCPServer(
                id="srv-1",
                user_id=OWNER,
                name="shared-server",
                transport="stdio",
                command="npx",
                env={"API_KEY": "owner-key"},
                allow_fallback=True,
            )
        )
        await session.commit()
        await self._damage(session, "mcp_servers", "env")
        server = await session.get(MCPServer, "srv-1")
        assert server is not None and server.env == {}

        refusal = await _refusal(session, user_id, server)

        assert refusal.error_code == "credential_unavailable"

    async def test_a_server_that_really_stores_an_empty_object_is_opened(
        self, session: AsyncSession
    ) -> None:
        from fim_one.db.models.mcp_server import MCPServer

        session.add(
            MCPServer(
                id="srv-1",
                user_id=OWNER,
                name="shared-server",
                transport="stdio",
                command="npx",
                env={},
                allow_fallback=True,
            )
        )
        await session.commit()
        server = await session.get(MCPServer, "srv-1")

        got = await resolve_credential(session, provisional_principal(OWNER), server)

        assert got.source is CredentialSource.NONE

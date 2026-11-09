"""Saving a personal MCP credential checks it against the server's transport.

The save endpoint used to accept any mix of ``env`` and ``headers``, and the
portal form only ever sent ``env``.  For an SSE / HTTP server that stored a
credential the connection never reads, and the server then showed as
"configured".  The endpoints now agree with the use-time rule in
``core/mcp/credentials.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.db.base import Base
from fim_one.db.models.mcp_server import MCPServer
from fim_one.db.models.mcp_server_credential import MCPServerCredential
from fim_one.db.models.resource_subscription import ResourceSubscription
from fim_one.web.api.mcp_servers import (
    get_mcp_server,
    get_my_mcp_credentials,
    list_mcp_servers,
    upsert_my_mcp_credentials,
)
from fim_one.web.exceptions import AppError
from fim_one.web.schemas.mcp_server import MCPMyCredentialUpsert

OWNER = "owner-user"
SUBSCRIBER = "subscriber-user"
HTTP_ID = "srv-http"
STDIO_ID = "srv-stdio"


def _user(user_id: str) -> Any:
    return SimpleNamespace(id=user_id, is_admin=False)


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
        s.add(
            MCPServer(
                id=HTTP_ID,
                user_id=OWNER,
                name="http-server",
                transport="streamable_http",
                url="https://mcp.example.com/mcp",
                headers={"Authorization": "Bearer owner-token"},
                visibility="org",
                org_id="org-1",
                allow_fallback=False,
            )
        )
        s.add(
            MCPServer(
                id=STDIO_ID,
                user_id=OWNER,
                name="stdio-server",
                transport="stdio",
                command="npx",
                env={"API_KEY": "owner-key"},
                visibility="org",
                org_id="org-1",
                allow_fallback=False,
            )
        )
        for sid in (HTTP_ID, STDIO_ID):
            s.add(
                ResourceSubscription(
                    user_id=SUBSCRIBER,
                    resource_type="mcp_server",
                    resource_id=sid,
                    org_id="org-1",
                )
            )
        await s.commit()
        yield s
    await engine.dispose()


async def _save(
    session: AsyncSession,
    server_id: str,
    *,
    env: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    resp = await upsert_my_mcp_credentials(
        server_id,
        MCPMyCredentialUpsert(env=env, headers=headers),
        current_user=_user(SUBSCRIBER),
        db=session,
    )
    data: dict[str, Any] = resp.data
    return data


class TestSaveChecksTransport:
    async def test_env_only_on_http_server_is_refused(self, session: AsyncSession) -> None:
        with pytest.raises(AppError) as exc:
            await _save(session, HTTP_ID, env={"ANYTHING": "x"})

        assert exc.value.status_code == 400
        assert exc.value.error_code == "mcp_credential_needs_headers"

    async def test_headers_only_on_stdio_server_is_refused(self, session: AsyncSession) -> None:
        with pytest.raises(AppError) as exc:
            await _save(session, STDIO_ID, headers={"Authorization": "Bearer mine"})

        assert exc.value.status_code == 400
        assert exc.value.error_code == "mcp_credential_needs_env"

    async def test_headers_on_http_server_are_saved(self, session: AsyncSession) -> None:
        assert await _save(session, HTTP_ID, headers={"Authorization": "Bearer mine"}) == {
            "saved": True
        }

    async def test_env_on_stdio_server_is_saved(self, session: AsyncSession) -> None:
        assert await _save(session, STDIO_ID, env={"API_KEY": "mine"}) == {"saved": True}

    async def test_extra_field_alongside_the_needed_one_is_accepted(
        self, session: AsyncSession
    ) -> None:
        assert await _save(
            session, HTTP_ID, env={"ANYTHING": "x"}, headers={"Authorization": "Bearer mine"}
        ) == {"saved": True}

    async def test_empty_body_still_clears(self, session: AsyncSession) -> None:
        await _save(session, HTTP_ID, headers={"Authorization": "Bearer mine"})

        assert await _save(session, HTTP_ID) == {"saved": True, "cleared": True}


class TestStatusFollowsTransport:
    async def test_template_names_the_servers_header_keys_not_values(
        self, session: AsyncSession
    ) -> None:
        resp = await get_my_mcp_credentials(HTTP_ID, current_user=_user(SUBSCRIBER), db=session)

        assert resp.data["has_credentials"] is False
        assert resp.data["header_keys"] == ["Authorization"]
        assert resp.data["headers"] == {}

    async def test_saved_headers_come_back_for_editing(self, session: AsyncSession) -> None:
        await _save(session, HTTP_ID, headers={"Authorization": "Bearer mine"})

        resp = await get_my_mcp_credentials(HTTP_ID, current_user=_user(SUBSCRIBER), db=session)

        assert resp.data["has_credentials"] is True
        assert resp.data["headers"] == {"Authorization": "Bearer mine"}

    async def test_stale_env_only_row_on_http_server_does_not_count(
        self, session: AsyncSession
    ) -> None:
        """Rows the old form saved: the card must ask for keys again."""
        session.add(
            MCPServerCredential(server_id=HTTP_ID, user_id=SUBSCRIBER, env_blob={"ANYTHING": "x"})
        )
        await session.commit()

        status = await get_my_mcp_credentials(HTTP_ID, current_user=_user(SUBSCRIBER), db=session)
        detail = await get_mcp_server(HTTP_ID, current_user=_user(SUBSCRIBER), db=session)
        listing = await list_mcp_servers(
            page=1, size=50, current_user=_user(SUBSCRIBER), db=session
        )

        assert status.data["has_credentials"] is False
        assert detail.data["my_has_credentials"] is False
        by_id = {item["id"]: item for item in listing.items}
        assert by_id[HTTP_ID]["my_has_credentials"] is False

    async def test_fitting_credentials_count_in_the_list(self, session: AsyncSession) -> None:
        await _save(session, HTTP_ID, headers={"Authorization": "Bearer mine"})
        await _save(session, STDIO_ID, env={"API_KEY": "mine"})

        listing = await list_mcp_servers(
            page=1, size=50, current_user=_user(SUBSCRIBER), db=session
        )

        by_id = {item["id"]: item for item in listing.items}
        assert by_id[HTTP_ID]["my_has_credentials"] is True
        assert by_id[STDIO_ID]["my_has_credentials"] is True

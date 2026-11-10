"""What the MCP server API says about a credential in a server's address.

With fallback off, a server whose URL or launch arguments carry a
credential can be used by its owner only (``core/assembly/credentials.py``).
Saving it is allowed; the API tells the owner once the server is shared,
tells everyone else why they cannot use it, and never shows them the
address.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.db.base import Base
from fim_one.db.models.mcp_server import MCPServer
from fim_one.db.models.mcp_server_credential import MCPServerCredential
from fim_one.db.models.organization import Organization, OrgMembership
from fim_one.db.models.resource_subscription import ResourceSubscription
from fim_one.db.models.user import User
from fim_one.web.api.mcp_servers import (
    create_mcp_server,
    get_mcp_server,
    list_mcp_servers,
    publish_mcp_server,
    unpublish_mcp_server,
    update_mcp_server,
)
from fim_one.web.schemas.common import PublishRequest
from fim_one.web.schemas.mcp_server import MCPServerCreate, MCPServerUpdate

OWNER = "owner-user"
SUBSCRIBER = "subscriber-user"
ORG = "org-1"
SECRET_URL = "https://user:pw-in-url@mcp.example.com/mcp?token=tok-in-url#frag-in-url"
SECRET_ARGS = ["-y", "some-server", "--api-key", "sk-owner-0123456789"]
CODE = "mcp_server_address_private"


def _user(user_id: str) -> Any:
    return SimpleNamespace(id=user_id, username=user_id, is_admin=False)


@pytest.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        for uid in (OWNER, SUBSCRIBER):
            s.add(User(id=uid, email=f"{uid}@x.io", username=uid))
        s.add(Organization(id=ORG, name=ORG, slug=ORG, owner_id=OWNER))
        for uid in (OWNER, SUBSCRIBER):
            s.add(OrgMembership(org_id=ORG, user_id=uid, role="member"))
        await s.commit()
        yield s
    await engine.dispose()


async def _create(session: AsyncSession, **fields: Any) -> dict[str, Any]:
    body = MCPServerCreate(name="srv", **fields)
    resp = await create_mcp_server(body, current_user=_user(OWNER), db=session)
    data: dict[str, Any] = resp.data
    return data


async def _update(session: AsyncSession, server_id: str, **fields: Any) -> dict[str, Any]:
    resp = await update_mcp_server(
        server_id, MCPServerUpdate(**fields), current_user=_user(OWNER), db=session
    )
    data: dict[str, Any] = resp.data
    return data


async def _publish(session: AsyncSession, server_id: str) -> dict[str, Any]:
    resp = await publish_mcp_server(
        server_id, PublishRequest(scope="org", org_id=ORG), current_user=_user(OWNER), db=session
    )
    data: dict[str, Any] = resp.data
    return data


async def _subscribe(session: AsyncSession, server_id: str) -> None:
    session.add(
        ResourceSubscription(
            user_id=SUBSCRIBER, resource_type="mcp_server", resource_id=server_id, org_id=ORG
        )
    )
    await session.commit()


async def _as_subscriber(session: AsyncSession, server_id: str) -> tuple[dict[str, Any], Any]:
    """The server as the subscriber's detail and list endpoints return it."""
    detail = (await get_mcp_server(server_id, current_user=_user(SUBSCRIBER), db=session)).data
    listed = await list_mcp_servers(page=1, size=50, current_user=_user(SUBSCRIBER), db=session)
    (row,) = [item for item in listed.items if item["id"] == server_id]
    return detail, row


class TestTheOwnerIsWarnedOnceTheServerIsShared:
    async def test_saving_is_allowed_and_a_personal_server_carries_no_warning(
        self, session: AsyncSession
    ) -> None:
        created = await _create(session, transport="streamable_http", url=SECRET_URL)

        assert created["url"] == SECRET_URL
        assert created["address_private"] is True
        # Nobody else can reach a personal server: nothing to warn about yet.
        assert created["warning"] is None

    async def test_publishing_it_answers_with_the_warning(self, session: AsyncSession) -> None:
        created = await _create(session, transport="streamable_http", url=SECRET_URL)

        published = await _publish(session, created["id"])

        assert published["visibility"] == "org"
        assert published["warning"] == CODE
        # Which part of the address counted, so the owner knows where to look.
        assert published["warning_reasons"] == ["url_user_info", "url_query", "url_fragment"]
        for secret in ("pw-in-url", "tok-in-url", "frag-in-url"):
            assert secret not in repr(published["warning_reasons"])

    async def test_a_stdio_server_is_judged_by_its_launch_arguments(
        self, session: AsyncSession
    ) -> None:
        created = await _create(session, transport="stdio", command="npx", args=SECRET_ARGS)

        published = await _publish(session, created["id"])

        assert published["warning"] == CODE
        assert published["warning_reasons"] == ["arg_name", "arg_token"]

    async def test_an_edit_that_puts_a_credential_in_the_address_answers_with_the_warning(
        self, session: AsyncSession
    ) -> None:
        created = await _create(
            session, transport="streamable_http", url="https://mcp.example.com/mcp"
        )
        assert (await _publish(session, created["id"]))["warning"] is None

        updated = await _update(session, created["id"], url=SECRET_URL)

        assert updated["warning"] == CODE
        assert updated["address_private"] is True

    async def test_switching_fallback_off_on_a_shared_server_answers_with_the_warning(
        self, session: AsyncSession
    ) -> None:
        created = await _create(
            session, transport="streamable_http", url=SECRET_URL, allow_fallback=True
        )
        assert (await _publish(session, created["id"]))["warning"] is None

        updated = await _update(session, created["id"], allow_fallback=False)

        assert updated["warning"] == CODE

    @pytest.mark.parametrize(
        "remedy",
        [
            {"allow_fallback": True},
            {"url": "https://mcp.example.com/mcp", "headers": {"Authorization": "Bearer t"}},
        ],
        ids=["allow-fallback", "move-it-into-a-header"],
    )
    async def test_either_remedy_clears_it(
        self, session: AsyncSession, remedy: dict[str, Any]
    ) -> None:
        created = await _create(session, transport="streamable_http", url=SECRET_URL)
        assert (await _publish(session, created["id"]))["warning"] == CODE

        updated = await _update(session, created["id"], **remedy)

        assert updated["warning"] is None
        assert updated["address_private"] is False

    async def test_unpublishing_clears_it(self, session: AsyncSession) -> None:
        created = await _create(session, transport="streamable_http", url=SECRET_URL)
        await _publish(session, created["id"])

        resp = await unpublish_mcp_server(created["id"], current_user=_user(OWNER), db=session)

        assert resp.data["warning"] is None

    async def test_the_owners_list_and_detail_carry_it_too(self, session: AsyncSession) -> None:
        created = await _create(session, transport="streamable_http", url=SECRET_URL)
        await _publish(session, created["id"])

        detail = (await get_mcp_server(created["id"], current_user=_user(OWNER), db=session)).data
        listed = await list_mcp_servers(page=1, size=50, current_user=_user(OWNER), db=session)

        assert detail["warning"] == CODE
        assert [item["warning"] for item in listed.items] == [CODE]


class TestWhatSomeoneElseSees:
    async def _shared(self, session: AsyncSession, **fields: Any) -> str:
        created = await _create(session, **fields)
        await _publish(session, created["id"])
        # Approve, as an organization without review does.
        server = await session.get(MCPServer, created["id"])
        assert server is not None
        server.publish_status = None if server.publish_status is None else "approved"
        await session.commit()
        await _subscribe(session, created["id"])
        server_id: str = created["id"]
        return server_id

    async def test_they_are_told_the_server_is_its_owners_alone(
        self, session: AsyncSession
    ) -> None:
        server_id = await self._shared(session, transport="streamable_http", url=SECRET_URL)
        # Even with a credential of their own saved.
        session.add(
            MCPServerCredential(
                server_id=server_id, user_id=SUBSCRIBER, headers_blob={"Authorization": "mine"}
            )
        )
        await session.commit()

        for seen in await _as_subscriber(session, server_id):
            assert seen["address_private"] is True
            assert seen["my_has_credentials"] is True
            # The warning is the owner's to act on.
            assert seen["warning"] is None
            assert seen["warning_reasons"] == []

    @pytest.mark.parametrize("allow_fallback", [True, False])
    @pytest.mark.parametrize(
        "fields",
        [
            {"transport": "streamable_http", "url": SECRET_URL},
            {"transport": "stdio", "command": "npx", "args": SECRET_ARGS},
        ],
        ids=["http", "stdio"],
    )
    async def test_they_never_see_the_address(
        self, session: AsyncSession, fields: dict[str, Any], allow_fallback: bool
    ) -> None:
        server_id = await self._shared(session, allow_fallback=allow_fallback, **fields)

        for seen in await _as_subscriber(session, server_id):
            assert seen["url"] is None
            assert seen["args"] is None
            assert seen["command"] is None
            text = repr(seen)
            for secret in ("pw-in-url", "tok-in-url", "frag-in-url", "sk-owner", "mcp.example"):
                assert secret not in text
            assert seen["address_private"] is (not allow_fallback)

    async def test_a_plain_server_is_not_marked(self, session: AsyncSession) -> None:
        server_id = await self._shared(
            session, transport="streamable_http", url="https://mcp.example.com/mcp"
        )

        for seen in await _as_subscriber(session, server_id):
            assert seen["address_private"] is False


class TestAPersonalServerUsedByASharedAgentOrWorkflow:
    """Runners of the agent or workflow are refused the server like subscribers are."""

    async def _personal(self, session: AsyncSession) -> str:
        created = await _create(session, transport="streamable_http", url=SECRET_URL)
        assert created["warning"] is None
        server_id: str = created["id"]
        return server_id

    async def _views(self, session: AsyncSession, server_id: str) -> list[dict[str, Any]]:
        detail = (await get_mcp_server(server_id, current_user=_user(OWNER), db=session)).data
        listed = await list_mcp_servers(page=1, size=50, current_user=_user(OWNER), db=session)
        updated = await _update(session, server_id, description="edited")
        return [detail, *listed.items, updated]

    async def test_a_shared_agent_that_uses_it_brings_the_warning(
        self, session: AsyncSession
    ) -> None:
        from fim_one.db.models.agent import Agent

        server_id = await self._personal(session)
        session.add(
            Agent(
                id="agent-1",
                user_id=OWNER,
                name="a",
                mcp_server_ids=[server_id],
                visibility="org",
                org_id=ORG,
            )
        )
        await session.commit()

        for seen in await self._views(session, server_id):
            assert seen["visibility"] == "personal"
            assert seen["warning"] == CODE
            assert seen["warning_reasons"] == ["url_user_info", "url_query", "url_fragment"]

    async def test_a_shared_workflow_with_a_step_on_it_brings_the_warning(
        self, session: AsyncSession
    ) -> None:
        from fim_one.db.models.workflow import Workflow

        server_id = await self._personal(session)
        session.add(
            Workflow(
                id="wf-1",
                user_id=OWNER,
                name="w",
                blueprint={
                    "nodes": [
                        {"id": "n1", "type": "mcp", "data": {"type": "MCP", "server_id": server_id}}
                    ],
                    "edges": [],
                },
                status="active",
                visibility="org",
                org_id=ORG,
            )
        )
        await session.commit()

        for seen in await self._views(session, server_id):
            assert seen["warning"] == CODE

    async def test_a_personal_agent_or_one_that_uses_another_server_does_not(
        self, session: AsyncSession
    ) -> None:
        from fim_one.db.models.agent import Agent

        server_id = await self._personal(session)
        session.add(Agent(id="agent-1", user_id=OWNER, name="mine", mcp_server_ids=[server_id]))
        session.add(
            Agent(
                id="agent-2",
                user_id=OWNER,
                name="shared",
                mcp_server_ids=["another"],
                visibility="org",
                org_id=ORG,
            )
        )
        await session.commit()

        for seen in await self._views(session, server_id):
            assert seen["warning"] is None


class TestTheMarketListing:
    @pytest.mark.parametrize(
        ("url", "allow_fallback", "expected"),
        [
            (SECRET_URL, False, True),
            (SECRET_URL, True, False),
            ("https://h.example/mcp", False, False),
        ],
    )
    def test_it_says_which_server_only_its_owner_can_use(
        self, url: str, allow_fallback: bool, expected: bool
    ) -> None:
        """Before subscribing, and without showing the address."""
        from datetime import UTC, datetime

        from fim_one.web.api.market import _mcp_market_info

        info = _mcp_market_info(
            MCPServer(
                id="s",
                user_id=OWNER,
                name="srv",
                transport="streamable_http",
                url=url,
                allow_fallback=allow_fallback,
                org_id=ORG,
                created_at=datetime.now(UTC),
            )
        )

        assert info["address_private"] is expected
        assert "url" not in info and "tok-in-url" not in repr(info)

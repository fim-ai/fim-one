"""Connector tools as the assembly layer builds them (``core/assembly/toolset.py``)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.assembly.errors import AssemblyRefusal
from fim_one.core.assembly.principal import CallContext, provisional_principal
from fim_one.core.assembly.toolset import (
    DATABASE_OPERATIONS,
    ToolsetError,
    connector_tools,
    database_tools,
    mcp_tools,
)
from fim_one.core.security.encryption import encrypt_credential, encrypt_db_config
from fim_one.core.tool.connector import ConnectorToolAdapter
from fim_one.db.base import Base
from fim_one.db.models.connector_credential import ConnectorCredential

OWNER = "u-owner"
OTHER = "u-other"


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


def _ctx(user_id: str) -> CallContext:
    return CallContext(principal=provisional_principal(user_id), binding=None, entry="chat")


def _action(name: str, *, flagged: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"act-{name}",
        name=name,
        description=name,
        method="GET",
        path=f"/{name}",
        parameters_schema=None,
        request_body_template=None,
        response_extract=None,
        requires_confirmation=flagged,
    )


def _api(cid: str, *, allow_fallback: bool) -> SimpleNamespace:
    return SimpleNamespace(
        id=cid,
        name=cid,
        description=cid,
        type="api",
        user_id=OWNER,
        base_url="https://api.example.com",
        auth_type="bearer",
        auth_config=None,
        allow_fallback=allow_fallback,
        actions=[_action("ping"), _action("wipe", flagged=True)],
    )


def _database(cid: str, *, configured: bool = True) -> SimpleNamespace:
    column = SimpleNamespace(
        column_name="id",
        data_type="int",
        is_nullable=False,
        is_primary_key=True,
        display_name=None,
        description=None,
        is_pii=False,
        is_visible=True,
    )
    hidden = SimpleNamespace(**{**vars(column), "column_name": "secret", "is_visible": False})
    table = SimpleNamespace(
        table_name="orders",
        display_name=None,
        description=None,
        is_visible=True,
        columns=[column, hidden],
    )
    hidden_table = SimpleNamespace(**{**vars(table), "table_name": "audit", "is_visible": False})
    return SimpleNamespace(
        id=cid,
        name=cid,
        description=cid,
        type="database",
        user_id=OWNER,
        allow_fallback=True,
        db_config=encrypt_db_config({"driver": "postgresql", "host": "h", "password": "p"})
        if configured
        else None,
        database_schemas=[table, hidden_table],
    )


async def _store(session: AsyncSession, cid: str, user_id: str | None, token: str) -> None:
    session.add(
        ConnectorCredential(
            connector_id=cid,
            user_id=user_id,
            credentials_blob=encrypt_credential({"default_token": token}),
        )
    )
    await session.commit()


class TestConnectorTools:
    async def test_progressive_is_one_tool_without_the_connectors_the_user_may_not_call(
        self, session: AsyncSession
    ) -> None:
        await _store(session, "open", None, "tok-open")
        await _store(session, "closed", None, "tok-closed")

        tools: list[Any] = await connector_tools(
            session,
            _ctx(OTHER),
            [_api("open", allow_fallback=True), _api("closed", allow_fallback=False)],
            mode="progressive",
        )

        (meta,) = tools
        assert meta.name == "connector"
        assert meta.connector_names == ["open"]

    async def test_progressive_with_nothing_callable_is_no_tool(
        self, session: AsyncSession
    ) -> None:
        tools = await connector_tools(
            session, _ctx(OTHER), [_api("closed", allow_fallback=False)], mode="progressive"
        )

        assert tools == []

    async def test_legacy_is_one_tool_per_action_with_the_resolved_credential(
        self, session: AsyncSession
    ) -> None:
        await _store(session, "open", None, "tok-open")

        tools: list[Any] = await connector_tools(
            session, _ctx(OWNER), [_api("open", allow_fallback=False)], mode="legacy"
        )

        assert [t.name for t in tools] == ["open__ping", "open__wipe"]
        assert [t.requires_confirmation for t in tools] == [False, True]
        headers: dict[str, str] = {}
        tools[0]._inject_auth(headers)
        assert headers == {"Authorization": "Bearer tok-open"}
        assert tools[0]._on_call_complete is not None

    async def test_strict_raises_the_refusal_instead_of_leaving_the_connector_out(
        self, session: AsyncSession
    ) -> None:
        with pytest.raises(AssemblyRefusal) as caught:
            await connector_tools(
                session,
                _ctx(OTHER),
                [_api("closed", allow_fallback=False)],
                mode="legacy",
                strict=True,
            )

        assert caught.value.error_code == "credential_required"

    async def test_legacy_a_connector_that_cannot_be_built_does_not_cost_the_others(
        self, session: AsyncSession
    ) -> None:
        """The SSRF check in the tool's constructor raised out of the loop,
        and the turn lost every connector and database tool."""
        blocked = _api("blocked", allow_fallback=True)
        blocked.base_url = "http://169.254.169.254"

        tools = await connector_tools(
            session, _ctx(OWNER), [blocked, _api("open", allow_fallback=True)], mode="legacy"
        )

        assert [t.name for t in tools] == ["open__ping", "open__wipe"]
        with pytest.raises(ValueError, match="SSRF"):
            await connector_tools(session, _ctx(OWNER), [blocked], mode="legacy", strict=True)

    async def test_legacy_can_be_limited_to_the_actions_asked_for(
        self, session: AsyncSession
    ) -> None:
        """A workflow step runs one action: it builds one tool, not one per action."""
        built: list[str] = []
        real = ConnectorToolAdapter.__init__

        def _counting(self: Any, *args: Any, **kwargs: Any) -> None:
            built.append(kwargs["action_name"])
            real(self, *args, **kwargs)

        with patch.object(ConnectorToolAdapter, "__init__", _counting):
            tools = await connector_tools(
                session,
                _ctx(OWNER),
                [_api("open", allow_fallback=True)],
                mode="legacy",
                action_ids=["act-wipe"],
            )

        assert [t.name for t in tools] == ["open__wipe"]
        assert built == ["wipe"]


class TestDatabaseTools:
    async def test_only_the_owner_gets_database_tools(self, session: AsyncSession) -> None:
        db = _database("warehouse")

        assert await database_tools(session, _ctx(OTHER), [db], mode="progressive") == []
        tools: list[Any] = await database_tools(session, _ctx(OWNER), [db], mode="progressive")

        (meta,) = tools
        assert meta.name == "database"
        assert meta.database_names == ["warehouse"]
        assert meta._on_call_complete is not None

    async def test_a_database_with_no_connection_config_is_left_out(
        self, session: AsyncSession
    ) -> None:
        tools = await database_tools(
            session, _ctx(OWNER), [_database("draft", configured=False)], mode="legacy"
        )

        assert tools == []

    async def test_legacy_is_three_tools_over_the_visible_schema(
        self, session: AsyncSession
    ) -> None:
        tools: list[Any] = await database_tools(
            session, _ctx(OWNER), [_database("warehouse")], mode="legacy"
        )

        assert [t.name for t in tools] == [
            "warehouse__list_tables",
            "warehouse__describe_table",
            "warehouse__query",
        ]
        listing = await tools[0].run()
        assert "orders" in listing and "audit" not in listing


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------


def _mcp_server(name: str, *, transport: str, allow_fallback: bool) -> SimpleNamespace:
    return SimpleNamespace(
        id=name,
        name=name,
        transport=transport,
        command="npx" if transport == "stdio" else None,
        args=["-y", "srv"],
        working_dir=None,
        url=None if transport == "stdio" else f"https://{name}.example.com/mcp",
        env={"KEY": "owner"},
        headers={"Authorization": "Bearer owner"},
        allow_fallback=allow_fallback,
        user_id=OWNER,
    )

    async def test_legacy_can_be_limited_to_the_operations_asked_for(
        self, session: AsyncSession
    ) -> None:
        """A workflow step runs one operation and builds one tool."""
        for operation in DATABASE_OPERATIONS:
            (tool,) = await database_tools(
                session, _ctx(OWNER), [_database("warehouse")], mode="legacy", operations=[operation]
            )
            assert tool.name == f"warehouse__{operation}"

    async def test_strict_raises_the_refusal_instead_of_leaving_the_database_out(
        self, session: AsyncSession
    ) -> None:
        with pytest.raises(AssemblyRefusal) as refused:
            await database_tools(
                session, _ctx(OTHER), [_database("warehouse")], mode="legacy", strict=True
            )
        assert refused.value.error_code == "owner_only"

    async def test_strict_says_when_no_connection_is_saved(self, session: AsyncSession) -> None:
        with pytest.raises(ToolsetError, match="has no connection configured"):
            await database_tools(
                session, _ctx(OWNER), [_database("draft", configured=False)], mode="legacy", strict=True
            )
        # Someone who may not use it is refused, not told how it is configured.
        with pytest.raises(AssemblyRefusal):
            await database_tools(
                session, _ctx(OTHER), [_database("draft", configured=False)], mode="legacy", strict=True
            )


class _FakeMCPClient:
    def __init__(self) -> None:
        self.connects: dict[str, dict[str, Any]] = {}
        self.fail: set[str] = set()
        self.before_connect: dict[str, Callable[[], Awaitable[None]]] = {}
        self.tool_names: dict[str, list[str]] = {}
        self.dropped: list[str] = []
        self.disconnected = 0

    async def disconnect_all(self) -> None:
        self.disconnected += 1

    async def disconnect(self, name: str) -> None:
        self.dropped.append(name)

    async def _connect(self, name: str, **kwargs: Any) -> list[Any]:
        from fim_one.core.mcp.adapter import MCPToolAdapter

        if name in self.fail:
            raise ConnectionError("refused")
        if name in self.before_connect:
            await self.before_connect[name]()
        self.connects[name] = kwargs

        async def _call(_tool: str, _arguments: dict[str, Any]) -> Any:
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="pong")])

        return [
            MCPToolAdapter(
                server_name=name,
                tool_def={"name": tool, "description": tool},
                call_fn=_call,
            )
            for tool in self.tool_names.get(name, ["echo"])
        ]

    async def connect_stdio(self, *, name: str, **kwargs: Any) -> list[Any]:
        return await self._connect(name, **kwargs)

    async def connect_sse(self, *, name: str, **kwargs: Any) -> list[Any]:
        return await self._connect(name, **kwargs)

    async def connect_streamable_http(self, *, name: str, **kwargs: Any) -> list[Any]:
        return await self._connect(name, **kwargs)


@pytest.fixture()
def mcp_world(session: AsyncSession) -> Iterator[_FakeMCPClient]:
    client = _FakeMCPClient()

    @asynccontextmanager
    async def _create_session() -> AsyncIterator[AsyncSession]:
        yield session

    with (
        patch("fim_one.db.create_session", _create_session),
        patch("fim_one.core.mcp.MCPClient", lambda: client),
        patch("fim_one.core.security.is_stdio_allowed", lambda: True),
    ):
        yield client


class TestMcpTools:
    async def test_no_servers_opens_no_client(self) -> None:
        assert await mcp_tools(_ctx(OTHER), [], mode="progressive") == ([], None)

    async def test_progressive_is_one_tool_over_the_servers_the_user_may_open(
        self, mcp_world: _FakeMCPClient
    ) -> None:
        tools, client = await mcp_tools(
            _ctx(OTHER),
            [
                _mcp_server("open", transport="streamable_http", allow_fallback=True),
                _mcp_server("closed", transport="streamable_http", allow_fallback=False),
            ],
            mode="progressive",
        )

        assert client is mcp_world
        (meta,) = cast(list[Any], tools)
        assert meta.name == "mcp"
        assert meta.server_names == ["open"]
        assert mcp_world.connects == {
            "open": {
                "url": "https://open.example.com/mcp",
                "headers": {"Authorization": "Bearer owner"},
            }
        }

    async def test_legacy_is_one_tool_per_server_tool(
        self, mcp_world: _FakeMCPClient, session: AsyncSession
    ) -> None:
        from fim_one.db.models.user import User

        # A stdio server is started only for an owner who is an administrator.
        session.add(User(id=OWNER, email="owner@x.io", username=OWNER, is_admin=True))
        await session.commit()
        tools, _ = await mcp_tools(
            _ctx(OWNER),
            [_mcp_server("files", transport="stdio", allow_fallback=False)],
            mode="legacy",
        )

        assert [t.name for t in tools] == ["files__echo"]
        assert mcp_world.connects["files"]["env"] == {"KEY": "owner"}
        assert mcp_world.connects["files"]["command"] == "npx"

    async def test_a_server_that_fails_to_connect_does_not_take_the_others_down(
        self, mcp_world: _FakeMCPClient
    ) -> None:
        mcp_world.fail.add("down")

        tools, _ = await mcp_tools(
            _ctx(OWNER),
            [
                _mcp_server("down", transport="sse", allow_fallback=True),
                _mcp_server("up", transport="sse", allow_fallback=True),
            ],
            mode="legacy",
        )

        assert [t.name for t in tools] == ["up__echo"]

    async def test_stdio_is_left_out_when_the_instance_disables_it(
        self, mcp_world: _FakeMCPClient
    ) -> None:
        with patch("fim_one.core.security.is_stdio_allowed", lambda: False):
            tools, client = await mcp_tools(
                _ctx(OWNER),
                [_mcp_server("files", transport="stdio", allow_fallback=True)],
                mode="progressive",
            )

        assert tools == [] and client is mcp_world
        assert mcp_world.connects == {}


class TestMcpToolsStrict:
    """For a caller that needs this one server or nothing (a workflow MCP step)."""

    async def test_a_refusal_is_raised_and_nothing_stays_open(
        self, mcp_world: _FakeMCPClient
    ) -> None:
        with pytest.raises(AssemblyRefusal) as caught:
            await mcp_tools(
                _ctx(OTHER),
                [_mcp_server("closed", transport="sse", allow_fallback=False)],
                mode="legacy",
                strict=True,
            )

        assert caught.value.error_code == "credential_required"
        assert mcp_world.connects == {} and mcp_world.disconnected == 1

    async def test_a_server_that_cannot_be_opened_says_why(self, mcp_world: _FakeMCPClient) -> None:
        with (
            patch("fim_one.core.security.is_stdio_allowed", lambda: False),
            pytest.raises(ToolsetError, match="STDIO MCP transport is disabled"),
        ):
            await mcp_tools(
                _ctx(OWNER),
                [_mcp_server("files", transport="stdio", allow_fallback=True)],
                mode="legacy",
                strict=True,
            )
        with pytest.raises(ToolsetError, match="unsupported or misconfigured transport: carrier"):
            await mcp_tools(
                _ctx(OWNER),
                [_mcp_server("odd", transport="carrier", allow_fallback=True)],
                mode="legacy",
                strict=True,
            )

        assert mcp_world.disconnected == 2

    async def test_a_connection_error_propagates(self, mcp_world: _FakeMCPClient) -> None:
        mcp_world.fail.add("down")

        with pytest.raises(ConnectionError):
            await mcp_tools(
                _ctx(OWNER),
                [_mcp_server("down", transport="sse", allow_fallback=True)],
                mode="legacy",
                strict=True,
            )

        assert mcp_world.disconnected == 1


class TestMcpToolsCloseWhatTheyOpenedWhenCancelled:
    """A workflow step that times out mid-connect is cancelled, not failed.

    The client was closed on ``Exception`` only, so the cancellation left the
    servers already opened connected, with nobody holding the client.
    """

    @pytest.mark.parametrize("strict", [True, False])
    async def test_cancellation_during_connect(
        self, mcp_world: _FakeMCPClient, strict: bool
    ) -> None:
        async def _cancelled() -> None:
            raise asyncio.CancelledError

        mcp_world.before_connect["slow"] = _cancelled

        with pytest.raises(asyncio.CancelledError):
            await mcp_tools(
                _ctx(OWNER),
                [
                    _mcp_server("first", transport="streamable_http", allow_fallback=True),
                    _mcp_server("slow", transport="sse", allow_fallback=True),
                ],
                mode="legacy",
                strict=strict,
            )

        assert "first" in mcp_world.connects
        assert mcp_world.disconnected == 1

    async def test_a_node_timeout_closes_the_client(self, mcp_world: _FakeMCPClient) -> None:
        async def _hangs() -> None:
            await asyncio.sleep(30)

        mcp_world.before_connect["slow"] = _hangs

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                mcp_tools(
                    _ctx(OWNER),
                    [_mcp_server("slow", transport="sse", allow_fallback=True)],
                    mode="legacy",
                    strict=True,
                ),
                timeout=0.05,
            )

        assert mcp_world.disconnected == 1


class TestMcpToolNameCollisions:
    """One clashing name leaves that tool out; it does not cost the rest.

    Registering the returned tools raised on the first duplicate name, which
    ended the chat turn with the client still open and nobody holding it.
    """

    async def test_a_name_the_caller_already_has_is_left_out(
        self, mcp_world: _FakeMCPClient
    ) -> None:
        mcp_world.tool_names["files"] = ["echo", "read"]

        tools, client = await mcp_tools(
            _ctx(OWNER),
            [_mcp_server("files", transport="sse", allow_fallback=True)],
            mode="legacy",
            taken=["files__echo", "web_search"],
        )

        assert [t.name for t in tools] == ["files__read"]
        assert client is mcp_world and mcp_world.dropped == []

    async def test_two_servers_whose_tools_sanitise_to_one_name_keep_the_first(
        self, mcp_world: _FakeMCPClient
    ) -> None:
        tools, _ = await mcp_tools(
            _ctx(OWNER),
            [
                _mcp_server("a.b", transport="sse", allow_fallback=True),
                _mcp_server("a_b", transport="sse", allow_fallback=True),
            ],
            mode="legacy",
        )

        assert [t.name for t in tools] == ["a_b__echo"]
        assert cast(Any, tools[0])._server_name == "a.b"
        # The second server offers nothing that can be registered.
        assert mcp_world.dropped == ["a_b"]

    @pytest.mark.parametrize("mode", ["legacy", "progressive"])
    async def test_two_servers_of_the_same_name_keep_the_first(
        self, mcp_world: _FakeMCPClient, mode: Any
    ) -> None:
        first = _mcp_server("dup", transport="sse", allow_fallback=True)
        second = _mcp_server("dup", transport="streamable_http", allow_fallback=True)
        second.id = "dup-2"

        tools, _ = await mcp_tools(_ctx(OWNER), [first, second], mode=mode)

        assert len(tools) == 1
        # Only the first was connected: the second would have replaced it.
        assert mcp_world.connects["dup"]["url"] == first.url

    async def test_progressive_gives_way_to_a_registered_tool_called_mcp(
        self, mcp_world: _FakeMCPClient
    ) -> None:
        tools, client = await mcp_tools(
            _ctx(OWNER),
            [_mcp_server("files", transport="sse", allow_fallback=True)],
            mode="progressive",
            taken=["mcp"],
        )

        assert tools == [] and client is mcp_world
        assert mcp_world.dropped == ["files"]

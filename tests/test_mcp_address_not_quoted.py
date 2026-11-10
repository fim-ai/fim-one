"""Logs and error texts do not quote what an MCP server's address authenticates with.

A connection error names the URL it failed for, query string and user-info
included, and the client logged the URL and the launch arguments it was
about to open. Someone other than the owner is never shown a server's
address; the logs and the errors they are shown must not show it either.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.assembly.mcp_address import (
    error_without_url_secrets,
    traceback_without_url_secrets,
)
from fim_one.core.mcp import MCPClient
from fim_one.db.base import Base
from fim_one.web.exceptions import AppError

from .assembly import matrix as m

TOKEN = "owner-url-token"  # the query string of ``m.SECRET_URL``
KEY = "sk-owner-0123456789"  # among ``m.SECRET_ARGS``
BARE_URL = "https://mcp.example.test/mcp"


def _connection_error(url: str) -> Exception:
    """An error the way the HTTP client words it, chained like a real one."""
    try:
        try:
            raise OSError(f"connect to {url} refused")
        except OSError as cause:
            raise RuntimeError(f"Client error '401 Unauthorized' for url '{url}'") from cause
    except RuntimeError as exc:
        return exc


class _FailingClient:
    """An MCP client whose every connection fails, quoting the URL it was given."""

    async def connect_stdio(self, **_kwargs: Any) -> list[Any]:
        raise _connection_error(m.SECRET_URL)

    async def connect_sse(self, *, url: str, **_kwargs: Any) -> list[Any]:
        raise _connection_error(url)

    async def connect_streamable_http(self, *, url: str, **_kwargs: Any) -> list[Any]:
        raise _connection_error(url)

    async def disconnect(self, _name: str) -> None:
        return None

    async def disconnect_all(self) -> None:
        return None


@pytest.fixture()
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'quoted.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
def _nothing_remembered() -> Any:
    from fim_one.web.services.mcp_tool_listing import listings

    listings.clear()
    yield
    listings.clear()


async def _lent_scene(factory: async_sessionmaker[AsyncSession], role: str = "subscriber") -> Any:
    """Fallback on: the subscriber connects to the owner's address."""
    return await m.seed_scene(
        factory, m.SceneKey(role=role, allow_fallback=True, is_active=True, has_personal=False)
    )


class TestTheHelpers:
    def test_an_error_is_worded_without_the_secret_parts(self) -> None:
        text = error_without_url_secrets(_connection_error(m.SECRET_URL))
        assert text == f"RuntimeError: Client error '401 Unauthorized' for url '{BARE_URL}'"

    def test_a_traceback_keeps_the_chain_without_them(self) -> None:
        text = traceback_without_url_secrets(_connection_error(m.SECRET_URL))
        assert "OSError" in text and "RuntimeError" in text and "Traceback" in text
        assert text.count(BARE_URL) == 2
        assert TOKEN not in text


class TestTheClientsOwnLog:
    """``MCPClient`` logged the URL and the launch arguments as given."""

    async def _connect(self, how: str, caplog: pytest.LogCaptureFixture) -> str:
        def _unreachable(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("not reached in this test")

        url = "https://user:pw-in-url@mcp.example.test:8443/mcp?token=tok-in-url#frag-in-url"
        client = MCPClient()
        with (
            caplog.at_level(logging.INFO, logger="fim_one.core.mcp.client"),
            patch("fim_one.core.mcp.client._validate_url", lambda _url: None),
            patch("fim_one.core.security.validate_stdio_command", lambda _command: None),
            patch("mcp.client.stdio.stdio_client", _unreachable),
            patch("mcp.client.sse.sse_client", _unreachable),
            patch("mcp.client.streamable_http.streamable_http_client", _unreachable),
            pytest.raises(RuntimeError, match="not reached"),
        ):
            if how == "stdio":
                await client.connect_stdio(name="srv", command="npx", args=list(m.SECRET_ARGS))
            elif how == "sse":
                await client.connect_sse(name="srv", url=url)
            else:
                await client.connect_streamable_http(name="srv", url=url)
        await client.disconnect_all()
        assert "Connecting to MCP server 'srv'" in caplog.text
        return caplog.text

    @pytest.mark.parametrize("how", ["sse", "streamable_http"])
    async def test_the_url_is_logged_without_userinfo_query_or_fragment(
        self, how: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        text = await self._connect(how, caplog)
        assert "https://mcp.example.test:8443/mcp" in text
        for secret in ("pw-in-url", "tok-in-url", "frag-in-url", "user:"):
            assert secret not in text

    async def test_launch_arguments_are_counted_not_quoted(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        text = await self._connect("stdio", caplog)
        assert "npx (4 argument(s))" in text
        assert KEY not in text and "--api-key" not in text


class TestAConnectionThatFails:
    async def test_chat_logs_it_without_the_urls_secret(
        self, factory: async_sessionmaker[AsyncSession], caplog: pytest.LogCaptureFixture
    ) -> None:
        from fim_one.web.api import chat

        scene = await _lent_scene(factory)
        with (
            m.outside_world(factory),
            patch("fim_one.core.mcp.MCPClient", _FailingClient),
            caplog.at_level(logging.INFO),
        ):
            tools = await chat._resolve_tools(None, None, user_id=scene.actor)
            await chat._connect_pending_mcp_servers(tools, None)

        assert tools.get("mcp") is None
        # The failure is still logged, with where it went and the chain.
        assert (
            f"Failed to connect user MCP server '{scene.names['mcp_http_secret']}'" in caplog.text
        )
        assert BARE_URL in caplog.text and "OSError" in caplog.text
        assert TOKEN not in caplog.text

    async def test_a_workflow_step_fails_without_quoting_it(
        self, factory: async_sessionmaker[AsyncSession], caplog: pytest.LogCaptureFixture
    ) -> None:
        from fim_one.core.workflow.nodes import MCPExecutor
        from fim_one.core.workflow.types import NodeStatus, NodeType
        from fim_one.core.workflow.variable_store import VariableStore

        scene = await _lent_scene(factory)
        node = m._node(
            NodeType.MCP,
            {"server_id": scene.ids["mcp_http_secret"], "tool_name": "echo", "parameters": {}},
        )
        with (
            m.outside_world(factory),
            patch("fim_one.core.mcp.MCPClient", _FailingClient),
            caplog.at_level(logging.INFO),
        ):
            result = await MCPExecutor().execute(node, VariableStore(), m._context(scene))

        assert result.status == NodeStatus.FAILED
        # The runner is told what failed and where, not what authenticates.
        assert result.error == (
            f"MCP tool error: Client error '401 Unauthorized' for url '{BARE_URL}'"
        )
        assert "MCP node n1 failed" in caplog.text
        assert TOKEN not in caplog.text

    async def test_the_step_editors_listing_logs_it_without_the_secret(
        self, factory: async_sessionmaker[AsyncSession], caplog: pytest.LogCaptureFixture
    ) -> None:
        from fim_one.web.api import workflows as api

        scene = await _lent_scene(factory)
        with (
            m.outside_world(factory),
            patch("fim_one.core.mcp.MCPClient", _FailingClient),
            caplog.at_level(logging.INFO),
            pytest.raises(AppError) as failed,
        ):
            async with factory() as db:
                await api.list_step_mcp_tools(
                    scene.ids["mcp_http_secret"],
                    current_user=SimpleNamespace(id=scene.actor),
                    db=db,
                )

        assert failed.value.error_code == "mcp_tools_unavailable"
        assert TOKEN not in str(failed.value.detail)
        assert "Listing the tools of MCP server" in caplog.text and BARE_URL in caplog.text
        assert TOKEN not in caplog.text

    async def test_the_owners_connection_test_shows_the_owner_and_not_the_log(
        self, factory: async_sessionmaker[AsyncSession], caplog: pytest.LogCaptureFixture
    ) -> None:
        from fim_one.web.api.mcp_servers import test_mcp_server as run_connection_test

        scene = await _lent_scene(factory, role="owner")
        with (
            patch("fim_one.core.mcp.MCPClient", _FailingClient),
            caplog.at_level(logging.INFO),
        ):
            async with factory() as db:
                response = await run_connection_test(
                    scene.ids["mcp_http_secret"],
                    current_user=SimpleNamespace(id=scene.actor),
                    db=db,
                )

        # The owner typed the URL and is shown the error as it is.
        assert response.data["ok"] is False and TOKEN in response.data["error"]
        assert "MCP test failed for server" in caplog.text and BARE_URL in caplog.text
        assert TOKEN not in caplog.text


class TestACallThatFails:
    async def test_the_mcp_tool_answers_without_the_urls_secret(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from fim_one.core.mcp import build_mcp_meta_tool
        from fim_one.core.mcp.adapter import MCPToolAdapter

        async def _call(_tool: str, _arguments: dict[str, Any]) -> Any:
            raise _connection_error(m.SECRET_URL)

        adapter = MCPToolAdapter(
            server_name="srv",
            tool_def={"name": "echo", "description": "echo", "inputSchema": {"type": "object"}},
            call_fn=_call,
        )
        tool = build_mcp_meta_tool({"srv": [adapter]})
        with caplog.at_level(logging.INFO):
            answer = await tool.run(subcommand="call", server="srv", tool="echo", parameters={})

        assert answer == (
            "[Error] MCP call failed: RuntimeError: "
            f"Client error '401 Unauthorized' for url '{BARE_URL}'"
        )
        assert "MCPServerMetaTool call failed" in caplog.text
        assert TOKEN not in caplog.text

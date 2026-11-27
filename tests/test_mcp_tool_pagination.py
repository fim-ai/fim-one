"""A server that pages ``tools/list`` has every page loaded.

The MCP spec lets a server answer ``tools/list`` with part of its tools and a
``nextCursor``. The client read the first answer only, so the rest of the
server's tools never reached the agent, with nothing logged.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("mcp")

import mcp
from mcp.client import sse, stdio, streamable_http
from mcp.types import ListToolsResult, PaginatedRequestParams, Tool

from fim_one.core.mcp import MCPClient
from fim_one.core.mcp import client as client_module


def _page(*names: str, next_cursor: str | None = None) -> ListToolsResult:
    return ListToolsResult(
        tools=[Tool(name=name, input_schema={"type": "object"}) for name in names],
        next_cursor=next_cursor,
    )


class _Transport:
    async def __aenter__(self) -> tuple[None, None]:
        return None, None

    async def __aexit__(self, *exc: object) -> None:
        return None


def _serve(monkeypatch: pytest.MonkeyPatch, pages: dict[str | None, ListToolsResult]) -> None:
    """Answer ``tools/list`` from *pages*, keyed by the cursor asked for."""

    class _Session:
        def __init__(self, read: Any, write: Any) -> None:
            self.asked: list[str | None] = []

        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def initialize(self) -> None:
            return None

        async def list_tools(
            self, *, params: PaginatedRequestParams | None = None
        ) -> ListToolsResult:
            cursor = params.cursor if params else None
            assert cursor not in self.asked, "the same page was asked for twice"
            self.asked.append(cursor)
            return pages[cursor]

        async def call_tool(self, *args: object, **kwargs: object) -> None:
            return None

    monkeypatch.setattr(mcp, "ClientSession", _Session)
    for module, name in (
        (stdio, "stdio_client"),
        (sse, "sse_client"),
        (streamable_http, "streamable_http_client"),
    ):
        monkeypatch.setattr(module, name, lambda *args, **kwargs: _Transport())
    monkeypatch.setattr(client_module, "_validate_url", lambda url: None)


async def _connect(client: MCPClient, transport: str) -> list[str]:
    if transport == "stdio":
        tools = await client.connect_stdio("srv", "npx")
    elif transport == "sse":
        tools = await client.connect_sse("srv", "https://mcp.example.com/sse")
    else:
        tools = await client.connect_streamable_http("srv", "https://mcp.example.com/mcp")
    return [tool.name for tool in tools]


@pytest.mark.parametrize("transport", ["stdio", "sse", "streamable_http"])
async def test_every_page_of_tools_is_loaded(
    monkeypatch: pytest.MonkeyPatch, transport: str
) -> None:
    # An empty string is a cursor too: only an absent one ends the listing.
    _serve(
        monkeypatch,
        {
            None: _page("a", "b", next_cursor="page-2"),
            "page-2": _page("c", next_cursor=""),
            "": _page("d"),
        },
    )

    async with MCPClient() as client:
        names = await _connect(client, transport)

    assert names == ["srv__a", "srv__b", "srv__c", "srv__d"]


async def test_a_repeated_cursor_ends_the_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(
        monkeypatch,
        {
            None: _page("a", next_cursor="again"),
            "again": _page("a", "b", next_cursor="again"),
        },
    )

    async with MCPClient() as client:
        names = await _connect(client, "sse")

    assert names == ["srv__a", "srv__b"]

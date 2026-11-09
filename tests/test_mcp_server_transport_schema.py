"""An MCP server can only be saved with a transport the client can open."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fim_one.web.schemas.mcp_server import MCPServerCreate, MCPServerUpdate


@pytest.mark.parametrize("transport", ["http", "SSE", "streamable-http", ""])
def test_create_refuses_an_unknown_transport(transport: str) -> None:
    with pytest.raises(ValidationError):
        MCPServerCreate(name="s", transport=transport, url="https://example.com/mcp")


@pytest.mark.parametrize("transport", ["http", "SSE", "streamable-http", ""])
def test_update_refuses_an_unknown_transport(transport: str) -> None:
    with pytest.raises(ValidationError):
        MCPServerUpdate(transport=transport)


def test_the_three_known_transports_are_accepted() -> None:
    assert MCPServerCreate(name="s", transport="stdio", command="npx").transport == "stdio"
    for transport in ("sse", "streamable_http"):
        created = MCPServerCreate(name="s", transport=transport, url="https://example.com/mcp")
        assert created.transport == transport
    assert MCPServerUpdate(transport="sse").transport == "sse"
    assert MCPServerUpdate().transport is None

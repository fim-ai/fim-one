"""Chat's MCP connect step hands back its client whatever the tools are called.

A tool name that was already registered made the step raise after the
servers were connected: the chat turn ended with an internal error and the
client, not yet handed to the caller, was never disconnected. Before the
step moved into the assembly layer the clashing server was skipped with a
warning.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fim_one.core.assembly.principal import CallContext, provisional_principal
from fim_one.core.mcp.adapter import MCPToolAdapter
from fim_one.core.tool.registry import ToolRegistry

OWNER = "owner-1"


def _server(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"srv-{name}",
        name=name,
        transport="streamable_http",
        command=None,
        args=[],
        env=None,
        url=f"https://{name}.example.com/mcp",
        working_dir=None,
        headers={"Authorization": "Bearer owner"},
        is_active=True,
        allow_fallback=True,
        user_id=OWNER,
    )


def _adapter(server: str, tool: str) -> MCPToolAdapter:
    async def _call(_name: str, _arguments: dict[str, Any]) -> Any:
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")])

    return MCPToolAdapter(server_name=server, tool_def={"name": tool}, call_fn=_call)


def _no_credential_session() -> AsyncMock:
    result = MagicMock()
    result.all.return_value = []
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


async def _connect(registry: ToolRegistry, tools_by_server: dict[str, list[str]]) -> Any:
    from fim_one.web.api.chat import _connect_pending_mcp_servers

    client = AsyncMock()

    async def _open(*, name: str, **_: Any) -> list[Any]:
        return [_adapter(name, tool) for tool in tools_by_server[name]]

    client.connect_streamable_http = AsyncMock(side_effect=_open)
    pending: Any = registry  # chat hangs the servers to connect on the registry
    pending._pending_mcp_servers = [_server(name) for name in tools_by_server]
    pending._mcp_call_context = CallContext(
        principal=provisional_principal(OWNER), binding=None, entry="chat"
    )
    with (
        patch("fim_one.db.create_session", return_value=_no_credential_session()),
        patch("fim_one.core.mcp.MCPClient", return_value=client),
    ):
        returned = await _connect_pending_mcp_servers(
            registry, {"model_config_json": {"mcp_tool_mode": "legacy"}}
        )
    assert returned is client, "the caller must get the client to disconnect"
    return client


async def test_a_tool_named_like_a_registered_one_is_skipped_and_the_turn_goes_on() -> None:
    registry = ToolRegistry()
    registry.register(_adapter("files", "echo"))  # stands for any tool already there

    await _connect(registry, {"files": ["echo", "read"], "search": ["query"]})

    assert sorted(t.name for t in registry.list_tools()) == [
        "files__echo",
        "files__read",
        "search__query",
    ]


async def test_two_servers_whose_tool_names_collide_keep_the_first() -> None:
    registry = ToolRegistry()

    await _connect(registry, {"a.b": ["echo"], "a_b": ["echo", "other"]})

    assert sorted(t.name for t in registry.list_tools()) == ["a_b__echo", "a_b__other"]
    kept: Any = registry.get("a_b__echo")
    assert kept._server_name == "a.b"

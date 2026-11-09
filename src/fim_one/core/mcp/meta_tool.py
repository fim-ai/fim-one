"""MCPServerMetaTool — single tool proxy for progressive MCP tool disclosure.

Instead of registering every MCP tool as a separate tool (a single MCP server
can expose 20-90 tools, consuming massive context), the MCPServerMetaTool
presents a compact stub listing (~30 tokens per server) and exposes two
subcommands:

    discover <server>                        — returns full tool schemas on demand
    call <server> <tool> {"param": "value"}  — invokes an MCP tool

This reduces prompt size dramatically while keeping full functionality.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from fim_one.core.tool.base import BaseTool

from .adapter import MCPToolAdapter

logger = logging.getLogger(__name__)

_DISCOVER_INDENT = int(os.getenv("MCP_DISCOVER_INDENT", "2"))


@dataclass(frozen=True)
class MCPToolStub:
    """Lightweight MCP tool metadata stored for discover/call routing."""

    name: str
    description: str | None
    input_schema: dict[str, Any]  # JSON Schema for parameters


@dataclass(frozen=True)
class MCPServerStub:
    """Lightweight MCP server summary for the system prompt."""

    name: str  # server display name
    description: str | None
    tool_count: int
    tools: list[MCPToolStub] = field(default_factory=list)


class MCPServerMetaTool(BaseTool):
    """A single tool that proxies all MCP server operations.

    System prompt sees only lightweight stubs::

        mcp("discover", "github")
        mcp("call", "github", "create_issue", {"title": "Bug report"})

    Subcommands:
        discover <server_name> — returns full tool schemas
        call <server_name> <tool_name> <parameters> — invokes an MCP tool
    """

    def __init__(
        self,
        stubs: list[MCPServerStub],
        *,
        adapters: dict[str, dict[str, MCPToolAdapter]],
        on_call_complete: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self._stubs: dict[str, MCPServerStub] = {s.name: s for s in stubs}
        # server_name -> {tool_original_name -> MCPToolAdapter}
        self._adapters = adapters
        self._on_call_complete = on_call_complete

    # ------------------------------------------------------------------
    # BaseTool protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "mcp"

    @property
    def display_name(self) -> str:
        return "MCP"

    @property
    def description(self) -> str:
        lines = ["Interact with MCP servers. Available servers:"]
        for stub in self._stubs.values():
            desc = stub.description or stub.name
            tool_names = [t.name for t in stub.tools]
            if tool_names:
                names_str = ", ".join(tool_names)
                lines.append(
                    f"  - {stub.name}: {desc} ({stub.tool_count} tools: {names_str})"
                )
            else:
                lines.append(
                    f"  - {stub.name}: {desc} ({stub.tool_count} tools)"
                )
        lines.append("")
        lines.append("Subcommands:")
        lines.append(
            "  discover <server> — list tools with full parameter schemas"
        )
        lines.append(
            '  call <server> <tool> {"param": "value"} — invoke an MCP tool'
        )
        lines.append("")
        lines.append(
            "IMPORTANT: Only use tool names listed above. "
            "Call 'discover' first if you need parameter details."
        )
        return "\n".join(lines)

    @property
    def category(self) -> str:
        return "mcp"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        # Filter out empty names (Gemini rejects empty enum arrays and empty
        # strings inside enum arrays).
        server_names = sorted(n for n in self._stubs.keys() if n)
        server_prop: dict[str, Any] = {
            "type": "string",
            "description": "MCP server name",
        }
        if server_names:
            server_prop["enum"] = server_names
        return {
            "type": "object",
            "properties": {
                "subcommand": {
                    "type": "string",
                    "enum": ["discover", "call"],
                    "description": (
                        "discover: list tools for a server. "
                        "call: invoke a tool."
                    ),
                },
                "server": server_prop,
                "tool": {
                    "type": "string",
                    "description": "Tool name (required for call)",
                },
                "parameters": {
                    "type": "object",
                    "description": "Tool parameters as JSON (for call)",
                },
            },
            "required": ["subcommand", "server"],
        }

    async def run(self, **kwargs: Any) -> str:
        """Route to discover or call subcommand."""
        subcommand = kwargs.get("subcommand", "")
        server = kwargs.get("server", "")
        tool = kwargs.get("tool", "")
        parameters = kwargs.get("parameters") or {}

        if not subcommand:
            return "Error: 'subcommand' is required. Use 'discover' or 'call'."
        if not server:
            return "Error: 'server' is required."

        if subcommand == "discover":
            return self._discover(server)
        elif subcommand == "call":
            return await self._call(server, tool, parameters)
        else:
            return (
                f"Unknown subcommand: '{subcommand}'. "
                "Use 'discover' or 'call'."
            )

    # ------------------------------------------------------------------
    # Subcommand implementations
    # ------------------------------------------------------------------

    def _discover(self, server_name: str) -> str:
        """Return formatted tool list with full parameter schemas."""
        stub = self._stubs.get(server_name)
        if stub is None:
            available = ", ".join(sorted(self._stubs.keys()))
            return (
                f"Unknown server: '{server_name}'. "
                f"Available servers: {available}"
            )

        if not stub.tools:
            return f"Server '{server_name}' has no tools."

        lines = [
            f"Server: {stub.name}",
            f"Description: {stub.description or stub.name}",
            f"Tools ({len(stub.tools)}):",
            "",
        ]

        for tool_stub in stub.tools:
            lines.append(f"  {tool_stub.name}:")
            if tool_stub.description:
                lines.append(f"    description: {tool_stub.description}")
            if tool_stub.input_schema and tool_stub.input_schema.get("properties"):
                schema_str = json.dumps(
                    tool_stub.input_schema,
                    ensure_ascii=False,
                    indent=_DISCOVER_INDENT,
                )
                lines.append(f"    parameters: {schema_str}")
            else:
                lines.append("    parameters: (none)")
            lines.append("")

        return "\n".join(lines)

    async def _call(
        self,
        server_name: str,
        tool_name: str,
        parameters: dict[str, Any],
    ) -> str:
        """Invoke an MCP tool by delegating to the stored MCPToolAdapter."""
        stub = self._stubs.get(server_name)
        if stub is None:
            available = ", ".join(sorted(self._stubs.keys()))
            return (
                f"Unknown server: '{server_name}'. "
                f"Available servers: {available}"
            )

        if not tool_name:
            tool_names = [t.name for t in stub.tools]
            return (
                f"Error: 'tool' is required for call. "
                f"Available tools for '{server_name}': {', '.join(tool_names)}"
            )

        # Look up the adapter for this server + tool
        server_adapters = self._adapters.get(server_name, {})
        adapter = server_adapters.get(tool_name)

        if adapter is None:
            tool_names = [t.name for t in stub.tools]
            return (
                f"Unknown tool: '{tool_name}' for server '{server_name}'. "
                f"Available tools: {', '.join(tool_names)}"
            )

        try:
            result = await adapter.run(**parameters)
            return result
        except Exception as exc:
            logger.warning(
                "MCPServerMetaTool call failed: server=%s tool=%s",
                server_name,
                tool_name,
                exc_info=True,
            )
            return f"[Error] MCP call failed: {type(exc).__name__}: {exc}"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def server_names(self) -> list[str]:
        """Return sorted list of available server names."""
        return sorted(self._stubs.keys())

    @property
    def stub_count(self) -> int:
        """Return number of registered server stubs."""
        return len(self._stubs)


# ---------------------------------------------------------------------------
# Factory helper — builds an MCPServerMetaTool from MCPToolAdapters
# ---------------------------------------------------------------------------


def build_mcp_meta_tool(
    servers: dict[str, list[MCPToolAdapter]],
    on_call_complete: Callable[..., Awaitable[None]] | None = None,
) -> MCPServerMetaTool:
    """Build an MCPServerMetaTool from a mapping of server names to tool adapters.

    This is the primary integration point called from ``chat.py`` when
    ``MCP_TOOL_MODE=progressive``.

    Args:
        servers: Mapping of server_name -> list of MCPToolAdapter instances
            (as returned by MCPClient.connect_*).
        on_call_complete: Optional async callback for call logging.

    Returns:
        A fully configured MCPServerMetaTool instance.
    """
    stubs: list[MCPServerStub] = []
    adapters: dict[str, dict[str, MCPToolAdapter]] = {}

    for server_name, tool_adapters in servers.items():
        tool_stubs: list[MCPToolStub] = []
        adapter_map: dict[str, MCPToolAdapter] = {}

        for adapter in tool_adapters:
            # Use the original tool name (not the prefixed one) for cleaner UX
            original_name = adapter._original_name
            tool_stubs.append(
                MCPToolStub(
                    name=original_name,
                    description=adapter._description or None,
                    input_schema=adapter._schema,
                )
            )
            adapter_map[original_name] = adapter

        stub = MCPServerStub(
            name=server_name,
            description=None,  # MCP protocol doesn't expose server descriptions
            tool_count=len(tool_stubs),
            tools=tool_stubs,
        )
        stubs.append(stub)
        adapters[server_name] = adapter_map

    return MCPServerMetaTool(
        stubs=stubs,
        adapters=adapters,
        on_call_complete=on_call_complete,
    )


def get_mcp_tool_mode(agent_cfg: dict[str, Any] | None = None) -> Literal["progressive", "legacy"]:
    """Determine the MCP tool mode from environment or agent config.

    Priority:
        1. Agent-level ``model_config_json.mcp_tool_mode``
        2. Environment variable ``MCP_TOOL_MODE``
        3. Default: ``"progressive"``

    Returns:
        ``"progressive"`` or ``"legacy"``
    """
    # Check agent-level config first
    if agent_cfg:
        model_cfg = agent_cfg.get("model_config_json") or {}
        if isinstance(model_cfg, dict):
            agent_mode = model_cfg.get("mcp_tool_mode")
            if isinstance(agent_mode, str) and agent_mode in ("progressive", "legacy"):
                return "legacy" if agent_mode == "legacy" else "progressive"

    # Fall back to environment variable
    env_mode = os.environ.get("MCP_TOOL_MODE", "progressive").lower()
    if env_mode in ("progressive", "legacy"):
        return "legacy" if env_mode == "legacy" else "progressive"

    return "progressive"

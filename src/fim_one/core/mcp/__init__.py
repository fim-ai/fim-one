"""MCP (Model Context Protocol) client integration.

Provides :class:`MCPClient` for connecting to external MCP servers and
:class:`MCPToolAdapter` for wrapping MCP tools into the FIM One ``Tool``
protocol.  :class:`MCPServerMetaTool` consolidates all MCP tools behind a
single tool with ``discover``/``call`` subcommands (progressive disclosure).

The ``mcp`` package is an optional dependency — import errors are deferred
so the rest of the framework works without it installed.
"""

from __future__ import annotations

from .adapter import MCPToolAdapter
from .client import MCPClient, MCPConnectionError
from .meta_tool import (
    MCPServerMetaTool,
    MCPServerStub,
    MCPToolStub,
    build_mcp_meta_tool,
    get_mcp_tool_mode,
)

__all__ = [
    "MCPClient",
    "MCPConnectionError",
    "MCPServerMetaTool",
    "MCPServerStub",
    "MCPToolAdapter",
    "MCPToolStub",
    "build_mcp_meta_tool",
    "get_mcp_tool_mode",
]

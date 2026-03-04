"""MCP client that manages persistent connections to MCP servers."""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from fim_agent.core.tool.base import Tool

from .adapter import MCPToolAdapter

logger = logging.getLogger(__name__)


class MCPClient:
    """Manages connections to one or more MCP servers and provides tool adapters.

    Uses :class:`contextlib.AsyncExitStack` to keep server processes and
    sessions alive for the lifetime of the client.  Call :meth:`disconnect_all`
    (or use as an async context manager) to clean up.

    Example
    -------
    ::

        client = MCPClient()
        tools = await client.connect_stdio(
            name="filesystem",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        )
        # ... use tools ...
        await client.disconnect_all()
    """

    def __init__(self) -> None:
        self._exit_stack = contextlib.AsyncExitStack()
        self._sessions: dict[str, Any] = {}  # name -> ClientSession

    # ------------------------------------------------------------------
    # Async context-manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> MCPClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect_all()

    # ------------------------------------------------------------------
    # Connection methods
    # ------------------------------------------------------------------

    async def connect_stdio(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        working_dir: str | None = None,
    ) -> list[Tool]:
        """Connect to an MCP server via stdio transport.

        Parameters
        ----------
        name:
            Unique name for this server (used as tool-name prefix).
        command:
            Command to launch the server (e.g. ``"npx"``, ``"uvx"``).
        args:
            Command arguments.
        env:
            Optional environment variables for the server process.

        Returns
        -------
        list[Tool]
            List of :class:`MCPToolAdapter` instances wrapping the server's tools.

        Raises
        ------
        ImportError
            If the ``mcp`` package is not installed.
        """
        # Deferred import — mcp is an optional dependency.
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise ImportError(
                "The 'mcp' package is required for MCP integration. "
                "Install it with: uv sync --extra mcp"
            ) from exc

        server_params = StdioServerParameters(
            command=command,
            args=args or [],
            env=env,
            cwd=working_dir,
        )

        logger.info("Connecting to MCP server %r: %s %s", name, command, args or [])

        # Enter the stdio_client and ClientSession context managers via the
        # exit stack so they stay alive until disconnect_all() is called.
        read, write = await self._exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        session: ClientSession = await self._exit_stack.enter_async_context(
            ClientSession(read, write)
        )
        await session.initialize()
        self._sessions[name] = session

        # Discover tools exposed by the server.
        tools_result = await session.list_tools()

        adapters: list[Tool] = []
        for tool in tools_result.tools:
            adapter = MCPToolAdapter(
                server_name=name,
                tool_def={
                    "name": tool.name,
                    "description": tool.description or "",
                    "inputSchema": tool.inputSchema,
                },
                call_fn=session.call_tool,
            )
            adapters.append(adapter)

        tool_names = [t.name for t in adapters]
        logger.info(
            "MCP server %r connected — discovered %d tool(s): %s",
            name,
            len(adapters),
            tool_names,
        )
        return adapters

    async def connect_sse(
        self,
        name: str,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> list[Tool]:
        """Connect to an MCP server via SSE transport.

        Parameters
        ----------
        name:
            Unique name for this server (used as tool-name prefix).
        url:
            SSE endpoint URL of the MCP server.
        headers:
            Optional HTTP headers for the SSE connection.

        Returns
        -------
        list[Tool]
            List of :class:`MCPToolAdapter` instances wrapping the server's tools.

        Raises
        ------
        ImportError
            If the ``mcp`` package is not installed.
        """
        try:
            from mcp import ClientSession
            from mcp.client.sse import sse_client
        except ImportError as exc:
            raise ImportError(
                "The 'mcp' package is required for MCP integration. "
                "Install it with: uv sync --extra mcp"
            ) from exc

        logger.info("Connecting to MCP server %r via SSE: %s", name, url)

        read, write = await self._exit_stack.enter_async_context(
            sse_client(url, headers=headers or {})
        )
        session: ClientSession = await self._exit_stack.enter_async_context(
            ClientSession(read, write)
        )
        await session.initialize()
        self._sessions[name] = session

        tools_result = await session.list_tools()

        adapters: list[Tool] = []
        for tool in tools_result.tools:
            adapter = MCPToolAdapter(
                server_name=name,
                tool_def={
                    "name": tool.name,
                    "description": tool.description or "",
                    "inputSchema": tool.inputSchema,
                },
                call_fn=session.call_tool,
            )
            adapters.append(adapter)

        tool_names = [t.name for t in adapters]
        logger.info(
            "MCP server %r (SSE) connected — discovered %d tool(s): %s",
            name,
            len(adapters),
            tool_names,
        )
        return adapters

    async def connect_streamable_http(
        self,
        name: str,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> list[Tool]:
        """Connect to an MCP server via Streamable HTTP transport.

        Parameters
        ----------
        name:
            Unique name for this server (used as tool-name prefix).
        url:
            Streamable HTTP endpoint URL of the MCP server.
        headers:
            Optional HTTP headers for the connection.

        Returns
        -------
        list[Tool]
            List of :class:`MCPToolAdapter` instances wrapping the server's tools.

        Raises
        ------
        ImportError
            If the ``mcp`` package is not installed.
        """
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError as exc:
            raise ImportError(
                "The 'mcp' package is required for MCP integration. "
                "Install it with: uv sync --extra mcp"
            ) from exc

        logger.info("Connecting to MCP server %r via Streamable HTTP: %s", name, url)

        read, write, _ = await self._exit_stack.enter_async_context(
            streamablehttp_client(url, headers=headers or {})
        )
        session: ClientSession = await self._exit_stack.enter_async_context(
            ClientSession(read, write)
        )
        await session.initialize()
        self._sessions[name] = session

        tools_result = await session.list_tools()

        adapters: list[Tool] = []
        for tool in tools_result.tools:
            adapter = MCPToolAdapter(
                server_name=name,
                tool_def={
                    "name": tool.name,
                    "description": tool.description or "",
                    "inputSchema": tool.inputSchema,
                },
                call_fn=session.call_tool,
            )
            adapters.append(adapter)

        tool_names = [t.name for t in adapters]
        logger.info(
            "MCP server %r (Streamable HTTP) connected — discovered %d tool(s): %s",
            name,
            len(adapters),
            tool_names,
        )
        return adapters

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def disconnect(self, name: str) -> None:
        """Remove a server from the session registry.

        .. note::

            :class:`contextlib.AsyncExitStack` does not support removing
            individual async contexts.  The actual transport cleanup happens
            when :meth:`disconnect_all` is called.
        """
        if name in self._sessions:
            del self._sessions[name]
            logger.info(
                "Removed MCP server %r from session registry "
                "(will cleanup on disconnect_all)",
                name,
            )

    async def disconnect_all(self) -> None:
        """Disconnect from all connected MCP servers and clean up resources."""
        server_names = list(self._sessions.keys())
        await self._exit_stack.aclose()
        self._sessions.clear()
        if server_names:
            logger.info("Disconnected from MCP servers: %s", server_names)

    @property
    def connected_servers(self) -> list[str]:
        """Return names of currently connected MCP servers."""
        return list(self._sessions.keys())

    def __repr__(self) -> str:
        return f"MCPClient(servers={self.connected_servers!r})"

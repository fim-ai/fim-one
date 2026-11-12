"""MCP client that manages persistent connections to MCP servers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from fim_one.core.security import validate_url as _validate_url
from fim_one.core.security.log_secrets import install_url_secret_log_filter
from fim_one.core.tool.base import Tool

from .adapter import MCPToolAdapter

logger = logging.getLogger(__name__)

# The HTTP client under the SDK logs each request with its full URL, and
# the SDK logs failed connections with theirs. Whatever process opens an
# MCP server (a web worker, a scheduler, a script) has them stripped.
install_url_secret_log_filter()


def _loggable_url(url: str) -> str:
    """*url* without what can authenticate: user-info, query string, fragment."""
    from fim_one.core.assembly.mcp_address import strip_url_secrets

    return strip_url_secrets(url) or ""


def _tool_def_from_mcp(tool: Any) -> dict[str, Any]:
    """Build the adapter's tool dict from an MCP ``Tool``.

    mcp 2.x exposes the JSON Schema as ``input_schema``. mcp 1.x used
    ``inputSchema``. The adapter's own dict keeps the ``inputSchema`` key.
    """
    schema = getattr(tool, "input_schema", None)
    if schema is None:
        schema = getattr(tool, "inputSchema", None)
    if not isinstance(schema, dict):
        schema = {"type": "object", "properties": {}}
    description = getattr(tool, "description", None) or ""
    return {
        "name": tool.name,
        "description": description,
        "inputSchema": schema,
    }


class _Contexts:
    """The transports and sessions a client has open, closed last-in first-out.

    Not :class:`contextlib.AsyncExitStack`, for what that does when the
    closing task is being cancelled (a client that went away), which is when
    connections are usually closed. Every context on the stack then fails
    with a CancelledError of its own, and the exit stack hands each failure
    to the next context and links the errors to one another through
    ``__context__``. With two HTTP servers the links form a cycle. anyio
    follows the ``__context__`` chain of a cancelled task with no end to it,
    so the event loop spins and the process stops answering every request.

    Here each context is closed on its own, with no error handed on, and one
    failure is raised once all of them have been closed: an ordinary error
    if there was one (a server that refused the connection), otherwise the
    cancellation. The others are logged.
    """

    def __init__(self) -> None:
        self._entered: list[contextlib.AbstractAsyncContextManager[Any]] = []

    async def enter_async_context(self, context: Any) -> Any:
        value = await context.__aenter__()
        self._entered.append(context)
        return value

    def mark(self) -> int:
        """Where the stack stands now, for :meth:`close_back_to`."""
        return len(self._entered)

    async def close_back_to(self, mark: int) -> list[BaseException]:
        """Close what was entered since *mark*, newest first; what failed, in that order."""
        failures: list[BaseException] = []
        while len(self._entered) > mark:
            context = self._entered.pop()
            try:
                await context.__aexit__(None, None, None)
            except BaseException as exc:
                failures.append(exc)
        return failures

    async def aclose(self) -> None:
        failures = await self.close_back_to(0)
        if not failures:
            return
        # A transport whose server refused it cancels its own session, which
        # fails first, with that cancellation; the reason comes after, from
        # the transport. The reason is what the caller can act on.
        raised = next((f for f in failures if isinstance(f, Exception)), failures[0])
        for other in failures:
            if other is not raised and isinstance(other, Exception):
                # Not ``exc_info``: a connection error quotes the URL it
                # failed for, which can carry a credential.
                logger.warning(
                    "Another MCP connection failed to close\n%s",
                    _traceback_without_url_secrets(other),
                )
        # Outside the handler above, so that it is linked to nothing.
        raise raised


class MCPConnectionError(ConnectionError):
    """An MCP server could not be connected.

    The message names the kind of failure only (also in ``kind``); the
    failure itself, which can quote the server's URL, is the ``__cause__``.
    """

    def __init__(self, name: str, kind: str) -> None:
        super().__init__(f"MCP server {name!r} could not be connected ({kind})")
        self.kind = kind


def _first_cause(exc: BaseException) -> BaseException:
    """The first error inside *exc* when it is a group of them, else *exc*."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def _traceback_without_url_secrets(exc: BaseException) -> str:
    from fim_one.core.assembly.mcp_address import traceback_without_url_secrets

    return traceback_without_url_secrets(exc)


class MCPClient:
    """Manages connections to one or more MCP servers and provides tool adapters.

    Keeps server processes and
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
        self._exit_stack = _Contexts()
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

    async def _give_up_connecting(
        self, name: str, mark: int, exc: BaseException, previous: Any
    ) -> None:
        """Close what connecting to *name* had opened, after *exc* ended it.

        Returns for the caller to re-raise *exc*, unless *exc* is not what
        happened.

        A transport whose server cannot be reached does not raise the reason
        where the connection is being made: it cancels the task that is
        making it, and the reason comes out when the transport is closed.
        Left as a cancellation it would end the caller's whole request or
        chat turn, for one server that is down. Once the transport is closed
        that cancellation is withdrawn; if the task is then not being
        cancelled by anyone else, the reason is raised in its place as
        :class:`MCPConnectionError`.

        The other way round: a cancellation from outside (the user stopped
        the turn, a time limit ran out) can arrive while the failed
        connection is being closed. It is delivered once, into the close,
        which keeps it with the other failures. The task is still marked as
        being cancelled, and a cancellation is raised whatever *exc* was:
        "skip this server" must not carry on a turn that was stopped.

        *previous* is the session registered under *name* before this
        attempt (a second connection under a name already in use).
        """
        if previous is None:
            self._sessions.pop(name, None)
        else:
            self._sessions[name] = previous
        failures = await self._exit_stack.close_back_to(mark)
        reasons = [f for f in failures if isinstance(f, Exception)]
        task = asyncio.current_task()
        being_cancelled = task is not None and task.cancelling() > 0
        by_the_transport = isinstance(exc, asyncio.CancelledError) and not being_cancelled
        for reason in reasons[1:] if by_the_transport else reasons:
            # Not ``exc_info``: a connection error quotes the URL it
            # failed for, which can carry a credential.
            logger.warning(
                "An MCP connection that failed did not close cleanly\n%s",
                _traceback_without_url_secrets(reason),
            )
        if being_cancelled:
            if isinstance(exc, asyncio.CancelledError):
                return
            raise asyncio.CancelledError()
        if by_the_transport and reasons:
            cause = _first_cause(reasons[0])
            raise MCPConnectionError(name, type(cause).__name__) from reasons[0]

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

        # Defense-in-depth: validate command even if caller already checked
        from fim_one.core.security import validate_stdio_command
        validate_stdio_command(command)

        server_params = StdioServerParameters(
            command=command,
            args=args or [],
            env=env,
            cwd=working_dir,
        )

        # The arguments can carry a credential (``--api-key …``): their
        # number is logged, not their text.
        logger.info(
            "Connecting to MCP server %r: %s (%d argument(s))", name, command, len(args or [])
        )

        # Enter the stdio_client and ClientSession context managers via the
        # exit stack so they stay alive until disconnect_all() is called.
        mark = self._exit_stack.mark()
        previous = self._sessions.get(name)
        try:
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
        except BaseException as exc:
            await self._give_up_connecting(name, mark, exc, previous)
            raise

        adapters: list[Tool] = []
        for tool in tools_result.tools:
            adapter = MCPToolAdapter(
                server_name=name,
                tool_def=_tool_def_from_mcp(tool),
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

        try:
            _validate_url(url)
        except ValueError as exc:
            raise ValueError(f"SSRF check failed for MCP SSE URL: {exc}") from exc

        logger.info("Connecting to MCP server %r via SSE: %s", name, _loggable_url(url))

        mark = self._exit_stack.mark()
        previous = self._sessions.get(name)
        try:
            read, write = await self._exit_stack.enter_async_context(
                sse_client(url, headers=headers or {})
            )
            session: ClientSession = await self._exit_stack.enter_async_context(
                ClientSession(read, write)
            )
            await session.initialize()
            self._sessions[name] = session

            tools_result = await session.list_tools()
        except BaseException as exc:
            await self._give_up_connecting(name, mark, exc, previous)
            raise

        adapters: list[Tool] = []
        for tool in tools_result.tools:
            adapter = MCPToolAdapter(
                server_name=name,
                tool_def=_tool_def_from_mcp(tool),
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
            import httpx2
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:
            raise ImportError(
                "The 'mcp' package is required for MCP integration. "
                "Install it with: uv sync --extra mcp"
            ) from exc

        try:
            _validate_url(url)
        except ValueError as exc:
            raise ValueError(f"SSRF check failed for MCP Streamable HTTP URL: {exc}") from exc

        logger.info(
            "Connecting to MCP server %r via Streamable HTTP: %s", name, _loggable_url(url)
        )

        # mcp 2 removed ``streamablehttp_client`` (and the third
        # ``get_session_id`` yield). Headers and the v1 timeouts
        # (30s default, 300s read) now live on an httpx2 client, which
        # we own for the life of the connection. The transport does not
        # close a client it was given.
        http_client = httpx2.AsyncClient(
            headers=headers or {},
            timeout=httpx2.Timeout(30.0, read=300.0),
        )
        mark = self._exit_stack.mark()
        previous = self._sessions.get(name)
        try:
            await self._exit_stack.enter_async_context(http_client)
            read, write = await self._exit_stack.enter_async_context(
                streamable_http_client(url, http_client=http_client)
            )
            session: ClientSession = await self._exit_stack.enter_async_context(
                ClientSession(read, write)
            )
            await session.initialize()
            self._sessions[name] = session

            tools_result = await session.list_tools()
        except BaseException as exc:
            await self._give_up_connecting(name, mark, exc, previous)
            raise

        adapters: list[Tool] = []
        for tool in tools_result.tools:
            adapter = MCPToolAdapter(
                server_name=name,
                tool_def=_tool_def_from_mcp(tool),
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

            Connections are closed last-in first-out, not one in the
            middle.  The actual transport cleanup happens
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

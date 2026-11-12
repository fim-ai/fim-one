"""A server that cannot be connected fails as that, not as a cancellation.

The Streamable HTTP transport does not raise "connection refused" where the
connection is being made. It cancels the task that is making it, and the
reason comes out when the transport is closed. The client used to pass the
cancellation on: ``except Exception`` around a connection did not see it, so
one server that was down cancelled the whole request (the tool listing of a
workflow step answered 500 "No response returned") or the whole chat turn.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import pytest

from fim_one.core.mcp import MCPClient, MCPConnectionError
from fim_one.core.mcp import client as client_module


@pytest.fixture(autouse=True)
def _loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    # The address check refuses loopback, and a system proxy would answer
    # for the server that is not there.
    monkeypatch.setattr(client_module, "_validate_url", lambda url: None)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _cancelling() -> int:
    task = asyncio.current_task()
    assert task is not None
    return task.cancelling()


class TestAServerThatIsDown:
    async def test_streamable_http_refused_is_a_connection_error(self) -> None:
        client = MCPClient()

        with pytest.raises(MCPConnectionError) as failed:
            await client.connect_streamable_http("down", f"http://127.0.0.1:{_closed_port()}/mcp")

        assert failed.value.kind == "ConnectError"
        assert "127.0.0.1" not in str(failed.value)
        # The task goes on: it is not cancelled, and what was opened is closed.
        assert _cancelling() == 0
        await asyncio.sleep(0)
        assert client._exit_stack.mark() == 0
        assert client._sessions == {}
        await client.disconnect_all()

    async def test_sse_refused_closes_what_it_opened(self) -> None:
        client = MCPClient()

        with pytest.raises(Exception) as failed:
            await client.connect_sse("down", f"http://127.0.0.1:{_closed_port()}/sse")

        assert not isinstance(failed.value, asyncio.CancelledError)
        assert _cancelling() == 0
        assert client._exit_stack.mark() == 0

    async def test_the_servers_connected_before_it_stay_open(self) -> None:
        client = MCPClient()
        earlier = _Open()
        await client._exit_stack.enter_async_context(earlier)

        with pytest.raises(MCPConnectionError):
            await client.connect_streamable_http("down", f"http://127.0.0.1:{_closed_port()}/mcp")

        assert client._exit_stack.mark() == 1
        assert not earlier.closed
        await client.disconnect_all()
        assert earlier.closed


class _Open:
    closed = False

    async def __aenter__(self) -> _Open:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.closed = True


class TestACancellationStaysOne:
    async def test_cancelled_while_connecting(self) -> None:
        """A server that accepts the connection and never answers; the caller gives up."""
        held: list[asyncio.StreamWriter] = []

        async def silent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            held.append(writer)
            await reader.read()

        server = await asyncio.start_server(silent, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        client = MCPClient()
        try:
            connecting = asyncio.create_task(
                client.connect_streamable_http("silent", f"http://127.0.0.1:{port}/mcp")
            )
            for _ in range(200):
                if held:
                    break
                await asyncio.sleep(0.01)
            assert held, "the client never reached the server"
            connecting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await connecting
            assert client._exit_stack.mark() == 0
        finally:
            for writer in held:
                writer.close()
            server.close()
            await server.wait_closed()

    async def test_timeout_around_a_server_that_is_down_is_not_mistaken(self) -> None:
        """Refused well inside the caller's time limit: the reason, not a timeout."""
        client = MCPClient()
        with pytest.raises(MCPConnectionError):
            async with asyncio.timeout(20):
                await client.connect_streamable_http(
                    "down", f"http://127.0.0.1:{_closed_port()}/mcp"
                )
        assert _cancelling() == 0

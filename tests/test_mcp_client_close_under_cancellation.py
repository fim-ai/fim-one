"""Closing MCP connections while the closing task is being cancelled.

That is how connections are usually closed: a chat turn's clean-up runs
when the client has gone away and the response's task group cancels the
stream. With two or more HTTP servers connected, the errors raised by the
cancelled close used to be linked into a cycle, and anyio walked that cycle
without end: the event loop spun at 100% CPU and the process answered
nothing more.

The scenario runs in a child process with a deadline, so that a regression
fails this test instead of hanging the run.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("mcp")

_SRC = Path(__file__).resolve().parents[1] / "src"

_SCENARIO = textwrap.dedent(
    """
    import asyncio, contextlib, ipaddress, socket, sys, threading

    import anyio
    import uvicorn
    from mcp.server.mcpserver import MCPServer

    from fim_one.core.mcp import MCPClient
    from fim_one.core.security import ssrf

    # The server under test listens on this machine.
    loopback = ipaddress.IPv4Network("127.0.0.0/8")
    ssrf._BLOCKED_IPV4_NETWORKS[:] = [n for n in ssrf._BLOCKED_IPV4_NETWORKS if n != loopback]

    servers = int(sys.argv[1])

    mcp_server = MCPServer("under-test")

    @mcp_server.tool()
    def ping() -> str:
        return "pong"

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    app = mcp_server.streamable_http_app()
    web = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=web.run, daemon=True).start()

    async def turn(connected: asyncio.Event) -> None:
        client = MCPClient()
        try:
            for index in range(servers):
                await client.connect_streamable_http(f"s{index}", f"http://127.0.0.1:{port}/mcp")
            connected.set()
            await asyncio.sleep(60)
        finally:
            await client.disconnect_all()

    async def main() -> None:
        while not web.started:
            await asyncio.sleep(0.05)
        connected = asyncio.Event()
        async with anyio.create_task_group() as group:
            group.start_soon(turn, connected)
            await connected.wait()
            group.cancel_scope.cancel()
        print("closed")

    asyncio.run(main())
    """
)


@pytest.mark.parametrize("servers", [1, 2, 3])
def test_a_cancelled_turn_closes_its_servers_and_the_loop_goes_on(
    servers: int, tmp_path: Path
) -> None:
    script = tmp_path / "scenario.py"
    script.write_text(_SCENARIO)
    env = {
        **os.environ,
        "PYTHONPATH": str(_SRC),
        "NO_PROXY": "127.0.0.1,localhost",
    }
    try:
        done = subprocess.run(
            [sys.executable, str(script), str(servers)],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
            cwd=tmp_path,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"closing {servers} MCP server(s) under cancellation did not return: "
            "the event loop is spinning"
        )
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip().endswith("closed")

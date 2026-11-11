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

import asyncio
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

    servers, transport = int(sys.argv[1]), sys.argv[2]

    mcp_server = MCPServer("under-test")

    @mcp_server.tool()
    def ping() -> str:
        return "pong"

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    app = mcp_server.sse_app() if transport == "sse" else mcp_server.streamable_http_app()
    web = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=web.run, daemon=True).start()

    async def turn(connected: asyncio.Event) -> None:
        client = MCPClient()
        try:
            for index in range(servers):
                if transport == "sse":
                    await client.connect_sse(f"s{index}", f"http://127.0.0.1:{port}/sse")
                else:
                    await client.connect_streamable_http(f"s{index}", f"http://127.0.0.1:{port}/mcp")
            connected.set()
            await asyncio.sleep(60)
        finally:
            await client.disconnect_all()

    async def main() -> None:
        for _ in range(200):
            if web.started:
                break
            await asyncio.sleep(0.05)
        else:
            sys.exit("the test server did not start")
        connected = asyncio.Event()
        async with anyio.create_task_group() as group:
            group.start_soon(turn, connected)
            await connected.wait()
            group.cancel_scope.cancel()
        print("closed")

    asyncio.run(main())
    """
)


@pytest.mark.parametrize(
    ("servers", "transport"), [(1, "http"), (2, "http"), (3, "http"), (2, "sse")]
)
def test_a_cancelled_turn_closes_its_servers_and_the_loop_goes_on(
    servers: int, transport: str, tmp_path: Path
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
            [sys.executable, str(script), str(servers), transport],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
            cwd=tmp_path,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"closing {servers} {transport} MCP server(s) under cancellation did not return: "
            "the event loop is spinning"
        )
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip().endswith("closed")


class _Closes:
    """A context that records being closed and fails as told."""

    def __init__(self, log: list[str], name: str, failure: BaseException | None = None) -> None:
        self._log, self._name, self._failure = log, name, failure

    async def __aenter__(self) -> str:
        return self._name

    async def __aexit__(self, *exc: object) -> None:
        self._log.append(f"{self._name}:{exc[0]}")
        if self._failure is not None:
            raise self._failure


async def _close(*failures: BaseException | None) -> tuple[list[str], BaseException | None]:
    from fim_one.core.mcp.client import _Contexts

    log: list[str] = []
    contexts = _Contexts()
    for index, failure in enumerate(failures):
        await contexts.enter_async_context(_Closes(log, str(index), failure))
    try:
        await contexts.aclose()
    except BaseException as exc:
        return log, exc
    return log, None


class TestEachContextIsClosedOnItsOwn:
    async def test_last_in_first_out_and_no_error_is_handed_on(self) -> None:
        cancelled = asyncio.CancelledError("inner")
        log, raised = await _close(None, None, cancelled)
        assert log == ["2:None", "1:None", "0:None"]
        assert raised is cancelled

    async def test_cancellations_are_not_linked_to_one_another(self) -> None:
        first, second = asyncio.CancelledError("a"), asyncio.CancelledError("b")
        _, raised = await _close(second, first)
        assert raised is first
        assert first.__context__ is None and second.__context__ is None

    async def test_the_reason_wins_over_the_cancellation_it_caused(self) -> None:
        # Closed first: the session, cancelled by its transport. Then the
        # transport, with why.
        reason = ConnectionError("refused")
        _, raised = await _close(reason, asyncio.CancelledError("session"))
        assert raised is reason

    async def test_closing_again_does_nothing(self) -> None:
        from fim_one.core.mcp.client import _Contexts

        log: list[str] = []
        contexts = _Contexts()
        await contexts.enter_async_context(_Closes(log, "0"))
        await contexts.aclose()
        await contexts.aclose()
        assert log == ["0:None"]

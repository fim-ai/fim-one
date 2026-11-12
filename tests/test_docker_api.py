"""The docker backend talks to the sandbox proxy itself, with a token no child holds.

``core/tool/sandbox/docker_api.py`` makes the few requests a sandbox run
needs in-process, so the proxy's token stays in the application's memory,
and removes the container whenever a run ends early.

The chain under test is the real one except for the daemon: the client,
the real proxy (``docker_proxy.serve``), and a stand-in for ``dockerd``.
"""

from __future__ import annotations

import asyncio
import json
import struct
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from fim_one.core.tool.sandbox import DockerBackend
from fim_one.core.tool.sandbox.docker_api import ContainerSpec, parse_memory, run_container
from fim_one.core.tool.sandbox.docker_proxy import Policy, serve

pytestmark = pytest.mark.timeout(30)

TOKEN = "k" * 64
ROOT = "/srv/fim/data/sandbox"
IMAGE = "fim-sandbox:python"


def _frame(kind: int, data: bytes) -> bytes:
    return bytes([kind, 0, 0, 0]) + struct.pack(">I", len(data)) + data


class _Dockerd:
    """Enough of the Docker API to run one container, as ``dockerd`` answers it."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []
        self.created: list[dict[str, Any]] = []
        self.stdin = b""
        self.exit_code = 0
        self.runs_for = 0.0
        self.missing_image = False
        # What the daemon answers these with, when not the usual.
        self.attach_status = ""
        self.wait_status = ""
        self.start_status = ""
        self.garbled = False
        self.output = b""
        self.output_first = False
        self.killed = asyncio.Event()
        # Seconds the daemon takes over a removal, and over a create.
        self.delete_delay = 0.0
        self.create_delay = 0.0
        self._started = asyncio.Event()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        method, target = head.split(" ")[:2]
        length = next(
            (
                int(line.split(":")[1])
                for line in head.split("\r\n")
                if line.lower().startswith("content-length")
            ),
            0,
        )
        body = await reader.readexactly(length) if length else b""
        self.seen.append((method, target.split("?")[0]))
        assert "X-Fim-Sandbox-Token" not in head  # the proxy keeps it to itself

        def answer(status: str, payload: bytes = b"") -> None:
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Length: {len(payload)}\r\n\r\n".encode() + payload
            )

        if self.garbled:
            writer.write(b"HTTP/1.1 two-hundred OK\r\n\r\n")
        elif method == "DELETE":
            await asyncio.sleep(self.delete_delay)
            self.killed.set()
            answer("204 No Content")
        elif target.startswith("/containers/create"):
            await asyncio.sleep(self.create_delay)
            if self.missing_image:
                self.missing_image = False
                answer("404 Not Found", b'{"message":"No such image: fim-sandbox:python"}')
            else:
                self.created.append(json.loads(body))
                answer("201 Created", b'{"Id":"abc","Warnings":[]}')
        elif target.startswith("/images/create"):
            answer("200 OK", b'{"status":"Pulling"}')
        elif "/attach" in target and self.attach_status:
            answer(self.attach_status, b'{"message":"cannot attach"}')
        elif "/attach" in target:
            writer.write(b"HTTP/1.1 101 UPGRADED\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n\r\n")
            await writer.drain()
            await self._started.wait()
            if self.output_first:
                # A program that prints before it has read its input.
                writer.write(_frame(1, self.output))
                await writer.drain()
            if "stdin=1" in target:
                self.stdin = await reader.read()
            await self._finished()
            if self.output and not self.output_first:
                writer.write(_frame(1, self.output))
            else:
                writer.write(
                    _frame(1, b"out:" + self.stdin[:64]) + _frame(2, b"warn") + _frame(1, b"!")
                )
        elif "/wait" in target and self.wait_status:
            answer(self.wait_status, b'{"message":"cannot wait"}')
        elif "/wait" in target:
            writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
            await writer.drain()
            await self._started.wait()
            await self._finished()
            payload = json.dumps({"StatusCode": 137 if self.killed.is_set() else self.exit_code})
            writer.write(f"{len(payload):x}\r\n{payload}\r\n0\r\n\r\n".encode())
        elif target.endswith("/start") and self.start_status:
            answer(self.start_status, b'{"message":"cannot start"}')
        elif target.endswith("/start"):
            self._started.set()
            answer("204 No Content")
        elif "/kill" in target:
            self.killed.set()
            answer("204 No Content")
        else:
            answer("404 Not Found", b'{"message":"?"}')
        await writer.drain()
        writer.close()

    async def _finished(self) -> None:
        with_kill = asyncio.create_task(self.killed.wait())
        await asyncio.wait({with_kill}, timeout=self.runs_for)
        with_kill.cancel()


@pytest.fixture()
async def chain() -> AsyncIterator[tuple[str, _Dockerd]]:
    """``tcp://`` endpoint of the real proxy, in front of the stand-in daemon."""
    dockerd = _Dockerd()
    policy = Policy(images=frozenset({IMAGE}), sandbox_root=ROOT, token=TOKEN)
    with tempfile.TemporaryDirectory(prefix="fa", dir="/tmp") as directory:
        path = str(Path(directory) / "docker.sock")
        upstream = await asyncio.start_unix_server(dockerd.handle, path)
        server = await serve(policy, host="127.0.0.1", port=0, docker_socket=path)
        port = server.sockets[0].getsockname()[1]
        try:
            yield f"tcp://127.0.0.1:{port}", dockerd
        finally:
            server.close()
            upstream.close()


def _spec(**changes: Any) -> ContainerSpec:
    values: dict[str, Any] = {
        "name": "fim-sandbox-0123abcd",
        "image": IMAGE,
        "argv": ["/bin/sh"],
        "workdir": "/workspace/sub",
        "bind": f"{ROOT}/conv-1/workspace:/workspace",
        "network": "none",
        "memory": 256 * 1024**2,
        "nano_cpus": 500_000_000,
        "pids_limit": 256,
        "user": "10001:10001",
        "env": ["HOME=/tmp"],
        "stdin": True,
    }
    values.update(changes)
    return ContainerSpec(**values)


class TestRunThroughTheProxy:
    async def test_output_stdin_and_exit_code(self, chain: tuple[str, _Dockerd]) -> None:
        endpoint, dockerd = chain
        dockerd.exit_code = 3

        result = await run_container(endpoint, TOKEN, _spec(), stdin_data="echo hi\n", timeout=10)

        assert (result.stdout, result.stderr, result.exit_code) == ("out:echo hi\n!", "warn", 3)
        assert result.error is None and not result.timed_out
        assert dockerd.seen == [
            ("POST", "/containers/create"),
            ("POST", "/containers/fim-sandbox-0123abcd/attach"),
            ("POST", "/containers/fim-sandbox-0123abcd/wait"),
            ("POST", "/containers/fim-sandbox-0123abcd/start"),
        ]

    async def test_what_the_client_asks_for_is_what_the_proxy_requires(
        self, chain: tuple[str, _Dockerd]
    ) -> None:
        endpoint, dockerd = chain

        await run_container(endpoint, TOKEN, _spec(), stdin_data="", timeout=10)

        (created,) = dockerd.created
        assert created["HostConfig"] == {
            "Binds": [f"{ROOT}/conv-1/workspace:/workspace"],
            "NetworkMode": "none",
            "Memory": 268435456,
            "NanoCpus": 500000000,
            "PidsLimit": 256,
            "OomScoreAdj": 0,
            "AutoRemove": True,
            "SecurityOpt": ["no-new-privileges"],
            "CapDrop": ["ALL"],
        }
        assert created["User"] == "10001:10001" and created["WorkingDir"] == "/workspace/sub"

    @pytest.mark.parametrize("token", [None, "wrong" * 13])
    async def test_without_the_token_nothing_runs(
        self, chain: tuple[str, _Dockerd], token: str | None
    ) -> None:
        endpoint, dockerd = chain

        result = await run_container(endpoint, token, _spec(), stdin_data="", timeout=10)

        assert result.exit_code == -1 and "token" in (result.error or "")
        assert dockerd.seen == []

    @pytest.mark.parametrize(
        ("change", "why"),
        [
            ({"bind": "/:/workspace"}, "sandbox workspace"),
            ({"network": "bridge"}, "NetworkMode"),
            ({"user": None}, "User"),
            ({"image": "alpine"}, "image"),
        ],
    )
    async def test_the_proxy_still_decides(
        self, chain: tuple[str, _Dockerd], change: dict[str, Any], why: str
    ) -> None:
        endpoint, dockerd = chain

        result = await run_container(endpoint, TOKEN, _spec(**change), stdin_data="", timeout=10)

        assert result.exit_code == -1 and why.lower() in (result.error or "").lower()
        assert dockerd.created == []

    async def test_a_run_that_times_out_is_removed(self, chain: tuple[str, _Dockerd]) -> None:
        endpoint, dockerd = chain
        dockerd.runs_for = 30

        result = await run_container(endpoint, TOKEN, _spec(), stdin_data="sleep 60", timeout=1)

        assert result.timed_out and result.exit_code == 124
        assert dockerd.killed.is_set()
        assert dockerd.seen[-1] == ("DELETE", "/containers/fim-sandbox-0123abcd")

    @pytest.mark.parametrize(
        "knob", ["attach_status", "wait_status", "start_status"], ids=["attach", "wait", "start"]
    )
    async def test_a_container_that_was_created_and_not_run_is_removed(
        self, chain: tuple[str, _Dockerd], knob: str
    ) -> None:
        """Created, then a later step is refused: it must not stay behind."""
        endpoint, dockerd = chain
        setattr(dockerd, knob, "500 Internal Server Error")

        result = await run_container(endpoint, TOKEN, _spec(), stdin_data="", timeout=10)

        assert result.exit_code == -1 and "failed" in (result.error or "")
        assert dockerd.seen[-1] == ("DELETE", "/containers/fim-sandbox-0123abcd")

    async def test_a_cancelled_run_is_removed(self, chain: tuple[str, _Dockerd]) -> None:
        """The user stops the turn: the container does not keep running."""
        endpoint, dockerd = chain
        dockerd.runs_for = 30
        run = asyncio.create_task(
            run_container(endpoint, TOKEN, _spec(), stdin_data="sleep 600", timeout=60)
        )
        for _ in range(200):
            if ("POST", "/containers/fim-sandbox-0123abcd/start") in dockerd.seen:
                break
            await asyncio.sleep(0.01)

        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

        await asyncio.wait_for(dockerd.killed.wait(), timeout=5)
        assert dockerd.seen[-1] == ("DELETE", "/containers/fim-sandbox-0123abcd")

    async def test_a_stop_while_the_removal_is_awaited_is_not_lost(
        self, chain: tuple[str, _Dockerd]
    ) -> None:
        """Cancelled during the clean-up wait: the caller sees the cancellation."""
        endpoint, dockerd = chain
        dockerd.attach_status = "500 Internal Server Error"
        dockerd.delete_delay = 2
        run = asyncio.create_task(
            run_container(endpoint, TOKEN, _spec(), stdin_data="", timeout=10)
        )
        for _ in range(300):
            if dockerd.seen and dockerd.seen[-1][0] == "DELETE":
                break
            await asyncio.sleep(0.01)
        assert dockerd.seen[-1][0] == "DELETE"

        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

        assert run.cancelled()
        await asyncio.wait_for(dockerd.killed.wait(), timeout=5)  # the removal went on

    async def test_a_stop_while_the_create_is_answered_removes_by_name(
        self, chain: tuple[str, _Dockerd]
    ) -> None:
        """The create was sent, its answer not read yet: the container may exist."""
        endpoint, dockerd = chain
        dockerd.create_delay = 30
        run = asyncio.create_task(
            run_container(endpoint, TOKEN, _spec(), stdin_data="", timeout=10)
        )
        for _ in range(300):
            if ("POST", "/containers/create") in dockerd.seen:
                break
            await asyncio.sleep(0.01)

        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

        await asyncio.wait_for(dockerd.killed.wait(), timeout=5)
        assert ("DELETE", "/containers/fim-sandbox-0123abcd") in dockerd.seen

    async def test_a_create_that_is_not_answered_in_time(
        self, chain: tuple[str, _Dockerd], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fim_one.core.tool.sandbox import docker_api

        endpoint, dockerd = chain
        dockerd.create_delay = 30
        monkeypatch.setattr(docker_api, "_REQUEST_TIMEOUT", 0.3)

        result = await run_container(endpoint, TOKEN, _spec(), stdin_data="", timeout=10)

        assert result.exit_code == -1
        assert "cannot be reached" not in (result.error or "")
        assert "being created" in (result.error or "")
        await asyncio.wait_for(dockerd.killed.wait(), timeout=5)
        assert ("DELETE", "/containers/fim-sandbox-0123abcd") in dockerd.seen

    async def test_a_refused_create_sends_no_removal(self, chain: tuple[str, _Dockerd]) -> None:
        endpoint, dockerd = chain

        result = await run_container(
            endpoint, TOKEN, _spec(image="alpine"), stdin_data="", timeout=10
        )

        assert result.exit_code == -1 and dockerd.seen == []

    async def test_shutdown_waits_for_removals_under_way(self, chain: tuple[str, _Dockerd]) -> None:
        from fim_one.core.tool.sandbox.docker_backend import finish_cleanups

        endpoint, dockerd = chain
        dockerd.attach_status = "500 Internal Server Error"
        dockerd.delete_delay = 0.5
        run = asyncio.create_task(
            run_container(endpoint, TOKEN, _spec(), stdin_data="", timeout=10)
        )
        for _ in range(300):
            if dockerd.seen and dockerd.seen[-1][0] == "DELETE":
                break
            await asyncio.sleep(0.01)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        assert not dockerd.killed.is_set()

        assert await finish_cleanups(timeout=5)
        assert dockerd.killed.is_set()

    async def test_a_run_that_completes_is_not_removed_twice(
        self, chain: tuple[str, _Dockerd]
    ) -> None:
        endpoint, dockerd = chain

        await run_container(endpoint, TOKEN, _spec(), stdin_data="", timeout=10)

        assert not [seen for seen in dockerd.seen if seen[0] == "DELETE"]

    async def test_output_beyond_the_cap_is_dropped_and_said_so(
        self, chain: tuple[str, _Dockerd], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fim_one.core.tool.sandbox import docker_api

        endpoint, dockerd = chain
        dockerd.output = b"x" * 300_000

        async def capped(reader: asyncio.StreamReader) -> tuple[bytes, bytes]:
            return await original(reader, 100_000)

        original = docker_api._demultiplex
        monkeypatch.setattr(docker_api, "_demultiplex", capped)

        result = await run_container(endpoint, TOKEN, _spec(), stdin_data="", timeout=10)

        assert result.exit_code == 0
        assert result.stdout.startswith("x" * 100_000) and len(result.stdout) < 100_200
        assert "[Output truncated" in result.stdout

    async def test_input_is_written_while_output_is_read(self, chain: tuple[str, _Dockerd]) -> None:
        """A large input to a program that prints early does not stall the run."""
        endpoint, dockerd = chain
        dockerd.output_first = True
        dockerd.output = b"y" * 2_000_000

        result = await run_container(
            endpoint, TOKEN, _spec(), stdin_data="z" * 4_000_000, timeout=20
        )

        assert not result.timed_out and result.exit_code == 0
        assert result.stdout.startswith("y" * 1000) and len(dockerd.stdin) == 4_000_000

    async def test_an_answer_that_is_not_http_is_an_error_not_a_crash(
        self, chain: tuple[str, _Dockerd]
    ) -> None:
        endpoint, dockerd = chain
        dockerd.garbled = True

        result = await run_container(endpoint, TOKEN, _spec(), stdin_data="", timeout=10)

        assert result.exit_code == -1 and result.error

    async def test_a_missing_image_is_pulled_once(self, chain: tuple[str, _Dockerd]) -> None:
        endpoint, dockerd = chain
        dockerd.missing_image = True

        result = await run_container(endpoint, TOKEN, _spec(), stdin_data="", timeout=10)

        assert result.exit_code == 0
        assert [target for _, target in dockerd.seen[:3]] == [
            "/containers/create",
            "/images/create",
            "/containers/create",
        ]

    async def test_a_proxy_that_is_not_there(self) -> None:
        result = await run_container("tcp://127.0.0.1:1", TOKEN, _spec(), stdin_data="", timeout=5)

        assert result.exit_code == -1 and "cannot be reached" in (result.error or "")


async def test_a_busy_proxy_is_asked_again() -> None:
    """A 503 with Retry-After (nothing was forwarded) is retried; other answers are not."""
    from fim_one.core.tool.sandbox.docker_api import _Proxy

    answers = [
        b"HTTP/1.1 503 Service Unavailable\r\nRetry-After: 1\r\nContent-Length: 0\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok",
    ]
    seen = 0

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal seen
        await reader.readuntil(b"\r\n\r\n")
        writer.write(answers[min(seen, len(answers) - 1)])
        seen += 1
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        status, body = await _Proxy(f"tcp://127.0.0.1:{port}", TOKEN).call("GET", "/_ping")
        assert (status, body, seen) == (200, b"ok", 2)

        answers[1] = b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n"
        seen = 1
        status, _ = await _Proxy(f"tcp://127.0.0.1:{port}", TOKEN).call("GET", "/_ping")
        assert (status, seen) == (503, 2)  # not the proxy's busy answer: not retried
    finally:
        server.close()


class TestTheBackendUsesIt:
    async def test_no_docker_process_is_started_and_the_token_stays_here(
        self, chain: tuple[str, _Dockerd], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No child process is started, and the token is not left in the environment."""
        import os

        import fim_one.core.tool.sandbox as sandbox

        endpoint, dockerd = chain

        async def _no_children(*_cmd: str, **_kwargs: Any) -> Any:
            raise AssertionError("no process may be started")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _no_children)
        monkeypatch.setattr(sandbox, "_proxy_token", None)
        monkeypatch.setattr(sandbox, "_backend", None)
        monkeypatch.setenv("CODE_EXEC_BACKEND", "docker")
        monkeypatch.setenv("DOCKER_HOST", endpoint)
        monkeypatch.setenv("SANDBOX_PROXY_TOKEN", TOKEN)
        monkeypatch.setenv("DOCKER_HOST_DATA_DIR", "/srv/fim/data")
        monkeypatch.setenv("DOCKER_PYTHON_IMAGE", IMAGE)
        monkeypatch.setenv("DOCKER_SHELL_IMAGE", IMAGE)
        monkeypatch.setenv("DOCKER_SANDBOX_USER", "10001:10001")
        base = tmp_path / "data" / "sandbox"
        backend = sandbox.get_sandbox_backend()
        assert isinstance(backend, DockerBackend)
        backend._sandbox_base = base
        backend._container_data_dir = str(base.parent)
        workspace = base / "conv-1" / "workspace"

        shell = await backend.run_shell("echo hi", sandbox_dir=workspace / "sub", timeout=10)
        code = await backend.run_code("print(1)", language="python", exec_dir=workspace, timeout=10)

        assert "SANDBOX_PROXY_TOKEN" not in os.environ
        assert shell.stdout == "out:echo hi!" and code.exit_code == 0
        shell_body, code_body = dockerd.created
        assert shell_body["Cmd"] == ["/bin/sh"] and shell_body["WorkingDir"] == "/workspace/sub"
        assert code_body["Cmd"][0] == "python" and code_body["Cmd"][1].startswith(
            "/workspace/script_"
        )
        # Neither has a network.
        assert {b["HostConfig"]["NetworkMode"] for b in dockerd.created} == {"none"}

    def test_memory_sizes(self) -> None:
        assert parse_memory("256m") == 256 * 1024**2
        assert parse_memory("1g") == 1024**3 and parse_memory("512K") == 512 * 1024
        assert parse_memory("1048576") == 1048576 and parse_memory(7) == 7
        with pytest.raises(ValueError):
            parse_memory("lots")

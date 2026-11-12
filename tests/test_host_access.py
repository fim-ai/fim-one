"""Nothing a user steers reaches a container runtime from the application container.

While the application container can reach a container runtime (a socket
file, or an endpoint that is a daemon), the local code-execution backend
and stdio MCP servers start nothing at all. The check is on what is
reachable, not on the words of a command (``core/security/host_access.py``).
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from fim_one.core.tool.builtin.shell_exec import ShellExecTool
from fim_one.core.tool.sandbox import LocalBackend
from fim_one.core.tool.sandbox._env_utils import build_safe_env


@pytest.fixture()
def docker_socket(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A listening unix socket standing in for ``/var/run/docker.sock``."""
    import tempfile

    # A short path: AF_UNIX paths are limited to about a hundred bytes.
    with tempfile.TemporaryDirectory(prefix="fs", dir="/tmp") as directory:
        path = str(Path(directory) / "docker.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(1)
        try:
            yield path
        finally:
            server.close()


@pytest.fixture()
def in_container_with_socket(docker_socket: str, monkeypatch: pytest.MonkeyPatch) -> str:
    from fim_one.core.security import host_access

    # Only the stand-in: whatever the machine running the tests has is ignored.
    monkeypatch.setattr(host_access, "_WELL_KNOWN_SOCKETS", ())
    monkeypatch.setattr(host_access, "_SOCKET_GLOBS", ())
    monkeypatch.setenv("FIM_IN_CONTAINER", "1")
    monkeypatch.setenv("DOCKER_HOST", f"unix://{docker_socket}")
    return docker_socket


class TestExposedDockerSocket:
    def test_found_in_a_container(self, in_container_with_socket: str) -> None:
        from fim_one.core.security.host_access import (
            exposed_docker_socket,
            local_execution_refusal,
        )

        assert exposed_docker_socket() == in_container_with_socket
        assert in_container_with_socket in (local_execution_refusal() or "")

    def test_not_looked_for_outside_a_container(
        self, docker_socket: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Outside a container (a developer's machine) nothing is refused."""
        from fim_one.core.security.host_access import local_execution_refusal

        monkeypatch.setenv("FIM_IN_CONTAINER", "0")
        monkeypatch.setenv("DOCKER_HOST", f"unix://{docker_socket}")

        assert local_execution_refusal() is None

    def test_a_tcp_docker_host_is_not_a_socket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The sandbox proxy: it only starts sandbox containers."""
        from fim_one.core.security import host_access

        monkeypatch.setenv("FIM_IN_CONTAINER", "1")
        monkeypatch.setenv("DOCKER_HOST", "tcp://docker-socket-proxy:2375")
        monkeypatch.setattr(host_access, "_WELL_KNOWN_SOCKETS", ())

        assert host_access.local_execution_refusal() is None

    def test_a_plain_file_at_the_path_is_not_a_socket(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fim_one.core.security import host_access

        (tmp_path / "docker.sock").write_text("")
        monkeypatch.setenv("FIM_IN_CONTAINER", "1")
        monkeypatch.setenv("DOCKER_HOST", f"unix://{tmp_path / 'docker.sock'}")
        monkeypatch.setattr(host_access, "_WELL_KNOWN_SOCKETS", ())

        assert host_access.exposed_docker_socket() is None


class TestLocalBackendRefuses:
    async def test_shell(self, in_container_with_socket: str, tmp_path: Path) -> None:
        marker = tmp_path / "ran"

        result = await LocalBackend().run_shell(
            f"touch {marker}", sandbox_dir=tmp_path / "sb", timeout=10
        )

        assert result.exit_code == -1 and "container runtime" in (result.error or "")
        assert not marker.exists()

    @pytest.mark.parametrize("language", ["python", "javascript"])
    async def test_code(self, in_container_with_socket: str, tmp_path: Path, language: str) -> None:
        result = await LocalBackend().run_code(
            "print(1)", language=language, exec_dir=tmp_path / "exec", timeout=10
        )

        assert result.exit_code == -1 and "container runtime" in (result.error or "")
        assert not (tmp_path / "exec").exists()

    async def test_the_shell_tool_says_why(
        self, in_container_with_socket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import fim_one.core.tool.sandbox as sandbox

        monkeypatch.setenv("CODE_EXEC_BACKEND", "local")
        monkeypatch.setattr(sandbox, "_backend", None)

        answer = await ShellExecTool(sandbox_dir=tmp_path / "sb").run(command="ls")

        assert isinstance(answer, str)
        assert answer.startswith("[Error]") and "container runtime" in answer

    async def test_without_the_socket_it_runs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fim_one.core.security import host_access

        monkeypatch.setenv("FIM_IN_CONTAINER", "1")
        monkeypatch.delenv("DOCKER_HOST", raising=False)
        monkeypatch.setattr(host_access, "_WELL_KNOWN_SOCKETS", ())

        result = await LocalBackend().run_shell("echo ok", sandbox_dir=tmp_path, timeout=10)

        assert result.stdout.strip() == "ok"


class TestStdioMcpRefuses:
    async def test_no_process_is_started(
        self, in_container_with_socket: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fim_one.core.mcp import MCPClient

        monkeypatch.setenv("ALLOWED_STDIO_COMMANDS", "python3")
        client = MCPClient()

        with pytest.raises(RuntimeError, match="container runtime"):
            await client.connect_stdio(name="x", command="python3", args=["-c", "print(1)"])


class TestShellWordListAndEnvironment:
    """What the model is told at once; the refusal above is what protects."""

    @pytest.mark.parametrize(
        "command",
        [
            "docker run alpine true",
            "/usr/local/bin/docker ps",
            "docker-compose up",
            "podman run alpine",
            "cat /proc/1/environ",
            "strings /proc/self/environ",
            "cat /proc/7/mem",
            "ls -la /var/run/docker.sock",
        ],
    )
    async def test_blocked(self, tmp_path: Path, command: str) -> None:
        answer = await ShellExecTool(sandbox_dir=tmp_path).run(command=command)

        assert isinstance(answer, str) and answer.startswith("[Error] Command blocked")

    def test_children_are_not_told_where_docker_is(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DOCKER_HOST", "tcp://docker-socket-proxy:2375")
        monkeypatch.setenv("DOCKER_HOST_DATA_DIR", "/srv/fim/data")

        env = build_safe_env("/tmp/sb")

        assert not [name for name in env if name.upper().startswith("DOCKER_")]


class TestHardenProcess:
    def test_it_never_raises(self) -> None:
        from fim_one.core.security.host_access import harden_process

        assert harden_process() in (True, False)


class TestOtherWaysToAContainerRuntime:
    """Podman, containerd, sockets and endpoints the environment names."""

    @pytest.fixture(autouse=True)
    def _in_container(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fim_one.core.security import host_access

        monkeypatch.setattr(host_access, "_WELL_KNOWN_SOCKETS", ())
        monkeypatch.setattr(host_access, "_SOCKET_GLOBS", ())
        monkeypatch.setattr(host_access, "_probed", {})
        monkeypatch.setenv("FIM_IN_CONTAINER", "1")
        for name in ("DOCKER_HOST", "CONTAINER_HOST", "CONTAINERD_ADDRESS"):
            monkeypatch.delenv(name, raising=False)

    @pytest.fixture()
    def answering(self) -> Iterator[Any]:
        """A TCP endpoint that answers every request with the given bytes."""
        import threading

        servers: list[socket.socket] = []

        def start(answer: bytes) -> int:
            server = socket.socket()
            server.bind(("127.0.0.1", 0))
            server.listen(4)
            servers.append(server)

            def serve() -> None:
                while True:
                    try:
                        conn, _ = server.accept()
                    except OSError:
                        return
                    with conn:
                        conn.recv(4096)
                        conn.sendall(answer)

            threading.Thread(target=serve, daemon=True).start()
            return int(server.getsockname()[1])

        yield start
        for server in servers:
            server.close()

    def test_the_well_known_list_names_the_other_runtimes(self) -> None:
        from fim_one.core.security import host_access

        source = Path(host_access.__file__).read_text(encoding="utf-8")
        for runtime in ("docker.sock", "podman.sock", "containerd.sock", "crio.sock"):
            assert runtime in source

    @pytest.mark.parametrize("variable", ["DOCKER_HOST", "CONTAINER_HOST", "CONTAINERD_ADDRESS"])
    @pytest.mark.parametrize("prefix", ["unix://", ""])
    def test_a_socket_named_by_the_environment(
        self, docker_socket: str, monkeypatch: pytest.MonkeyPatch, variable: str, prefix: str
    ) -> None:
        from fim_one.core.security.host_access import exposed_docker_socket

        monkeypatch.setenv(variable, prefix + docker_socket)

        assert exposed_docker_socket() == docker_socket

    def test_a_socket_found_by_pattern(
        self, docker_socket: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fim_one.core.security import host_access

        monkeypatch.setattr(
            host_access,
            "_SOCKET_GLOBS",
            (str(Path(docker_socket).parent.parent / "*/docker.sock"),),
        )

        assert host_access.exposed_docker_socket() == docker_socket

    def test_a_tcp_endpoint_that_is_a_docker_daemon(
        self, answering: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fim_one.core.security.host_access import local_execution_refusal

        port = answering(b"HTTP/1.1 200 OK\r\nApi-Version: 1.47\r\nContent-Length: 2\r\n\r\nOK")
        monkeypatch.setenv("DOCKER_HOST", f"tcp://127.0.0.1:{port}")

        assert f"DOCKER_HOST=tcp://127.0.0.1:{port}" in (local_execution_refusal() or "")

    def test_a_tcp_endpoint_that_is_something_else(
        self, answering: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not the proxy, and it answers: not given the benefit of the doubt."""
        from fim_one.core.security.host_access import local_execution_refusal

        port = answering(
            b"HTTP/1.1 400 Bad Request\r\n\r\nClient sent an HTTP request to an HTTPS server."
        )
        monkeypatch.setenv("DOCKER_HOST", f"tcp://127.0.0.1:{port}")

        assert local_execution_refusal() is not None

    def test_the_sandbox_proxy_is_recognised(
        self, answering: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fim_one.core.security.host_access import local_execution_refusal

        port = answering(
            b"HTTP/1.1 401 Unauthorized\r\n\r\n"
            b'{"message": "sandbox proxy: the sandbox token is missing or wrong"}'
        )
        monkeypatch.setenv("DOCKER_HOST", f"tcp://127.0.0.1:{port}")

        assert local_execution_refusal() is None

    def test_something_that_only_claims_to_be_the_proxy_is_not(
        self, answering: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fim_one.core.security.host_access import local_execution_refusal

        port = answering(b"HTTP/1.1 200 OK\r\nServer: sandbox proxy\r\nContent-Length: 2\r\n\r\nOK")
        monkeypatch.setenv("DOCKER_HOST", f"tcp://127.0.0.1:{port}")

        assert local_execution_refusal() is not None

    def test_an_address_nothing_answers_at(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fim_one.core.security.host_access import local_execution_refusal

        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
        closed.close()
        monkeypatch.setenv("DOCKER_HOST", f"tcp://127.0.0.1:{port}")

        assert local_execution_refusal() is None

    @pytest.mark.parametrize(
        "value", ["ssh://root@build-host", "npipe:////./pipe/docker_engine", "fd://3"]
    )
    def test_any_other_scheme_is_a_way_to_a_daemon(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        from fim_one.core.security.host_access import local_execution_refusal

        monkeypatch.setenv("DOCKER_HOST", value)

        assert local_execution_refusal() is not None

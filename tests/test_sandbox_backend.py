"""Tests for the pluggable sandbox backend system."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import fim_one.core.tool.sandbox as _sandbox_module
from fim_one.core.tool.sandbox import (
    DockerBackend,
    LocalBackend,
    SandboxResult,
    get_sandbox_backend,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# The test interpreter, as a shell-safe path on every OS.
_PYTHON = Path(sys.executable).as_posix()


def _reset_singleton() -> None:
    """Reset the module-level backend singleton between tests."""
    _sandbox_module._backend = None


# ---------------------------------------------------------------------------
# TestLocalBackendPython
# ---------------------------------------------------------------------------


class TestLocalBackendPython:
    """LocalBackend.run_code with language='python'."""

    @pytest.fixture(autouse=True)
    def tmp_exec(self, tmp_path: Path) -> Path:
        self.exec_dir = tmp_path / "exec"
        return self.exec_dir

    def _backend(self) -> LocalBackend:
        return LocalBackend()

    @pytest.mark.asyncio
    async def test_print_output(self) -> None:
        result = await self._backend().run_code(
            "print('hello')", language="python", exec_dir=self.exec_dir, timeout=10
        )
        assert isinstance(result, SandboxResult)
        assert "hello" in result.stdout
        assert result.exit_code == 0
        assert not result.timed_out
        assert result.error is None
        assert result.script_path is not None
        assert result.script_path.exists()
        assert result.script_path.suffix == ".py"

    @pytest.mark.asyncio
    async def test_exception_traceback(self) -> None:
        result = await self._backend().run_code(
            "raise ValueError('boom')",
            language="python",
            exec_dir=self.exec_dir,
            timeout=10,
        )
        assert "ValueError" in result.stdout
        assert "boom" in result.stdout
        assert result.script_path is not None
        assert result.script_path.exists()

    @pytest.mark.asyncio
    async def test_blocked_module(self) -> None:
        result = await self._backend().run_code(
            "import subprocess", language="python", exec_dir=self.exec_dir, timeout=10
        )
        assert "blocked" in result.stdout.lower() or "ImportError" in result.stdout
        assert result.script_path is not None
        assert result.script_path.exists()

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        # Use a short sleep (5s) so the zombie thread dies quickly after
        # asyncio.wait_for cancels.  60s would block executor shutdown
        # and trip pytest-timeout during teardown.
        result = await self._backend().run_code(
            "import time; time.sleep(5)",
            language="python",
            exec_dir=self.exec_dir,
            timeout=1,
        )
        assert result.timed_out
        assert result.exit_code == 124

    @pytest.mark.asyncio
    async def test_unsupported_language(self) -> None:
        result = await self._backend().run_code(
            "print('hi')", language="ruby", exec_dir=self.exec_dir, timeout=10
        )
        assert result.exit_code == -1
        assert result.error is not None
        assert "ruby" in result.error.lower()


# ---------------------------------------------------------------------------
# TestLocalBackendShell
# ---------------------------------------------------------------------------


class TestLocalBackendShell:
    """LocalBackend.run_shell."""

    @pytest.fixture(autouse=True)
    def tmp_sandbox(self, tmp_path: Path) -> Path:
        self.sandbox_dir = tmp_path / "sandbox"
        return self.sandbox_dir

    def _backend(self) -> LocalBackend:
        return LocalBackend()

    @pytest.mark.asyncio
    async def test_echo(self) -> None:
        result = await self._backend().run_shell(
            "echo hello", sandbox_dir=self.sandbox_dir, timeout=10
        )
        assert "hello" in result.stdout
        assert result.exit_code == 0
        assert not result.timed_out

    @pytest.mark.asyncio
    async def test_exit_code_propagation(self) -> None:
        result = await self._backend().run_shell(
            "exit 42", sandbox_dir=self.sandbox_dir, timeout=10
        )
        assert result.exit_code == 42

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        # A Python sleep instead of `sleep` so the test runs on Windows too.
        result = await self._backend().run_shell(
            f'"{_PYTHON}" -c "import time; time.sleep(60)"',
            sandbox_dir=self.sandbox_dir,
            timeout=1,
        )
        assert result.timed_out
        assert result.exit_code == 124

    @pytest.mark.asyncio
    @pytest.mark.skipif(sys.platform != "win32", reason="Windows process-tree kill")
    async def test_timeout_kills_the_command_not_just_the_shell(self) -> None:
        # cmd.exe is the direct child; the command it starts must die too.
        marker = f"fim_tree_kill_{uuid.uuid4().hex[:8]}"
        result = await self._backend().run_shell(
            f'"{_PYTHON}" -c "import time; time.sleep(60)  # {marker}"',
            sandbox_dir=self.sandbox_dir,
            timeout=2,
        )
        assert result.timed_out
        await asyncio.sleep(1)
        query = (
            "(Get-CimInstance Win32_Process | Where-Object "
            f"{{ $_.CommandLine -like '*{marker}*' -and $_.Name -eq 'python.exe' }}).Count"
        )
        leftover = subprocess.run(
            ["powershell", "-NoProfile", "-Command", query],
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.strip()
        assert int(leftover or 0) == 0


# ---------------------------------------------------------------------------
# TestKillProcTree
# ---------------------------------------------------------------------------


class TestKillProcTree:
    """_kill_proc_tree picks the right kill path per platform.

    The Windows cases mock ``subprocess.run`` so no real taskkill is
    issued against the fake PID.
    """

    @staticmethod
    def _windows(run: MagicMock) -> Any:
        from fim_one.core.tool.sandbox import local_backend

        return (
            patch.object(local_backend.sys, "platform", "win32"),
            patch.object(local_backend.subprocess, "run", run),
            patch.object(local_backend.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
        )

    def test_windows_kills_the_tree_with_taskkill(self) -> None:
        from fim_one.core.tool.sandbox import local_backend

        proc = MagicMock(pid=1234)
        run = MagicMock(return_value=MagicMock(returncode=0))
        platform, sub_run, flag = self._windows(run)
        with platform, sub_run, flag, patch.object(
            local_backend.os, "killpg", create=True
        ) as killpg:
            local_backend._kill_proc_tree(proc)

        assert run.call_args.args[0] == ["taskkill", "/F", "/T", "/PID", "1234"]
        killpg.assert_not_called()
        proc.kill.assert_not_called()

    def test_windows_falls_back_when_taskkill_fails(self) -> None:
        from fim_one.core.tool.sandbox import local_backend

        proc = MagicMock(pid=1234)
        run = MagicMock(return_value=MagicMock(returncode=128))
        platform, sub_run, flag = self._windows(run)
        with platform, sub_run, flag:
            local_backend._kill_proc_tree(proc)

        proc.kill.assert_called_once()

    def test_windows_falls_back_when_taskkill_is_missing(self) -> None:
        from fim_one.core.tool.sandbox import local_backend

        proc = MagicMock(pid=1234)
        run = MagicMock(side_effect=FileNotFoundError)
        platform, sub_run, flag = self._windows(run)
        with platform, sub_run, flag:
            local_backend._kill_proc_tree(proc)

        proc.kill.assert_called_once()

    def test_windows_falls_back_when_taskkill_hangs(self) -> None:
        from fim_one.core.tool.sandbox import local_backend

        proc = MagicMock(pid=1234)
        run = MagicMock(side_effect=local_backend.subprocess.TimeoutExpired("taskkill", 10))
        platform, sub_run, flag = self._windows(run)
        with platform, sub_run, flag:
            local_backend._kill_proc_tree(proc)

        proc.kill.assert_called_once()

    def test_posix_kills_process_group(self) -> None:
        from fim_one.core.tool.sandbox import local_backend

        proc = MagicMock(pid=1234)
        with (
            patch.object(local_backend.sys, "platform", "linux"),
            patch.object(local_backend.os, "killpg", create=True) as killpg,
            patch.object(local_backend.signal, "SIGKILL", 9, create=True),
        ):
            local_backend._kill_proc_tree(proc)

        killpg.assert_called_once_with(1234, 9)
        proc.kill.assert_not_called()

    def test_posix_falls_back_when_group_is_gone(self) -> None:
        from fim_one.core.tool.sandbox import local_backend

        proc = MagicMock(pid=1234)
        with (
            patch.object(local_backend.sys, "platform", "linux"),
            patch.object(
                local_backend.os, "killpg", create=True, side_effect=ProcessLookupError
            ),
            patch.object(local_backend.signal, "SIGKILL", 9, create=True),
        ):
            local_backend._kill_proc_tree(proc)

        proc.kill.assert_called_once()

    def test_already_exited_process_is_ignored(self) -> None:
        from fim_one.core.tool.sandbox import local_backend

        proc = MagicMock(pid=1234)
        proc.kill.side_effect = ProcessLookupError
        run = MagicMock(return_value=MagicMock(returncode=128))
        platform, sub_run, flag = self._windows(run)
        with platform, sub_run, flag:
            local_backend._kill_proc_tree(proc)  # must not raise


# ---------------------------------------------------------------------------
# TestDockerBackendErrorHandling (no Docker required)
# ---------------------------------------------------------------------------


class TestDockerBackendErrorHandling:
    """DockerBackend error handling — no real Docker needed (monkeypatched)."""

    @pytest.fixture(autouse=True)
    def tmp_dirs(self, tmp_path: Path) -> None:
        self.exec_dir = tmp_path / "exec"
        self.sandbox_dir = tmp_path / "sandbox"

    @pytest.mark.asyncio
    async def test_docker_not_found_run_code(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FileNotFoundError → friendly error message."""
        monkeypatch.setattr(
            "asyncio.create_subprocess_exec",
            AsyncMock(side_effect=FileNotFoundError("docker not found")),
        )
        backend = DockerBackend()
        result = await backend.run_code(
            "print('hi')", language="python", exec_dir=self.exec_dir, timeout=10
        )
        assert result.exit_code == -1
        assert result.error is not None
        assert "docker" in result.error.lower()
        # Script file should still be written even when docker is unavailable
        assert result.script_path is not None
        assert result.script_path.exists()
        assert result.script_path.suffix == ".py"

    @pytest.mark.asyncio
    async def test_docker_not_found_run_shell(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "asyncio.create_subprocess_exec",
            AsyncMock(side_effect=FileNotFoundError("docker not found")),
        )
        backend = DockerBackend()
        result = await backend.run_shell(
            "echo hi", sandbox_dir=self.sandbox_dir, timeout=10
        )
        assert result.exit_code == -1
        assert result.error is not None
        assert "docker" in result.error.lower()

    @pytest.mark.asyncio
    async def test_unsupported_language(self) -> None:
        backend = DockerBackend()
        result = await backend.run_code(
            "puts 'hi'", language="ruby", exec_dir=self.exec_dir, timeout=10
        )
        assert result.exit_code == -1
        assert result.error is not None

    def test_custom_images(self) -> None:
        backend = DockerBackend(
            python_image="myrepo/python:custom",
            node_image="myrepo/node:custom",
            shell_image="myrepo/shell:custom",
        )
        assert backend._images["python"] == "myrepo/python:custom"
        assert backend._images["javascript"] == "myrepo/node:custom"
        assert backend._shell_image == "myrepo/shell:custom"


# ---------------------------------------------------------------------------
# TestGetSandboxBackend
# ---------------------------------------------------------------------------


class TestGetSandboxBackend:
    """Factory function and singleton behaviour."""

    @pytest.fixture(autouse=True)
    def reset(self) -> None:
        _reset_singleton()
        yield
        _reset_singleton()

    def test_default_is_local(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CODE_EXEC_BACKEND", raising=False)
        backend = get_sandbox_backend()
        assert isinstance(backend, LocalBackend)

    def test_docker_backend_selected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODE_EXEC_BACKEND", "docker")
        backend = get_sandbox_backend()
        assert isinstance(backend, DockerBackend)

    def test_singleton_behaviour(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CODE_EXEC_BACKEND", raising=False)
        b1 = get_sandbox_backend()
        b2 = get_sandbox_backend()
        assert b1 is b2

    def test_singleton_reset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CODE_EXEC_BACKEND", raising=False)
        b1 = get_sandbox_backend()
        _reset_singleton()
        b2 = get_sandbox_backend()
        assert b1 is not b2

    def test_docker_image_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODE_EXEC_BACKEND", "docker")
        monkeypatch.setenv("DOCKER_PYTHON_IMAGE", "custom-python:latest")
        monkeypatch.setenv("DOCKER_NODE_IMAGE", "custom-node:latest")
        monkeypatch.setenv("DOCKER_SHELL_IMAGE", "custom-shell:latest")
        backend = get_sandbox_backend()
        assert isinstance(backend, DockerBackend)
        assert backend._images["python"] == "custom-python:latest"
        assert backend._images["javascript"] == "custom-node:latest"
        assert backend._shell_image == "custom-shell:latest"

    def test_local_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODE_EXEC_BACKEND", "LOCAL")
        backend = get_sandbox_backend()
        assert isinstance(backend, LocalBackend)


# ---------------------------------------------------------------------------
# Docker integration tests (require running Docker daemon)
# ---------------------------------------------------------------------------


@pytest.mark.docker
class TestDockerBackendPython:
    """Integration tests — require Docker daemon running."""

    @pytest.fixture(autouse=True)
    def tmp_exec(self, tmp_path: Path) -> None:
        self.exec_dir = tmp_path / "exec"

    def _backend(self) -> DockerBackend:
        return DockerBackend()

    @pytest.mark.asyncio
    async def test_basic_execution(self) -> None:
        result = await self._backend().run_code(
            "print('docker works')",
            language="python",
            exec_dir=self.exec_dir,
            timeout=30,
        )
        assert "docker works" in result.stdout
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_network_isolated(self) -> None:
        """Container should have no network access."""
        result = await self._backend().run_code(
            "import urllib.request; urllib.request.urlopen('http://example.com', timeout=2)",
            language="python",
            exec_dir=self.exec_dir,
            timeout=30,
        )
        # Should fail with a network-related error
        assert result.exit_code != 0 or "Error" in result.stdout

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        result = await self._backend().run_code(
            "import time; time.sleep(60)",
            language="python",
            exec_dir=self.exec_dir,
            timeout=3,
        )
        assert result.timed_out
        assert result.exit_code == 124

    @pytest.mark.asyncio
    async def test_file_persistence(self, tmp_path: Path) -> None:
        """Files written in /workspace should appear in exec_dir on host."""
        exec_dir = tmp_path / "shared_exec"
        exec_dir.mkdir()
        await self._backend().run_code(
            "with open('/workspace/out.txt', 'w') as f: f.write('hello')",
            language="python",
            exec_dir=exec_dir,
            timeout=30,
        )
        assert (exec_dir / "out.txt").read_text() == "hello"


@pytest.mark.docker
class TestDockerBackendShell:
    """Docker shell integration tests."""

    @pytest.fixture(autouse=True)
    def tmp_sandbox(self, tmp_path: Path) -> None:
        self.sandbox_dir = tmp_path / "sandbox"

    def _backend(self) -> DockerBackend:
        return DockerBackend()

    @pytest.mark.asyncio
    async def test_basic_command(self) -> None:
        result = await self._backend().run_shell(
            "echo hello from docker", sandbox_dir=self.sandbox_dir, timeout=30
        )
        assert "hello from docker" in result.stdout
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        result = await self._backend().run_shell(
            "sleep 60", sandbox_dir=self.sandbox_dir, timeout=3
        )
        assert result.timed_out

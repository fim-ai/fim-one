"""Container code-execution defaults and the entrypoint's reading of .env."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from fim_one.core.tool.sandbox import (
    DockerBackend,
    LocalBackend,
    code_exec_backend,
    get_sandbox_backend,
)

_ROOT = Path(__file__).resolve().parents[1]


def _reset() -> None:
    import fim_one.core.tool.sandbox as sandbox

    sandbox._backend = None


def _resolve(env: dict[str, str]) -> str:
    proc = subprocess.run(
        [
            "sh",
            "-c",
            '. ./docker-entrypoint.sh; resolve_code_exec_backend; printf %s "$CODE_EXEC_BACKEND"',
        ],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


class TestEntrypointBackend:
    def test_unset_is_docker(self) -> None:
        env = {"CODE_EXEC_BACKEND_LIB_ONLY": "1", "PATH": os.environ.get("PATH", "")}
        assert _resolve(env) == "docker"

    def test_leftover_local_is_docker(self) -> None:
        env = {
            "CODE_EXEC_BACKEND_LIB_ONLY": "1",
            "CODE_EXEC_BACKEND": "local",
            "PATH": os.environ.get("PATH", ""),
        }
        assert _resolve(env) == "docker"

    def test_opt_in_keeps_local(self) -> None:
        env = {
            "CODE_EXEC_BACKEND_LIB_ONLY": "1",
            "CODE_EXEC_BACKEND": "local",
            "ALLOW_LOCAL_CODE_EXEC": "true",
            "PATH": os.environ.get("PATH", ""),
        }
        assert _resolve(env) == "local"


class TestCodeExecBackend:
    def setup_method(self) -> None:
        _reset()

    def teardown_method(self) -> None:
        _reset()

    def test_explicit_local_outside_a_container(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FIM_IN_CONTAINER", "0")
        monkeypatch.setenv("CODE_EXEC_BACKEND", "local")
        assert code_exec_backend() == "local"
        assert isinstance(get_sandbox_backend(), LocalBackend)

    def test_unknown_value_in_a_container_is_docker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FIM_IN_CONTAINER", "1")
        monkeypatch.setenv("CODE_EXEC_BACKEND", "bogus")
        assert code_exec_backend() == "docker"
        assert isinstance(get_sandbox_backend(), DockerBackend)

    def test_explicit_local_is_still_named_local(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A process that still has local keeps that name; the run itself is refused."""
        monkeypatch.setenv("FIM_IN_CONTAINER", "1")
        monkeypatch.setenv("CODE_EXEC_BACKEND", "local")
        monkeypatch.delenv("ALLOW_LOCAL_CODE_EXEC", raising=False)
        assert code_exec_backend() == "local"

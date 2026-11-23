"""Sandbox backends — pluggable OS-level execution isolation.

Switch backends via the ``CODE_EXEC_BACKEND`` environment variable:
  - ``docker`` — Docker containers, full OS-level isolation. This is the
    default inside a container (``running_in_container``).
  - ``local``  — in-process exec + subprocess, no OS isolation. This is the
    default outside a container (``./start.sh``). Inside a container it runs
    only when ``ALLOW_LOCAL_CODE_EXEC=true``; otherwise the entrypoint selects
    ``docker`` and the local backend refuses the run.

Public API
----------
get_sandbox_backend()   → SandboxBackend (singleton)
SandboxResult           — result dataclass
SandboxBackend          — Protocol (for type checking)
LocalBackend            — concrete local backend
DockerBackend           — concrete docker backend
"""

from __future__ import annotations

import logging
import os

from .docker_backend import DockerBackend
from .local_backend import LocalBackend
from .protocol import SandboxBackend, SandboxResult

logger = logging.getLogger(__name__)

__all__ = [
    "DockerBackend",
    "LocalBackend",
    "SandboxBackend",
    "SandboxResult",
    "code_exec_backend",
    "get_sandbox_backend",
    "take_proxy_token",
]

# Module-level singleton — created once on first call.
_backend: SandboxBackend | None = None

_proxy_token: str | None = None


def take_proxy_token() -> str | None:
    """The sandbox proxy's token, taken out of the process environment.

    The entrypoint hands it over as ``SANDBOX_PROXY_TOKEN``. It is kept in
    memory from the first call on and removed from ``os.environ``, so that
    nothing the application starts (a stdio MCP server, a local-backend
    command, the Docker CLI's own children) inherits it by accident.
    """
    global _proxy_token
    taken = os.environ.pop("SANDBOX_PROXY_TOKEN", None)
    if taken:
        _proxy_token = taken.strip() or None
    return _proxy_token


def code_exec_backend() -> str:
    """``docker`` or ``local``, from ``CODE_EXEC_BACKEND``.

    Unset means ``docker`` inside a container and ``local`` outside one.
    An unknown value inside a container is ``docker`` as well. Outside a
    container an unknown value stays ``local``, which is what an unset value
    used to select everywhere.
    """
    from fim_one.core.security.host_access import running_in_container

    raw = os.environ.get("CODE_EXEC_BACKEND", "").strip().lower()
    in_container = running_in_container()
    if raw in ("", "docker"):
        if raw == "" and not in_container:
            return "local"
        return "docker"
    if raw == "local":
        return "local"
    if in_container:
        logger.warning("CODE_EXEC_BACKEND=%r is not docker or local; using docker.", raw)
        return "docker"
    return "local"


def get_sandbox_backend() -> SandboxBackend:
    """Return the configured sandbox backend (singleton).

    The backend type is chosen by the ``CODE_EXEC_BACKEND`` environment
    variable.  Subsequent calls return the same instance.

    To reset the singleton in tests::

        import fim_one.core.tool.sandbox as _sandbox
        _sandbox._backend = None
    """
    global _backend
    if _backend is None:
        mode = code_exec_backend()
        if mode == "docker":
            host_data_dir = os.environ.get("DOCKER_HOST_DATA_DIR") or None
            _backend = DockerBackend(
                python_image=os.environ.get("DOCKER_PYTHON_IMAGE", "python:3.11-slim"),
                node_image=os.environ.get("DOCKER_NODE_IMAGE", "node:20-slim"),
                shell_image=os.environ.get("DOCKER_SHELL_IMAGE", "python:3.11-slim"),
                default_memory=os.environ.get("DOCKER_MEMORY", "256m"),
                default_cpu=float(os.environ.get("DOCKER_CPUS", "0.5")),
                host_data_dir=host_data_dir,
                sandbox_user=os.environ.get("DOCKER_SANDBOX_USER") or None,
                shell_network=os.environ.get("DOCKER_SHELL_NETWORK") or None,
                pids_limit=int(os.environ.get("DOCKER_PIDS_LIMIT", "256")),
                proxy_token=take_proxy_token(),
                # tcp://: the sandbox proxy. The backend then talks to it
                # itself; no Docker CLI, no child that carries the token.
                proxy_endpoint=(
                    os.environ.get("DOCKER_HOST")
                    if os.environ.get("DOCKER_HOST", "").startswith("tcp://")
                    else None
                ),
            )
            logger.info("Sandbox backend: docker (python=%s, node=%s, shell=%s, host_data_dir=%s)",
                        os.environ.get("DOCKER_PYTHON_IMAGE", "python:3.11-slim"),
                        os.environ.get("DOCKER_NODE_IMAGE", "node:20-slim"),
                        os.environ.get("DOCKER_SHELL_IMAGE", "python:3.11-slim"),
                        host_data_dir or "(not set — assumes host-local)")
        else:
            _backend = LocalBackend()
            logger.info("Sandbox backend: local (in-process exec, no container isolation)")
            from fim_one.core.security.host_access import (
                local_execution_refusal,
                running_in_container,
            )

            refusal = local_execution_refusal()
            if refusal is not None:
                logger.error("Code execution is refused. %s", refusal)
            elif running_in_container():
                logger.warning(
                    "CODE_EXEC_BACKEND=local inside a container: every user who can "
                    "use the code tools runs commands in the application container "
                    "(its data volume and the other users' files included). "
                    "Use CODE_EXEC_BACKEND=docker for anything shared."
                )
    return _backend

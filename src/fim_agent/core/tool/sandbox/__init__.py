"""Sandbox backends — pluggable OS-level execution isolation.

Switch backends via the ``CODE_EXEC_BACKEND`` environment variable:
  - ``local``  (default) — in-process exec + subprocess, no OS isolation
  - ``docker``            — Docker containers, full OS-level isolation

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
    "get_sandbox_backend",
    "SandboxResult",
    "SandboxBackend",
    "LocalBackend",
    "DockerBackend",
]

# Module-level singleton — created once on first call.
_backend: SandboxBackend | None = None


def get_sandbox_backend() -> SandboxBackend:
    """Return the configured sandbox backend (singleton).

    The backend type is chosen by the ``CODE_EXEC_BACKEND`` environment
    variable.  Subsequent calls return the same instance.

    To reset the singleton in tests::

        import fim_agent.core.tool.sandbox as _sandbox
        _sandbox._backend = None
    """
    global _backend
    if _backend is None:
        mode = os.environ.get("CODE_EXEC_BACKEND", "local").lower()
        if mode == "docker":
            host_data_dir = os.environ.get("DOCKER_HOST_DATA_DIR") or None
            _backend = DockerBackend(
                python_image=os.environ.get("DOCKER_PYTHON_IMAGE", "python:3.11-slim"),
                node_image=os.environ.get("DOCKER_NODE_IMAGE", "node:20-slim"),
                shell_image=os.environ.get("DOCKER_SHELL_IMAGE", "python:3.11-slim"),
                default_memory=os.environ.get("DOCKER_MEMORY", "256m"),
                default_cpu=float(os.environ.get("DOCKER_CPUS", "0.5")),
                host_data_dir=host_data_dir,
            )
            logger.info("Sandbox backend: docker (python=%s, node=%s, shell=%s, host_data_dir=%s)",
                        os.environ.get("DOCKER_PYTHON_IMAGE", "python:3.11-slim"),
                        os.environ.get("DOCKER_NODE_IMAGE", "node:20-slim"),
                        os.environ.get("DOCKER_SHELL_IMAGE", "python:3.11-slim"),
                        host_data_dir or "(not set — assumes host-local)")
        else:
            _backend = LocalBackend()
            logger.info("Sandbox backend: local (in-process exec, no container isolation)")
    return _backend

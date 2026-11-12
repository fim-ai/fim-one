"""What code started by the application must never be handed: the Docker daemon.

Access to the Docker daemon is equivalent to root on the host. Two kinds of
code run next to the application and are steered by users: commands of the
``local`` code-execution backend (``shell_exec``, ``python_exec``,
``node_exec``) and stdio MCP servers. When the application runs in a
container that has the daemon's socket mounted, both are refused outright: a
list of forbidden words in a command is not a boundary, the absence of the
socket is.

The default deployment never mounts the socket into the application
container: with ``CODE_EXEC_BACKEND=docker`` it talks to
``docker-socket-proxy`` (see ``core/tool/sandbox/docker_proxy.py``), which
only starts sandbox containers.

Outside a container (a developer running ``./start.sh``) the commands run
as the developer on the developer's machine either way, so nothing is
refused there.
"""

from __future__ import annotations

import logging
import os
import stat
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "docker_socket_paths",
    "exposed_docker_socket",
    "harden_process",
    "local_execution_refusal",
    "running_in_container",
]

# Where a container runtime's control socket is usually found. Any of them
# is the same thing as the Docker daemon's: a way to start a container with
# the host's root mounted.
_WELL_KNOWN_SOCKETS: tuple[str, ...] = (
    "/var/run/docker.sock",
    "/run/docker.sock",
    "/var/run/podman/podman.sock",
    "/run/podman/podman.sock",
    "/var/run/containerd/containerd.sock",
    "/run/containerd/containerd.sock",
    "/run/k3s/containerd/containerd.sock",
    "/var/run/crio/crio.sock",
    "/run/crio/crio.sock",
    "/var/run/cri-dockerd.sock",
    "/run/cri-dockerd.sock",
)
_SOCKET_GLOBS: tuple[str, ...] = (
    "/run/user/*/docker.sock",
    "/run/user/*/podman/podman.sock",
    "/var/run/*/docker.sock",
)
# Variables that say where a runtime listens.
_ENDPOINT_VARIABLES = ("DOCKER_HOST", "CONTAINER_HOST", "CONTAINERD_ADDRESS")

LOCAL_EXECUTION_REFUSED = (
    "Refused: the application container can reach a container runtime ({socket}), "
    "and commands are not started next to one. Remove the socket mount "
    "(the default docker-compose.yml no longer has it) and do not point DOCKER_HOST "
    "at a Docker daemon; code execution with CODE_EXEC_BACKEND=docker goes through "
    "the sandbox proxy."
)

_PROBE_TTL = 60.0
_probed: dict[tuple[str, int], tuple[float, str]] = {}


def running_in_container() -> bool:
    """Whether this process runs inside a container.

    ``FIM_IN_CONTAINER`` (set by the image) decides when present; otherwise
    the marker files container runtimes leave are looked for.
    """
    flag = os.environ.get("FIM_IN_CONTAINER", "").strip().lower()
    if flag in ("1", "true", "yes"):
        return True
    if flag in ("0", "false", "no"):
        return False
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def docker_socket_paths() -> list[str]:
    """Where a runtime's socket would be: the well-known paths and what the environment names."""
    import glob

    paths = list(_WELL_KNOWN_SOCKETS)
    for pattern in _SOCKET_GLOBS:
        paths.extend(sorted(glob.glob(pattern)))
    for variable in _ENDPOINT_VARIABLES:
        value = os.environ.get(variable, "")
        path = value[len("unix://") :] if value.startswith("unix://") else value
        if path.startswith("/") and path not in paths:
            paths.append(path)
    return paths


def _probe(host: str, port: int) -> str:
    """What answers at ``host:port``: ``proxy``, ``daemon``, ``other`` or ``unreachable``.

    The sandbox proxy says who it is in every refusal; a Docker daemon
    answers ``/_ping`` with ``200``. The answer is remembered for a minute.
    """
    import socket
    import time

    key = (host, port)
    known = _probed.get(key)
    if known is not None and known[0] > time.monotonic():
        return known[1]
    kind = "unreachable"
    try:
        with socket.create_connection((host, port), timeout=2) as conn:
            conn.settimeout(2)
            conn.sendall(b"GET /_ping HTTP/1.1\r\nHost: docker\r\nConnection: close\r\n\r\n")
            answer = b""
            while len(answer) < 4096:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                answer += chunk
        if b"sandbox proxy" in answer and not answer.startswith(b"HTTP/1.1 200"):
            kind = "proxy"
        elif answer.startswith((b"HTTP/1.1 200", b"HTTP/1.0 200")):
            kind = "daemon"
        else:
            kind = "other"
    except OSError:
        kind = "unreachable"
    _probed[key] = (time.monotonic() + _PROBE_TTL, kind)
    return kind


def _exposed_endpoint() -> str | None:
    """A runtime endpoint the environment names that is not the sandbox proxy, or ``None``.

    ``tcp://`` is asked what it is: the sandbox proxy is fine (it only
    starts sandbox containers and wants a token these commands do not
    have), a daemon is not, and neither is anything that answers but is
    not the proxy. An address nothing answers at gives nothing away. Any
    other scheme (``ssh://``, ``npipe://``, ``fd://``) is a way to a daemon.
    """
    from urllib.parse import urlsplit

    for variable in _ENDPOINT_VARIABLES:
        value = os.environ.get(variable, "").strip()
        if not value or value.startswith(("unix://", "/")):
            continue  # a socket path: looked at by exposed_docker_socket()
        shown = f"{variable}={value.split('@')[-1][:80]}"
        if not value.startswith(("tcp://", "http://")):
            return shown
        try:
            parts = urlsplit(value)
            host, port = parts.hostname, parts.port or 2375
        except ValueError:
            return shown
        if not host:
            return shown
        if _probe(host, port) in ("daemon", "other"):
            return shown
    return None


def exposed_docker_socket() -> str | None:
    """A container runtime this container can reach, or ``None``.

    A socket file at a known path or one the environment names, or a
    network endpoint the environment names that is not the sandbox proxy.
    Only ever something when running in a container: that is where reaching
    a runtime in reach is refused.
    """
    if not running_in_container():
        return None
    for path in docker_socket_paths():
        try:
            if stat.S_ISSOCK(os.stat(path).st_mode):
                return path
        except OSError:
            continue
    return _exposed_endpoint()


def local_execution_refusal() -> str | None:
    """Why a command may not be started next to the application, or ``None``."""
    socket_path = exposed_docker_socket()
    if socket_path is None:
        return None
    return LOCAL_EXECUTION_REFUSED.format(socket=socket_path)


def harden_process() -> bool:
    """Make this process's ``/proc`` entries unreadable to its own children.

    On Linux, a process that is not *dumpable* has its ``/proc/<pid>/environ``,
    ``mem`` and friends owned by root, and cannot be ptrace-attached by an
    unprivileged process of the same user.  The application's environment
    holds the JWT secret, the credential encryption key and the database
    URL; commands of the ``local`` backend and stdio MCP servers run as the
    same user and could otherwise read them from ``/proc``.

    It does nothing against a process running as root (root reads anything):
    the image runs the application as an unprivileged user.  Returns whether
    the flag was set.
    """
    if not sys.platform.startswith("linux"):
        return False
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        pr_set_dumpable = 4
        if libc.prctl(pr_set_dumpable, 0, 0, 0, 0) != 0:
            logger.warning("Could not mark the process non-dumpable (prctl failed)")
            return False
    except Exception:
        logger.warning("Could not mark the process non-dumpable", exc_info=True)
        return False
    return True

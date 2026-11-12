"""Docker-based sandbox backend.

Executes code and shell commands inside ephemeral Docker containers for
full OS-level isolation.  No module blocklists are needed — the container
boundary IS the security boundary.

Two ways to the Docker daemon:
  - ``DOCKER_HOST=tcp://…`` (the bundled compose deployment): the sandbox
    proxy. The backend makes the few requests itself (``docker_api.py``);
    no Docker CLI is needed, and the image has none.
  - Anything else (a developer's machine): the Docker CLI must be installed
    and the daemon running.
  Images are not pulled ahead of time; the first call may wait for a pull.
  Pre-pull recommended: ``docker pull python:3.11-slim node:20-slim``

Recommended: build and use the custom sandbox image (``Dockerfile.sandbox``)
  which pre-installs common packages (pdfplumber, Pillow, pandas, etc.).
  Without it, agents cannot install packages at runtime because sandbox
  containers have no network.

  Build:  ``docker build -f Dockerfile.sandbox -t fim-sandbox:python .``
  Then set: ``DOCKER_PYTHON_IMAGE=fim-sandbox:python``

Environment variables (all optional):
  DOCKER_PYTHON_IMAGE    — Python image (default: python:3.11-slim)
  DOCKER_NODE_IMAGE      — Node.js image (default: node:20-slim)
  DOCKER_SHELL_IMAGE     — Shell image   (default: python:3.11-slim)
  DOCKER_MEMORY          — Default RAM cap per container (default: 256m)
  DOCKER_CPUS            — Default CPU quota per container (default: 0.5)
  DOCKER_PIDS_LIMIT      — Process limit per container (default 256).
  DOCKER_HOST_DATA_DIR   — Host-side path of the ``/app/data`` volume mount.
                           Required when fim-one runs inside a container:
                           sandbox containers are started by the host's
                           daemon, which names mounts by their host path.
  DOCKER_SANDBOX_USER    — ``uid:gid`` the sandbox containers run as. Unset:
                           the application's own uid:gid when it is not root
                           (so it can manage the files they write), else the
                           image's default user. The proxy has its own
                           setting and refuses any other value.
  DOCKER_SHELL_NETWORK   — A Docker network ``shell_exec`` containers join.
                           Unset (the default): no network at all, like code
                           execution. See the security page before setting it.
  DOCKER_HOST            — ``tcp://…``: the sandbox proxy, see above.
  SANDBOX_PROXY_TOKEN    — The secret the sandbox proxy asks for. It stays in
                           this process: no child is given it.

What is asked of the daemon is exactly: ``docker run --rm [-i] --name
fim-sandbox-<hex> --network=<none|egress network> --memory --cpus
--pids-limit --security-opt no-new-privileges [--user --cap-drop ALL -e
HOME=/tmp] -v <sandbox workspace>:/workspace -w /workspace[/sub] <image>
<argv>``. The proxy refuses anything else, so keep the two in step: a new
flag here needs a rule in ``docker_proxy.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from pathlib import Path, PurePosixPath

from .protocol import SandboxResult

logger = logging.getLogger(__name__)

# Container clean-ups still under way when their run was cancelled.
_cleanups: set[asyncio.Future[None]] = set()



async def finish_cleanups(timeout: float = 10) -> bool:
    """For the application's shutdown: wait a bounded time for sandbox clean-ups under way.

    Both kinds: the CLI path's (``docker kill``) and the proxy path's
    (``docker_api.finish_removals``). Whether all of them ended.
    """
    from .docker_api import finish_removals

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    pending = set(_cleanups)
    if pending:
        _, still = await asyncio.wait(pending, timeout=timeout)
        if still:
            logger.warning(
                "%d sandbox container clean-up(s) did not finish before shutdown", len(still)
            )
    else:
        still = set()
    removed = await finish_removals(max(0.0, deadline - loop.time()))
    return removed and not still


# Default images — overridable via env vars (see __init__.py factory)
_DEFAULT_PYTHON_IMAGE = "python:3.11-slim"
_DEFAULT_NODE_IMAGE = "node:20-slim"
_DEFAULT_SHELL_IMAGE = "python:3.11-slim"

# Map language → (image, entrypoint argv)
_LANGUAGE_CONFIG: dict[str, tuple[str, list[str]]] = {
    "python": (_DEFAULT_PYTHON_IMAGE, ["python"]),
    "javascript": (_DEFAULT_NODE_IMAGE, ["node"]),
}


class DockerBackend:
    """Sandbox backend that runs all code inside Docker containers.

    Security properties (per container):
      - ``--network=none``, for code and for shell commands alike. A shell
        gets a network only when the operator names one (*shell_network*)
      - ``--memory=256m`` RAM cap, ``--cpus=0.5`` CPU cap, a process limit
      - no new privileges; not root and no capabilities when the
        application is not root itself
      - ``--rm`` containers are removed immediately on exit
      - the sandbox's workspace is volume-mounted as ``/workspace``
    """

    def __init__(
        self,
        *,
        python_image: str = _DEFAULT_PYTHON_IMAGE,
        node_image: str = _DEFAULT_NODE_IMAGE,
        shell_image: str = _DEFAULT_SHELL_IMAGE,
        default_memory: str = "256m",
        default_cpu: float = 0.5,
        host_data_dir: str | None = None,
        container_data_dir: str = "/app/data",
        sandbox_base: Path | None = None,
        sandbox_user: str | None = None,
        shell_network: str | None = None,
        pids_limit: int = 256,
        proxy_token: str | None = None,
        proxy_endpoint: str | None = None,
    ) -> None:
        self._images = {
            "python": python_image,
            "javascript": node_image,
        }
        self._shell_image = shell_image
        self._default_memory = default_memory
        self._default_cpu = default_cpu
        # DooD path translation: when fim-one runs inside a container and
        # spawns sandbox containers via the host Docker socket, sandbox paths are
        # container-internal (e.g. /app/data/sandbox/…). The host daemon can't see
        # them.  If host_data_dir is set, we translate /app/data/… → host path.
        self._host_data_dir = host_data_dir
        self._container_data_dir = container_data_dir
        # The directory that holds one sandbox directory per conversation or
        # run. A path under it is mounted by its sandbox directory, never by
        # itself (see ``_mount_for``).
        self._sandbox_base = sandbox_base
        self._sandbox_user = sandbox_user
        self._shell_network = shell_network or None
        self._pids_limit = pids_limit
        self._proxy_token = proxy_token or None
        # ``tcp://host:port`` of the sandbox proxy, when there is one.
        self._proxy_endpoint = proxy_endpoint or None
        # Entrypoints per language (image is per-instance, not global)
        self._entrypoints: dict[str, list[str]] = {
            "python": ["python"],
            "javascript": ["node"],
        }

    def _resolve_host_path(self, container_path: Path) -> str:
        """Translate a container-internal path to a host-visible path.

        When ``host_data_dir`` is configured (DooD deployment), replaces the
        container data prefix (``/app/data``) with the host-side mount path so
        that ``docker run -v`` points to a real directory on the host.

        When not configured (fim-one runs directly on host), the path is
        returned as-is.
        """
        if self._host_data_dir is None:
            return str(container_path)

        path_str = str(container_path)
        if path_str.startswith(self._container_data_dir):
            return self._host_data_dir + path_str[len(self._container_data_dir):]

        logger.warning(
            "Sandbox path %s is outside container data dir %s — "
            "Docker volume mount may fail in DooD mode",
            path_str, self._container_data_dir,
        )
        return path_str

    def _base(self) -> Path:
        if self._sandbox_base is not None:
            return self._sandbox_base
        from fim_one.core.assembly.sandbox import sandbox_base

        return sandbox_base()

    def _mount_for(self, directory: Path) -> tuple[Path, str]:
        """What to mount at ``/workspace`` for *directory*, and the working directory.

        A directory inside a sandbox's workspace
        (``<base>/<sandbox id>/workspace/sub/dir``) is reached by mounting
        the workspace and working in ``/workspace/sub/dir``. What is mounted
        is always the same, fixed path: the workspace is the mount point
        inside the container and its parent ``<sandbox id>`` is never
        mounted, so the mounted path cannot change between runs.

        Raises ``ValueError`` for a directory under the sandbox base that is
        not in a workspace, or that leads out of it through a symlink.

        A directory that is not under the sandbox base (tests, a caller's
        own scratch directory) is mounted as it is.
        """
        base = self._base()
        try:
            relative = directory.relative_to(base)
        except ValueError:
            return directory, "/workspace"
        parts = relative.parts
        if len(parts) < 2 or parts[1] != "workspace" or ".." in parts:
            raise ValueError(f"Not a directory inside a sandbox workspace: {directory}")
        sandbox = base / parts[0]
        root = sandbox / "workspace"
        for trusted in (sandbox, root):
            if trusted.is_symlink():
                raise ValueError(f"Sandbox directory {trusted} is a symlink")
        resolved_root = root.resolve()
        resolved = directory.resolve()
        if resolved != resolved_root and resolved_root not in resolved.parents:
            raise ValueError(f"{directory} leads outside its sandbox")
        inner = PurePosixPath("/workspace", *resolved.relative_to(resolved_root).parts)
        return root, str(inner)

    def _hardening(self) -> list[str]:
        """Flags every sandbox container gets."""
        flags = ["--security-opt", "no-new-privileges", f"--pids-limit={self._pids_limit}"]
        user = self._sandbox_user_flag()
        if user:
            # Not root in the container either, with no capability left, and
            # the files it writes belong to the application. HOME: the
            # image's is root's.
            flags += ["--user", user, "--cap-drop=ALL", "-e", "HOME=/tmp"]
        return flags

    def _sandbox_user_flag(self) -> str | None:
        user = self._sandbox_user
        if user is None and hasattr(os, "getuid") and os.getuid() != 0:
            user = f"{os.getuid()}:{os.getgid()}"
        return user or None

    def _ensure_dir(self, directory: Path) -> None:
        """Create *directory*, crossing no symlink below its workspace."""
        from fim_one.core.security import safe_fs

        root, parts = safe_fs.anchor_of(directory, base=self._base())
        root.mkdir(parents=True, exist_ok=True)
        if parts:
            safe_fs.mkdir(root, parts)

    def _write_script(self, exec_dir: Path, name: str, code: str) -> None:
        """Write the script as a new file, never through a symlink left under its name."""
        from fim_one.core.security import safe_fs

        root, parts = safe_fs.anchor_of(exec_dir, base=self._base())
        safe_fs.write_text(root, (*parts, name), code, exclusive=True)

    async def run_code(
        self,
        code: str,
        *,
        language: str,
        exec_dir: Path,
        timeout: int,
        memory: str | None = None,
        cpu: float | None = None,
    ) -> SandboxResult:
        image = self._images.get(language)
        if image is None:
            return SandboxResult(
                stdout="",
                stderr="",
                exit_code=-1,
                error=f"Unsupported language '{language}' in docker backend.",
            )

        ext = "py" if language == "python" else "js"
        try:
            # Before anything is created or written: where it really leads.
            mount_dir, workdir = self._mount_for(exec_dir)
            self._ensure_dir(exec_dir)
        except (ValueError, OSError) as exc:
            return SandboxResult(stdout="", stderr="", exit_code=-1, error=str(exc))
        script_name = f"script_{uuid.uuid4().hex[:8]}.{ext}"
        script_path = exec_dir / script_name
        try:
            self._write_script(exec_dir, script_name, code)
        except OSError as exc:
            return SandboxResult(stdout="", stderr="", exit_code=-1, error=str(exc))

        entrypoint = self._entrypoints[language]
        container_name = f"fim-sandbox-{uuid.uuid4().hex[:8]}"
        mem_limit = memory or self._default_memory
        cpu_limit = cpu or self._default_cpu

        host_path = self._resolve_host_path(mount_dir)
        cmd = [
            "docker", "run", "--rm",
            "--name", container_name,
            "--network=none",
            f"--memory={mem_limit}", f"--cpus={cpu_limit}",
            *self._hardening(),
            "-v", f"{host_path}:/workspace",
            "-w", workdir,
            image, *entrypoint, f"{workdir}/{script_name}",
        ]

        logger.debug(
            "docker run_code: container=%s image=%s language=%s exec_dir=%s host_path=%s script=%s timeout=%ds memory=%s cpus=%s",
            container_name, image, language, exec_dir, host_path, script_name, timeout, mem_limit, cpu_limit,
        )
        result = await self._run_container(
            cmd,
            stdin_data="",
            timeout=timeout,
            container_name=container_name,
            image=image,
            argv=[*entrypoint, f"{workdir}/{script_name}"],
            workdir=workdir,
            bind=f"{host_path}:/workspace",
            network="none",
            memory=mem_limit,
            cpu=cpu_limit,
        )
        result.script_path = script_path
        return result

    async def run_shell(
        self,
        command: str,
        *,
        sandbox_dir: Path,
        timeout: int,
        memory: str | None = None,
        cpu: float | None = None,
    ) -> SandboxResult:
        try:
            mount_dir, workdir = self._mount_for(sandbox_dir)
            self._ensure_dir(sandbox_dir)
        except (ValueError, OSError) as exc:
            return SandboxResult(stdout="", stderr="", exit_code=-1, error=str(exc))
        container_name = f"fim-sandbox-{uuid.uuid4().hex[:8]}"
        mem_limit = memory or self._default_memory
        cpu_limit = cpu or self._default_cpu

        # No network, like code execution: the outbound rules the
        # application applies to itself do not reach into a container. Only
        # a network the operator named is joined.
        host_path = self._resolve_host_path(mount_dir)
        cmd = [
            "docker", "run", "--rm", "-i",
            "--name", container_name,
            f"--network={self._shell_network or 'none'}",
            f"--memory={mem_limit}", f"--cpus={cpu_limit}",
            *self._hardening(),
            "-v", f"{host_path}:/workspace",
            "-w", workdir,
            self._shell_image, "/bin/sh",
        ]

        logger.debug(
            "docker run_shell: container=%s image=%s sandbox_dir=%s host_path=%s timeout=%ds memory=%s cpus=%s",
            container_name, self._shell_image, sandbox_dir, host_path, timeout, mem_limit, cpu_limit,
        )
        return await self._run_container(
            cmd,
            stdin_data=command,
            timeout=timeout,
            container_name=container_name,
            image=self._shell_image,
            argv=["/bin/sh"],
            workdir=workdir,
            bind=f"{host_path}:/workspace",
            network=self._shell_network or "none",
            memory=mem_limit,
            cpu=cpu_limit,
            stdin=True,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _kill_container(self, container_name: str) -> None:
        """Best effort: stop a sandbox container whose run timed out."""
        if not container_name:
            return
        try:
            killer = await asyncio.create_subprocess_exec(
                "docker", "kill", container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )  # fmt: skip
            await asyncio.wait_for(killer.wait(), timeout=10)
        except (OSError, TimeoutError):
            logger.warning("Could not stop sandbox container %s after its timeout", container_name)

    async def _run_container(
        self,
        cmd: list[str],
        *,
        stdin_data: str,
        timeout: int,
        container_name: str = "",
        image: str = "",
        argv: list[str] | None = None,
        workdir: str = "/workspace",
        bind: str = "",
        network: str = "none",
        memory: str = "256m",
        cpu: float = 0.5,
        stdin: bool = False,
    ) -> SandboxResult:
        """Run a sandbox container, pipe *stdin_data*, and collect output.

        Through the sandbox proxy when there is one (the requests are made
        here, see ``docker_api.py``); otherwise with the Docker CLI and *cmd*.
        """
        if self._proxy_endpoint is not None:
            from .docker_api import ContainerSpec, parse_memory, run_container

            user = self._sandbox_user_flag()
            try:
                spec = ContainerSpec(
                    name=container_name,
                    image=image,
                    argv=list(argv or []),
                    workdir=workdir,
                    bind=bind,
                    network=network,
                    memory=parse_memory(memory),
                    nano_cpus=int(float(cpu) * 10**9),
                    pids_limit=self._pids_limit,
                    user=user,
                    env=["HOME=/tmp"] if user else [],
                    stdin=stdin,
                )
            except ValueError as exc:
                return SandboxResult(stdout="", stderr="", exit_code=-1, error=str(exc))
            return await run_container(
                self._proxy_endpoint,
                self._proxy_token,
                spec,
                stdin_data=stdin_data,
                timeout=timeout,
            )
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return SandboxResult(
                stdout="",
                stderr="",
                exit_code=-1,
                error=(
                    "Docker CLI not found. Install Docker or set "
                    "CODE_EXEC_BACKEND=local to use local execution."
                ),
            )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=stdin_data.encode()),
                timeout=timeout,
            )
        except asyncio.CancelledError:
            # The turn was stopped: neither the CLI nor the container stays.
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            cleanup = asyncio.ensure_future(self._kill_container(container_name))
            _cleanups.add(cleanup)
            cleanup.add_done_callback(_cleanups.discard)
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(cleanup)
            raise
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            logger.debug("docker container=%s timed out", container_name)
            # Killing the CLI leaves the container running; stop it too.
            await self._kill_container(container_name)
            return SandboxResult(
                stdout="", stderr="", exit_code=124, timed_out=True
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        exit_code = proc.returncode or 0

        logger.debug("docker container=%s exited exit_code=%d", container_name, exit_code)

        # Detect OOM kill
        error: str | None = None
        if exit_code == 137:
            error = "Container killed (OOM): memory limit exceeded."
            logger.warning("Docker container OOM killed. cmd=%s", cmd[3:6])

        # Detect missing image (first-run scenario)
        if "Unable to find image" in stderr:
            logger.warning(
                "Docker image not pre-pulled — pulling now. This may add latency. "
                "Run 'docker pull %s' to avoid this delay.", cmd[-2]
            )

        return SandboxResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            error=error,
        )

"""Run one sandbox container through the sandbox proxy, without the Docker CLI.

The proxy asks for a token (``docker_proxy.py``). The application makes the
handful of requests itself, over a plain TCP connection, so the token stays
in its own memory and no child process is given it.

The requests are the ones ``docker run --rm [-i]`` makes: create, attach,
wait, start; a kill when the run times out; a pull when the image is
missing.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import struct
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

from .protocol import SandboxResult

logger = logging.getLogger(__name__)

__all__ = ["ContainerSpec", "finish_removals", "parse_memory", "run_container"]

TOKEN_HEADER = "X-Fim-Sandbox-Token"
_UNITS = {"": 1, "b": 1, "k": 1024, "m": 1024**2, "g": 1024**3}
_REQUEST_TIMEOUT = 30
_PULL_TIMEOUT = 600
# Pauses before a request the proxy turned away as busy (503) is sent again.
_BUSY_RETRIES = (0.2, 0.5, 1.0)
# The most that is kept of a container's stdout, and of its stderr. The rest
# is read and dropped (the container is not blocked), and the result says so.
MAX_STREAM_BYTES = int(os.environ.get("SANDBOX_MAX_OUTPUT_BYTES", str(10 * 1024 * 1024)))
_FAILURES = (
    OSError,
    TimeoutError,
    ValueError,
    asyncio.IncompleteReadError,
    asyncio.LimitOverrunError,
)
# Removals still under way when their run was cancelled: held so they finish.
_removals: set[asyncio.Task[None]] = set()


@dataclass(frozen=True)
class ContainerSpec:
    """One sandbox container, as ``docker_backend.py`` describes it."""

    name: str
    image: str
    argv: list[str]
    workdir: str
    bind: str
    network: str
    memory: int
    nano_cpus: int
    pids_limit: int
    user: str | None
    env: list[str] = field(default_factory=list)
    stdin: bool = False

    def body(self) -> dict[str, Any]:
        host: dict[str, Any] = {
            "Binds": [self.bind],
            "NetworkMode": self.network,
            "Memory": self.memory,
            "NanoCpus": self.nano_cpus,
            "PidsLimit": self.pids_limit,
            "AutoRemove": True,
            "SecurityOpt": ["no-new-privileges"],
            "CapDrop": ["ALL"],
        }
        body: dict[str, Any] = {
            "Image": self.image,
            "Cmd": self.argv,
            "WorkingDir": self.workdir,
            "Env": self.env,
            "AttachStdin": self.stdin,
            "AttachStdout": True,
            "AttachStderr": True,
            "OpenStdin": self.stdin,
            "StdinOnce": self.stdin,
            "HostConfig": host,
        }
        if self.user:
            body["User"] = self.user
        return body


def parse_memory(value: str | int) -> int:
    """``256m`` / ``1g`` / ``512k`` / a number of bytes, as bytes."""
    if isinstance(value, int):
        return value
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([bkmg]?)b?\s*", value.lower())
    if match is None:
        raise ValueError(f"Not a memory size: {value!r}")
    return int(float(match.group(1)) * _UNITS[match.group(2)])


def _dechunk(data: bytes) -> bytes:
    out = b""
    while data:
        line, _, rest = data.partition(b"\r\n")
        try:
            size = int(line.split(b";")[0].strip() or b"0", 16)
        except ValueError:
            break
        if size == 0:
            break
        out += rest[:size]
        data = rest[size + 2 :]
    return out


class _Proxy:
    def __init__(self, endpoint: str, token: str | None) -> None:
        parts = urlsplit(endpoint)
        if parts.scheme != "tcp" or not parts.hostname:
            raise ValueError(f"Not a tcp:// endpoint: {endpoint!r}")
        self._host, self._port = parts.hostname, parts.port or 2375
        self._token = token or ""
        # Requests written to the proxy so far (sent, answered or not).
        self.sent = 0

    def _head(self, method: str, target: str, body: bytes | None, upgrade: bool) -> bytes:
        lines = [f"{method} {target} HTTP/1.1", "Host: docker", f"{TOKEN_HEADER}: {self._token}"]
        if body is not None:
            lines += ["Content-Type: application/json", f"Content-Length: {len(body)}"]
        lines += ["Connection: Upgrade", "Upgrade: tcp"] if upgrade else ["Connection: close"]
        return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + (body or b"")

    async def open(
        self, method: str, target: str, body: bytes | None = None, *, upgrade: bool = False
    ) -> tuple[int, bytes, asyncio.StreamReader, asyncio.StreamWriter]:
        """Send one request; the status, the response head, and the open connection.

        The proxy answers 503 with ``Retry-After`` when it is serving as many
        connections as it takes; nothing was forwarded then, and the request
        is sent again after a short pause, a few times.
        """
        for pause in (*_BUSY_RETRIES, None):
            reader, writer = await asyncio.open_connection(self._host, self._port)
            try:
                writer.write(self._head(method, target, body, upgrade))
                self.sent += 1
                await writer.drain()
                head = await reader.readuntil(b"\r\n\r\n")
                status = int(head.split(b" ", 2)[1])
                busy = status == 503 and b"\r\nretry-after:" in head.lower()
                if busy and pause is not None:
                    writer.close()
                    logger.info("The sandbox proxy is busy; trying again in %.1f s", pause)
                    await asyncio.sleep(pause)
                    continue
                return status, head, reader, writer
            except BaseException:
                writer.close()
                raise
        raise AssertionError("unreachable")  # the last pass always returns

    async def call(
        self, method: str, target: str, body: bytes | None = None, *, timeout: float | None = None
    ) -> tuple[int, bytes]:
        """One request and its whole answer."""

        async def _do() -> tuple[int, bytes]:
            status, head, reader, writer = await self.open(method, target, body)
            try:
                rest = await reader.read()
            finally:
                writer.close()
            chunked = b"transfer-encoding: chunked" in head.lower()
            return status, _dechunk(rest) if chunked else rest

        return await asyncio.wait_for(_do(), timeout or _REQUEST_TIMEOUT)


def _message(payload: bytes) -> str:
    try:
        found = json.loads(payload)
        if isinstance(found, dict) and isinstance(found.get("message"), str):
            return str(found["message"])
    except ValueError:
        pass
    return payload.decode("utf-8", errors="replace")[:300]


async def _demultiplex(
    reader: asyncio.StreamReader, limit: int = MAX_STREAM_BYTES
) -> tuple[bytes, bytes]:
    """Split the attach stream (8-byte frame headers) into stdout and stderr.

    At most *limit* bytes of each are kept. What comes after is read and
    dropped, and a line at the end says how much.
    """
    kept = {1: bytearray(), 2: bytearray()}
    dropped = {1: 0, 2: 0}
    while True:
        try:
            header = await reader.readexactly(8)
        except asyncio.IncompleteReadError:
            break
        kind = 2 if header[0] == 2 else 1
        remaining = struct.unpack(">I", header[4:])[0]
        while remaining:
            chunk = await reader.read(min(remaining, 65536))
            if not chunk:
                remaining = 0
                break
            remaining -= len(chunk)
            room = limit - len(kept[kind])
            kept[kind].extend(chunk[:room] if room > 0 else b"")
            dropped[kind] += max(0, len(chunk) - max(room, 0))

    def finish(kind: int) -> bytes:
        if not dropped[kind]:
            return bytes(kept[kind])
        note = f"\n\n[Output truncated — exceeded {limit // 1024} KB limit]"
        return bytes(kept[kind]) + note.encode()

    return finish(1), finish(2)


async def _remove(proxy: _Proxy, name: str) -> None:
    """Stop and remove the container, whatever state it is in. Never raises.

    A container that does not exist (never created, or gone already) is a
    404, which is fine.
    """
    with contextlib.suppress(*_FAILURES):
        await proxy.call("DELETE", f"/containers/{name}?force=1", timeout=15)


async def finish_removals(timeout: float) -> bool:
    """Wait at most *timeout* seconds for the removals still under way; whether all ended.

    For the application's shutdown: a container whose run ended early is
    not left behind because the process exited during its removal.
    """
    pending = set(_removals)
    if not pending:
        return True
    _, still = await asyncio.wait(pending, timeout=timeout)
    if still:
        logger.warning("%d sandbox container removal(s) did not finish before shutdown", len(still))
    return not still


def _image_and_tag(image: str) -> tuple[str, str]:
    """``repo`` and ``tag`` of an image name; ``latest`` when it names no tag.

    ``registry:5000/team/box`` has a port, not a tag. A digest
    (``repo@sha256:…``) is not a tag: such an image is not pulled here.
    """
    repository, colon, tag = image.rpartition(":")
    if not colon or "/" in tag:
        return image, "latest"
    return repository, tag


async def run_container(
    endpoint: str, token: str | None, spec: ContainerSpec, *, stdin_data: str, timeout: int
) -> SandboxResult:
    """Create, run and wait for the container *spec* describes; its output and exit code."""
    try:
        proxy = _Proxy(endpoint, token)
    except ValueError as exc:
        return SandboxResult(stdout="", stderr="", exit_code=-1, error=str(exc))
    name = quote(spec.name, safe="")
    body = json.dumps(spec.body()).encode()

    def failed(what: str, payload: bytes) -> SandboxResult:
        return SandboxResult(
            stdout="", stderr="", exit_code=-1, error=f"Sandbox {what} failed: {_message(payload)}"
        )

    created = False
    # Set from just before a create is sent until its answer has been read,
    # and left set when the call raised: the container may exist although
    # no answer arrived (the request may have reached the daemon first).
    create_pending = False
    completed = False
    writers: list[asyncio.StreamWriter] = []

    async def _create_once() -> tuple[int, bytes]:
        nonlocal create_pending, created
        create_pending = True
        status, answer = await proxy.call("POST", f"/containers/create?name={name}", body)
        create_pending = False
        created = status == 201
        return status, answer

    async def _create() -> SandboxResult | None:
        status, answer = await _create_once()
        if status == 404 and b"No such image" in answer:
            if "@" in spec.image:
                return failed("image pull", b"a digest-pinned image must be pulled beforehand")
            image, tag = _image_and_tag(spec.image)
            logger.warning("Sandbox image %s is missing; pulling it", spec.image)
            # Always one tag: without one the daemon pulls every tag there is.
            pull = f"/images/create?fromImage={quote(image, safe='')}&tag={quote(tag, safe='')}"
            status, answer = await proxy.call("POST", pull, timeout=_PULL_TIMEOUT)
            if status != 200:
                return failed("image pull", answer)
            status, answer = await _create_once()
        if status != 201:
            return failed("container create", answer)
        return None

    async def _run() -> SandboxResult:
        nonlocal completed
        query = "stream=1&stdout=1&stderr=1" + ("&stdin=1" if spec.stdin else "")
        status, head, reader, attach_writer = await proxy.open(
            "POST", f"/containers/{name}/attach?{query}", upgrade=True
        )
        writers.append(attach_writer)
        if status not in (101, 200):
            return failed("attach", head + await reader.read())
        # Asked before the start (the daemon answers the head at once, the
        # exit code when the container exits): a container that is done
        # within milliseconds is not missed.
        status, wait_head, wait_reader, wait_writer = await proxy.open(
            "POST", f"/containers/{name}/wait?condition=next-exit"
        )
        writers.append(wait_writer)
        if status != 200:
            return failed("wait", wait_head + await wait_reader.read())
        status, answer = await proxy.call("POST", f"/containers/{name}/start")
        if status not in (204, 304):
            return failed("start", answer)

        async def _feed() -> None:
            # While the output is being read: a script that prints before it
            # has read all of its input would otherwise block both sides.
            with contextlib.suppress(OSError):
                attach_writer.write(stdin_data.encode())
                await attach_writer.drain()
                if attach_writer.can_write_eof():
                    attach_writer.write_eof()

        feeder = asyncio.create_task(_feed()) if spec.stdin else None
        try:
            stdout, stderr = await _demultiplex(reader)
        finally:
            if feeder is not None:
                feeder.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await feeder
        answer = await wait_reader.read()
        if b"transfer-encoding: chunked" in wait_head.lower():
            answer = _dechunk(answer)
        try:
            exit_code = int(json.loads(answer).get("StatusCode", -1))
        except (ValueError, AttributeError, TypeError):
            exit_code = -1
        completed = True
        return SandboxResult(
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            exit_code=exit_code,
            error="Container killed (OOM): memory limit exceeded." if exit_code == 137 else None,
        )

    try:
        try:
            refused = await _create()
        except _FAILURES as exc:
            if proxy.sent == 0:
                error = (
                    f"The sandbox proxy at {endpoint} cannot be reached ({type(exc).__name__})."
                )
            else:
                error = (
                    f"The sandbox proxy at {endpoint} stopped answering while the "
                    f"container was being created ({type(exc).__name__})."
                )
            return SandboxResult(stdout="", stderr="", exit_code=-1, error=error)
        if refused is not None:
            return refused
        try:
            return await asyncio.wait_for(_run(), timeout)
        except TimeoutError:
            return SandboxResult(stdout="", stderr="", exit_code=124, timed_out=True)
        except _FAILURES as exc:
            return SandboxResult(
                stdout="",
                stderr="",
                exit_code=-1,
                error=f"Sandbox run failed ({type(exc).__name__}).",
            )
    finally:
        for writer in writers:
            writer.close()
        if (created or create_pending) and not completed:
            # Whatever ended the run early (a refusal after the create, a
            # timeout before or after the start, the turn being cancelled,
            # a create whose answer never came): the container is stopped
            # and removed, by name. Shielded, and held, so
            # that a cancellation does not cancel the removal too. A
            # cancellation that arrives while the removal is awaited is
            # passed on: the removal goes on without the caller.
            removal = asyncio.ensure_future(_remove(proxy, name))
            _removals.add(removal)
            removal.add_done_callback(_removals.discard)
            await asyncio.shield(removal)

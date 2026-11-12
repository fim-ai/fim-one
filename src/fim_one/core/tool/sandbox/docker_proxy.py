"""Sandbox Docker proxy: the only way the application reaches the Docker daemon.

Access to the Docker daemon is equivalent to root on the host. The ``docker``
code-execution backend needs the daemon for one thing: to start the
short-lived sandbox containers ``docker_backend.py`` asks for. This proxy
sits between the two. It holds the daemon's socket; the application
container does not.

**Nothing the client sends reaches the daemon.** A request is parsed
strictly (anything a second parser could read differently is refused), the
few things it may say are validated one by one, and a new request is built
from the validated values: the request line, the query string, the headers
and, for ``containers/create``, the JSON body. The proxy therefore does not
depend on reading a request the same way the daemon would: there is one
accepted form of a request, and what is sent is the proxy's own text.

What a sandbox container is:

- named ``fim-sandbox-<hex>``, from one of the configured sandbox images;
- its only mount is ``<host data dir>/sandbox/<sandbox id>/workspace`` at
  ``/workspace``;
- no network (``none``), or the one egress network the operator named and
  labelled (``SANDBOX_PROXY_EGRESS_NETWORK``); never the default bridge, the
  host's network or another container's;
- hardened: the one non-root user configured here, a memory, CPU and process
  limit within bounds, ``no-new-privileges``, every capability dropped,
  removed when it exits;
- labelled by the proxy (``fim-one.sandbox=1``, and ``fim-one.project=<compose
  project>`` when the proxy knows it), so that a deployment can find its own
  sandboxes; the client sets no label;
- stopped and removed by the proxy once ``SANDBOX_PROXY_MAX_LIFETIME``
  seconds have passed since its create, whatever the client does;
- nothing else: no privileged mode, host namespaces, added capabilities,
  devices, extra mounts, volumes, ports, client labels, runtimes.

Everything else answers ``403``. Containers are addressed by the name the
backend gives them or by an id whose container has such a name, so the
proxy cannot be used to touch any other container.

The client must present the shared token (``X-Fim-Sandbox-Token``). The
application holds it in memory only; the commands it starts (stdio MCP
servers, the local backend) are not given it and cannot read it.

It is a single file with no dependency outside the standard library, so it
runs on its own (``python docker_proxy.py``) without importing the
application.

Environment
-----------
``DOCKER_HOST_DATA_DIR``         Host path of the application's ``data`` directory (required).
``DOCKER_PYTHON_IMAGE`` / ``DOCKER_NODE_IMAGE`` / ``DOCKER_SHELL_IMAGE``
                                 The sandbox images, as the application reads them.
``SANDBOX_PROXY_TOKEN``          The shared token. Unset: read from, or generated into,
``SANDBOX_PROXY_TOKEN_FILE``     this file (default ``/run/fim-sandbox-proxy/token``, mode 0400).
``SANDBOX_PROXY_EXTRA_IMAGES``   More allowed images, comma-separated.
``SANDBOX_PROXY_ALLOW_PULL``     ``false`` to refuse pulling an allowed image (default: allowed).
``SANDBOX_PROXY_EGRESS_NETWORK`` A Docker network sandbox shells may join instead of ``none``.
                                 It must carry the label ``fim-one.sandbox-egress=filtered``
                                 and be created with ``com.docker.network.bridge.enable_icc=false``.
``SANDBOX_PROXY_USER``           The ``uid:gid`` sandbox containers run as (default
                                 ``10001:10001``, the application's user in the image).
``SANDBOX_PROXY_MAX_CONNECTIONS`` Connections with a valid token served at once (default 64).
``SANDBOX_PROXY_MAX_PENDING``    Connections whose token has not been checked yet (default 16);
                                 each has ``5`` s to send its request head.
``SANDBOX_PROXY_MAX_MEMORY``     Upper bound on a container's memory, bytes (default 4 GiB).
``SANDBOX_PROXY_MAX_CPUS``       Upper bound on a container's CPUs (default 4).
``SANDBOX_PROXY_MAX_PIDS``       Upper bound on a container's process count (default 4096).
``SANDBOX_PROXY_MAX_LIFETIME``   Seconds after its create at which a sandbox container is
                                 stopped and removed by the proxy (default 1800). Keep it above
                                 the application's ``SANDBOX_TIMEOUT``. A limit that is pending
                                 when the proxy restarts is not carried over.
``SANDBOX_PROXY_PROJECT``        The compose project the sandboxes are labelled with. Unset:
                                 read from the proxy's own container, when it can be.
``SANDBOX_PROXY_LISTEN``         ``host:port`` to listen on (default ``0.0.0.0:2375``).
``SANDBOX_PROXY_CLIENT_HOSTS``   Host names whose addresses may connect, comma-separated (the
                                 application's service name). Unset: any peer with the token.
``SANDBOX_PROXY_DOCKER_SOCKET``  The daemon's socket (default ``/var/run/docker.sock``).
``CODE_EXEC_BACKEND``            Anything but ``docker``: every request is refused. With the
                                 ``local`` backend nothing has a use for the daemon.

What it does not do: it cannot tell whether the directory it is asked to
mount is a symlink on the host. Only the application can create entries in
``data/sandbox/<id>``, so this is out of reach of sandboxed code and of
users, but not of someone who already controls the application process.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import dataclasses
import hmac
import http.client
import json
import logging
import os
import posixpath
import re
import secrets
import socket
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar
from urllib.parse import quote

logger = logging.getLogger("fim_one.sandbox_proxy")

__all__ = [
    "EGRESS_LABEL",
    "PROJECT_LABEL",
    "SANDBOX_LABEL",
    "TOKEN_HEADER",
    "Policy",
    "Verdict",
    "parse_head",
    "parse_json_strictly",
    "policy_from_env",
    "serve",
]

TOKEN_HEADER = "X-Fim-Sandbox-Token"
EGRESS_LABEL = ("fim-one.sandbox-egress", "filtered")
# Set by the proxy on every sandbox container it creates.
SANDBOX_LABEL = ("fim-one.sandbox", "1")
PROJECT_LABEL = "fim-one.project"
_ICC_OPTION = "com.docker.network.bridge.enable_icc"
_PROJECT_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}")

_MAX_HEAD = 64 * 1024
_MAX_BODY = 1024 * 1024
_T = TypeVar("_T")

_HEAD_TIMEOUT = 30
# For the head of a connection whose token has not been checked yet.
_AUTH_TIMEOUT = 5
# How long a connection that has not sent its head yet keeps its pending
# place whatever arrives after it (seconds since it was accepted).
_EVICT_GRACE = 0.25

_METHODS = ("GET", "HEAD", "POST", "DELETE")
# RFC 9110 token: what a header name may be made of.
_HEADER_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_REQUEST_LINE = re.compile(r"(GET|HEAD|POST|DELETE) (/[!-~]*) HTTP/1\.1")
_PATH = re.compile(r"(?:/v(\d{1,2}\.\d{1,3}))?((?:/[A-Za-z0-9_][A-Za-z0-9_.-]*){1,3})")
_QUERY_NAME = re.compile(r"[A-Za-z]{1,32}")
# What a query value may decode to. Nothing here means anything to a URL parser.
_QUERY_VALUE = re.compile(r"[A-Za-z0-9._:/@-]{0,255}")
_PERCENT = re.compile(r"%([0-9A-Fa-f]{2})")

_SANDBOX_NAME = re.compile(r"fim-sandbox-[0-9a-f]{8,32}")
_CONTAINER_ID = re.compile(r"[0-9a-f]{12,64}")
_SANDBOX_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_USER = re.compile(r"([1-9]\d{0,9})(?::([1-9]\d{0,9}))?")
_NETWORK_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}")
_BOOLS = ("0", "1", "true", "false")

# ``/containers/<ref>[/<action>]``: method, and the query parameters it may
# carry with the values each may take.
_CONTAINER_ACTIONS: dict[tuple[str, str], dict[str, re.Pattern[str] | tuple[str, ...]]] = {
    ("POST", "start"): {},
    ("POST", "attach"): {
        "stream": _BOOLS, "stdin": _BOOLS, "stdout": _BOOLS, "stderr": _BOOLS, "logs": _BOOLS,
    },
    ("POST", "wait"): {"condition": ("not-running", "next-exit", "removed")},
    ("POST", "kill"): {"signal": re.compile(r"[A-Z0-9]{1,12}")},
    ("POST", "stop"): {"t": re.compile(r"\d{1,4}"), "signal": re.compile(r"[A-Z0-9]{1,12}")},
    ("GET", "json"): {},
    ("DELETE", ""): {"force": _BOOLS, "v": _BOOLS},
}  # fmt: skip

_NO_NEW_PRIVILEGES = {"no-new-privileges", "no-new-privileges:true", "no-new-privileges=true"}

# Top-level fields of a create body, lower-cased (the daemon matches names
# without regard to case). A field that is not listed must be empty.
_TOP_FIELDS = {
    "image", "user", "workingdir", "env", "cmd", "entrypoint", "attachstdin", "attachstdout",
    "attachstderr", "openstdin", "stdinonce", "hostconfig", "networkingconfig",
}  # fmt: skip


def _is_empty(value: Any) -> bool:
    """Whether *value* says nothing: null, false, 0, "", or a container with nothing in it.

    A mapping with keys is never empty, whatever the keys map to: in
    ``Volumes``, ``ExposedPorts`` or ``Tmpfs`` the key is the request.
    """
    if value is None or value is False:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float, str)):
        return value in (0, "")
    if isinstance(value, (dict, list, tuple)):
        return len(value) == 0
    return False


def _is_blank(value: Any) -> bool:
    """Whether a record with fixed fields has every field empty (``{"Type": "", "Config": {}}``)."""
    if isinstance(value, dict):
        return all(_is_blank(item) for item in value.values())
    return _is_empty(value)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _int_between(value: Any, low: int, high: int) -> int | None:
    """*value* when it is a whole number in ``low..high``, else ``None``."""
    if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
        return value
    return None


# ---------------------------------------------------------------------------
# Strict parsing: one reading, or none
# ---------------------------------------------------------------------------


class ParseError(ValueError):
    """The request can be read more than one way, or is not one this proxy reads."""


def parse_head(head: bytes) -> tuple[str, str, dict[str, str]]:
    """The method, request target and headers of a request head, read strictly.

    *head* is everything up to and including the first CRLF CRLF. Refused:
    a CR or LF that is not part of a CRLF pair, any other control character
    or non-ASCII byte, a method or version outside the few accepted, a
    header name that is not a token, a folded header, a header given twice.
    The headers come back keyed by lower-cased name.
    """
    if not head.endswith(b"\r\n\r\n"):
        raise ParseError("incomplete request head")
    lines = head[:-4].split(b"\r\n")
    for line in lines:
        # After splitting on CRLF no CR or LF may be left: a line ends at a
        # CRLF pair and nowhere else.
        if any(byte < 0x20 or byte > 0x7E for byte in line):
            raise ParseError("control or non-ASCII character in the request head")
    request_line = _REQUEST_LINE.fullmatch(lines[0].decode("ascii"))
    if request_line is None:
        raise ParseError("malformed request line")
    headers: dict[str, str] = {}
    for raw in lines[1:]:
        name, colon, value = raw.decode("ascii").partition(":")
        if not colon or not _HEADER_NAME.fullmatch(name):
            raise ParseError("malformed header")
        key = name.lower()
        if key in headers:
            raise ParseError(f"header {name!r} given twice")
        headers[key] = value.strip(" ")
    return request_line.group(1), request_line.group(2), headers


def _reject_constant(name: str) -> Any:
    raise ParseError(f"{name} is not JSON")


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """An object whose keys are unique without regard to case, keyed by lower-cased name."""
    found: dict[str, Any] = {}
    for key, value in pairs:
        lowered = key.lower()
        if lowered in found:
            raise ParseError(f"key {key!r} given twice")
        found[lowered] = value
    return found


def parse_json_strictly(body: bytes) -> Any:
    """Parse a JSON body, refusing what two parsers could read differently.

    A key may appear once in an object (at any depth, compared without
    regard to case). Objects come back keyed by lower-cased name.
    """
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise ParseError("body is not UTF-8") from None
    try:
        return json.loads(text, object_pairs_hook=_unique_keys, parse_constant=_reject_constant)
    except ParseError:
        raise
    except (ValueError, RecursionError):
        raise ParseError("body is not JSON") from None


def _parse_target(target: str) -> tuple[str | None, list[str], dict[str, str]]:
    """The API version, path segments and query parameters of a request target.

    Each parameter may be given once. Values are percent-decoded once and
    must then consist of plain characters; ``+`` is not accepted.
    """
    path, question, query = target.partition("?")
    match = _PATH.fullmatch(path)
    if match is None:
        raise ParseError("malformed request path")
    segments = match.group(2).split("/")[1:]
    if any(segment in (".", "..") for segment in segments):
        raise ParseError("malformed request path")
    params: dict[str, str] = {}
    if question and query:
        for pair in query.split("&"):
            name, equals, raw = pair.partition("=")
            if not equals or not _QUERY_NAME.fullmatch(name):
                raise ParseError("malformed query string")
            if name in params:
                raise ParseError(f"query parameter {name!r} given twice")
            if "+" in raw or "%" in _PERCENT.sub("", raw):
                raise ParseError("malformed query string")
            value = _PERCENT.sub(lambda m: chr(int(m.group(1), 16)), raw)
            if not _QUERY_VALUE.fullmatch(value):
                raise ParseError(f"query parameter {name!r} has an unexpected value")
            params[name] = value
    return match.group(1), segments, params


# ---------------------------------------------------------------------------
# The policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    """Whether a request is forwarded and, if it is, the request that is sent.

    ``method``, ``path``, ``query``, ``headers`` and ``body`` are built by
    the proxy from validated values; none of them is the client's text.
    """

    allowed: bool
    reason: str = ""
    # ``stream``: the daemon takes the connection over (attach).
    stream: bool = False
    # A container id whose name must be looked up before forwarding.
    container_id: str | None = None
    # A network whose label must be looked up before forwarding.
    network: str | None = None
    method: str = ""
    path: str = ""
    query: tuple[tuple[str, str], ...] = ()
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes | None = None

    @staticmethod
    def deny(reason: str) -> Verdict:
        return Verdict(False, reason)

    @property
    def target(self) -> str:
        if not self.query:
            return self.path
        return self.path + "?" + "&".join(f"{k}={quote(v, safe='')}" for k, v in self.query)


@dataclass(frozen=True)
class Policy:
    """What the proxy forwards."""

    images: frozenset[str]
    # Host path of ``data/sandbox``; a mount source is ``<this>/<sandbox id>/workspace``.
    sandbox_root: str
    allow_pull: bool = True
    enabled: bool = True
    extra_reason: str = field(default="", compare=False)
    # The shared secret a client must present; ``None`` checks nothing (a
    # policy built in code; ``policy_from_env`` always sets one).
    token: str | None = field(default=None, repr=False)
    egress_network: str | None = None
    # The uid:gid every sandbox container runs as. Set on the proxy's side:
    # the client's value must equal it, it does not choose it.
    sandbox_user: str = "10001:10001"
    max_memory: int = 4 * 1024**3
    max_nano_cpus: int = 4 * 10**9
    max_pids: int = 4096
    # Seconds after the create at which the proxy removes the container.
    max_lifetime: int = 1800
    # Labels the proxy puts on every sandbox container (none by default).
    labels: tuple[tuple[str, str], ...] = ()

    # -- requests ---------------------------------------------------------

    def authorize(self, method: str, target: str, body: bytes) -> Verdict:
        """Decide one request, and build the request to send if it is allowed."""
        if not self.enabled:
            return Verdict.deny(
                self.extra_reason
                or "the sandbox proxy is switched off (CODE_EXEC_BACKEND is not docker)"
            )
        if method not in _METHODS:
            return Verdict.deny("method not allowed")
        try:
            version, segments, params = _parse_target(target)
        except ParseError as exc:
            return Verdict.deny(str(exc))
        prefix = f"/v{version}" if version else ""

        def forward(path: str, query: dict[str, str], **extra: Any) -> Verdict:
            return Verdict(
                True,
                method=method,
                path=prefix + path,
                query=tuple(sorted(query.items())),
                **extra,
            )

        if body and (method, segments) != ("POST", ["containers", "create"]):
            return Verdict.deny("only containers/create takes a body")
        if segments == ["_ping"] and method in ("GET", "HEAD") and not params:
            return forward("/_ping", {})
        if segments == ["version"] and method == "GET" and not params:
            return forward("/version", {})
        if segments == ["containers", "create"] and method == "POST":
            return self._authorize_create(params, body, forward)
        if segments == ["images", "create"] and method == "POST":
            return self._authorize_pull(params, forward)
        if segments[0] == "containers" and len(segments) in (2, 3):
            return self._authorize_container(method, segments, params, forward)
        return Verdict.deny(f"{method} /{'/'.join(segments)} is not something a sandbox needs")

    def _authorize_container(
        self,
        method: str,
        segments: list[str],
        params: dict[str, str],
        forward: Callable[..., Verdict],
    ) -> Verdict:
        ref = segments[1]
        action = segments[2] if len(segments) == 3 else ""
        allowed_params = _CONTAINER_ACTIONS.get((method, action))
        if allowed_params is None:
            return Verdict.deny(f"{method} /{'/'.join(segments)} is not something a sandbox needs")
        for name, value in params.items():
            rule = allowed_params.get(name)
            if rule is None or not (
                value in rule if isinstance(rule, tuple) else rule.fullmatch(value)
            ):
                return Verdict.deny(f"query parameter {name!r} is not allowed here")
        path = f"/containers/{ref}" + (f"/{action}" if action else "")
        stream = action == "attach"
        if _SANDBOX_NAME.fullmatch(ref):
            return forward(path, params, stream=stream)
        if _CONTAINER_ID.fullmatch(ref):
            # Allowed only if that container is a sandbox: the caller looks
            # its name up (``sandbox_name``).
            return forward(path, params, stream=stream, container_id=ref)
        return Verdict.deny("not a sandbox container")

    def _authorize_pull(self, params: dict[str, str], forward: Callable[..., Verdict]) -> Verdict:
        """A pull of one configured image, by repository and tag.

        Always exactly one tag: without one the daemon pulls every tag of
        the repository. A registry with a port (``host:5000/team/box:1``)
        is fine. A digest-pinned image (``repo@sha256:…``) is not pulled
        through the proxy; pull it on the host beforehand. No registry
        credentials are forwarded: a private image is pulled on the host.
        """
        if not self.allow_pull:
            return Verdict.deny("pulling images is switched off (SANDBOX_PROXY_ALLOW_PULL)")
        if set(params) != {"fromImage", "tag"}:
            return Verdict.deny("a pull names one image and one tag, and nothing else")
        image, tag = params["fromImage"], params["tag"]
        if not image or not tag or ":" in tag or "@" in image or "@" in tag:
            return Verdict.deny("a pull names one image and one tag, and nothing else")
        wanted = f"{image}:{tag}"
        if not (wanted in self.images or (tag == "latest" and image in self.images)):
            return Verdict.deny(f"image {wanted!r} is not a configured sandbox image")
        return forward("/images/create", {"fromImage": image, "tag": tag})

    def _authorize_create(
        self, params: dict[str, str], body: bytes, forward: Callable[..., Verdict]
    ) -> Verdict:
        name = params.get("name", "")
        if set(params) - {"name"} or not _SANDBOX_NAME.fullmatch(name):
            return Verdict.deny(
                "a sandbox container must be named fim-sandbox-<hex>, and that is all"
            )
        try:
            config = parse_json_strictly(body)
        except ParseError as exc:
            return Verdict.deny(f"containers/create: {exc}")
        if not isinstance(config, dict):
            return Verdict.deny("containers/create body is not an object")
        built = self.build_container(config)
        if isinstance(built, str):
            return Verdict.deny(built)
        network = built["HostConfig"]["NetworkMode"]
        return forward(
            "/containers/create",
            {"name": name},
            headers=(("Content-Type", "application/json"),),
            body=json.dumps(built, separators=(",", ":"), ensure_ascii=True).encode("ascii"),
            network=None if network == "none" else network,
        )

    # -- the container itself ---------------------------------------------

    def check_container(self, config: dict[str, Any]) -> str | None:
        """Why *config* (a ``containers/create`` body) is not a sandbox container, or ``None``."""
        try:
            strict = parse_json_strictly(json.dumps(config).encode())
        except ParseError as exc:
            return str(exc)
        built = self.build_container(strict)
        return built if isinstance(built, str) else None

    def build_container(self, config: dict[str, Any]) -> dict[str, Any] | str:
        """The create body to send for *config*, or why it is not a sandbox container.

        *config* is a strictly parsed body (keys lower-cased). The result has
        only the fields this proxy sets, each from a validated value.
        """
        for key, value in config.items():
            if key not in _TOP_FIELDS and not _is_empty(value):
                return f"{key} is not allowed"

        image = config.get("image")
        if not isinstance(image, str) or image not in self.images:
            return f"image {image!r} is not a configured sandbox image"
        user = config.get("user")
        if user != self.sandbox_user:
            return f"User must be {self.sandbox_user} (the sandbox user configured on the proxy)"
        workdir = config.get("workingdir") or "/workspace"
        if (
            not isinstance(workdir, str)
            or not (workdir == "/workspace" or workdir.startswith("/workspace/"))
            or posixpath.normpath(workdir) != workdir
            or any(ord(ch) < 0x20 for ch in workdir)
        ):
            return "WorkingDir must be inside /workspace"

        built: dict[str, Any] = {"Image": image, "User": user, "WorkingDir": workdir}
        if self.labels:
            built["Labels"] = dict(self.labels)
        for key, name in (("env", "Env"), ("cmd", "Cmd"), ("entrypoint", "Entrypoint")):
            value = config.get(key)
            if value is None:
                continue
            if not (isinstance(value, list) and all(isinstance(item, str) for item in value)):
                return f"{name} must be a list of strings"
            if any("\x00" in item for item in value):
                return f"{name} must be a list of strings"
            built[name] = list(value)
        for key, name in (
            ("attachstdin", "AttachStdin"),
            ("attachstdout", "AttachStdout"),
            ("attachstderr", "AttachStderr"),
            ("openstdin", "OpenStdin"),
            ("stdinonce", "StdinOnce"),
        ):
            value = config.get(key)
            if value is not None and not isinstance(value, bool):
                return f"{name} must be a boolean"
            built[name] = bool(value)

        host = config.get("hostconfig")
        if not isinstance(host, dict):
            return "HostConfig is required"
        host_built = self._build_host_config(host)
        if isinstance(host_built, str):
            return host_built
        built["HostConfig"] = host_built

        networking = config.get("networkingconfig") or {}
        if not isinstance(networking, dict) or set(networking) - {"endpointsconfig"}:
            return "NetworkingConfig is not allowed"
        endpoints = networking.get("endpointsconfig") or {}
        if not isinstance(endpoints, dict):
            return "NetworkingConfig is not allowed"
        for network, settings in endpoints.items():
            # Keys were lower-cased by the parser; network names are compared as such.
            if network != host_built["NetworkMode"].lower() or not _is_blank(settings):
                return f"network {network!r} is not allowed"
        return built

    def _build_host_config(self, host: dict[str, Any]) -> dict[str, Any] | str:
        handled = {
            "binds", "networkmode", "memory", "nanocpus", "pidslimit", "oomscoreadj",
            "autoremove", "securityopt", "capdrop", "restartpolicy", "logconfig",
            "consolesize", "memoryswappiness", "maskedpaths", "readonlypaths",
        }  # fmt: skip
        for key, value in host.items():
            if key not in handled and not _is_empty(value):
                # Privileged, PidMode, IpcMode, UTSMode, UsernsMode, CapAdd,
                # Devices, DeviceRequests, Mounts, VolumesFrom, PortBindings,
                # PublishAllPorts, Runtime, CgroupParent, Sysctls, ExtraHosts,
                # Links, GroupAdd, Tmpfs, MemorySwap, ... and whatever a
                # later Docker adds.
                return f"HostConfig.{key} is not allowed"
        for key in ("maskedpaths", "readonlypaths"):
            # Empty means "none at all" for these, not "the default".
            if host.get(key) is not None:
                return f"HostConfig.{key} is not allowed"
        if host.get("memoryswappiness") not in (None, -1):
            return "HostConfig.memoryswappiness is not allowed"
        restart = host.get("restartpolicy")
        if restart is not None and not (
            isinstance(restart, dict)
            and restart.get("name") in (None, "", "no")
            and _is_empty(restart.get("maximumretrycount"))
            and not set(restart) - {"name", "maximumretrycount"}
        ):
            return "HostConfig.RestartPolicy is not allowed"
        if not _is_blank(host.get("logconfig")):
            return "HostConfig.LogConfig is not allowed"
        console = host.get("consolesize")
        if console is not None and not (
            isinstance(console, list) and all(_is_int(item) for item in console)
        ):
            return "HostConfig.ConsoleSize must be numbers"

        bind = self._check_binds(host.get("binds"))
        if bind.startswith("!"):
            return bind[1:]
        network = host.get("networkmode")
        if network != "none" and not (
            self.egress_network is not None and network == self.egress_network
        ):
            return f"NetworkMode {network!r} is not allowed: a sandbox has no network" + (
                f" or {self.egress_network!r}" if self.egress_network else ""
            )
        memory = _int_between(host.get("memory"), 1, self.max_memory)
        if memory is None:
            return f"HostConfig.Memory must be set, at most {self.max_memory} bytes"
        cpus = _int_between(host.get("nanocpus"), 1, self.max_nano_cpus)
        if cpus is None:
            return f"HostConfig.NanoCpus must be set, at most {self.max_nano_cpus}"
        pids = _int_between(host.get("pidslimit"), 1, self.max_pids)
        if pids is None:
            return f"HostConfig.PidsLimit must be set, at most {self.max_pids}"
        oom = _int_between(host.get("oomscoreadj") or 0, 0, 1000)
        if oom is None:
            return "HostConfig.OomScoreAdj must not be negative"
        auto_remove = host.get("autoremove")
        if auto_remove is not None and not isinstance(auto_remove, bool):
            return "HostConfig.AutoRemove must be a boolean"
        security = host.get("securityopt")
        if (
            not isinstance(security, list)
            or not security
            or not all(isinstance(item, str) and item in _NO_NEW_PRIVILEGES for item in security)
        ):
            return "HostConfig.SecurityOpt must be no-new-privileges, and only that"
        cap_drop = host.get("capdrop")
        if not (
            isinstance(cap_drop, list)
            and len(cap_drop) == 1
            and isinstance(cap_drop[0], str)
            and cap_drop[0].upper() == "ALL"
        ):
            return "HostConfig.CapDrop must be ALL"
        return {
            "Binds": [bind],
            "NetworkMode": network,
            "Memory": memory,
            "NanoCpus": cpus,
            "PidsLimit": pids,
            "OomScoreAdj": oom,
            # Always: a sandbox container does not outlive its run.
            "AutoRemove": True,
            "SecurityOpt": ["no-new-privileges"],
            "CapDrop": ["ALL"],
        }

    def _check_binds(self, binds: Any) -> str:
        """The bind to send (``<source>:/workspace``), or ``!`` and why there is none."""
        if not isinstance(binds, list) or len(binds) != 1 or not isinstance(binds[0], str):
            return "!exactly one bind mount (the sandbox workspace) is required"
        parts = binds[0].split(":")
        if len(parts) == 2:
            source, target, mode = parts[0], parts[1], ""
        elif len(parts) == 3:
            source, target, mode = parts
        else:
            return "!bind mount is not <source>:/workspace"
        if target != "/workspace" or mode not in ("", "rw"):
            return "!the sandbox workspace must be mounted at /workspace"
        prefix = self.sandbox_root + "/"
        suffix = "/workspace"
        if (
            not source.startswith(prefix)
            or not source.endswith(suffix)
            or posixpath.normpath(source) != source
            or not _SANDBOX_ID.fullmatch(source[len(prefix) : -len(suffix)])
        ):
            return (
                "!bind mount source must be a sandbox workspace "
                f"({self.sandbox_root}/<sandbox id>/workspace)"
            )
        return f"{source}:/workspace"


def _positive_int(env: Any, name: str, default: int) -> int:
    raw = (env.get(name) or "").strip()
    try:
        value = int(float(raw)) if raw else default
    except ValueError:
        raise ValueError(f"{name} must be a number") from None
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def policy_from_env(environ: dict[str, str] | None = None, *, token: str | None = None) -> Policy:
    """The policy the environment describes. Raises ``ValueError`` when it describes none.

    The token is *token*, else ``SANDBOX_PROXY_TOKEN``; without one there is
    no policy.
    """
    env = os.environ if environ is None else environ
    data_dir = (env.get("DOCKER_HOST_DATA_DIR") or "").strip()
    if not data_dir.startswith("/"):
        raise ValueError(
            "DOCKER_HOST_DATA_DIR must be the absolute host path "
            "of the application's data directory"
        )
    token = token or (env.get("SANDBOX_PROXY_TOKEN") or "").strip()
    if len(token) < 32:
        raise ValueError("SANDBOX_PROXY_TOKEN must be set, at least 32 characters")
    images = {
        env.get("DOCKER_PYTHON_IMAGE") or "python:3.11-slim",
        env.get("DOCKER_NODE_IMAGE") or "node:20-slim",
        env.get("DOCKER_SHELL_IMAGE") or "python:3.11-slim",
    }
    images.update(
        item.strip()
        for item in (env.get("SANDBOX_PROXY_EXTRA_IMAGES") or "").split(",")
        if item.strip()
    )
    egress = (env.get("SANDBOX_PROXY_EGRESS_NETWORK") or "").strip() or None
    if egress is not None and (
        not _NETWORK_NAME.fullmatch(egress)
        or egress.lower() in ("none", "host", "bridge", "default")
        or egress.lower().startswith("container")
    ):
        raise ValueError(
            "SANDBOX_PROXY_EGRESS_NETWORK must name a network of its own, "
            "not none, host, bridge, default or a container's"
        )
    user = (env.get("SANDBOX_PROXY_USER") or "10001:10001").strip()
    if not _USER.fullmatch(user) or ":" not in user:
        raise ValueError("SANDBOX_PROXY_USER must be a numeric, non-root uid:gid")
    backend = (env.get("CODE_EXEC_BACKEND") or "docker").strip().lower()
    labels = [SANDBOX_LABEL]
    project = project_label(env.get("SANDBOX_PROXY_PROJECT"))
    if project is not None:
        labels.append(project)
    return Policy(
        images=frozenset(images),
        sandbox_root=posixpath.normpath(data_dir) + "/sandbox",
        allow_pull=(env.get("SANDBOX_PROXY_ALLOW_PULL") or "true").strip().lower()
        not in ("0", "false", "no"),
        enabled=backend == "docker",
        token=token,
        egress_network=egress,
        sandbox_user=user,
        max_memory=_positive_int(env, "SANDBOX_PROXY_MAX_MEMORY", 4 * 1024**3),
        max_nano_cpus=_positive_int(env, "SANDBOX_PROXY_MAX_CPUS", 4) * 10**9,
        max_pids=_positive_int(env, "SANDBOX_PROXY_MAX_PIDS", 4096),
        max_lifetime=_positive_int(env, "SANDBOX_PROXY_MAX_LIFETIME", 1800),
        labels=tuple(labels),
    )


def project_label(project: str | None) -> tuple[str, str] | None:
    """The ``fim-one.project`` label for a compose project name, or ``None``."""
    project = (project or "").strip()
    if not _PROJECT_NAME.fullmatch(project):
        return None
    return (PROJECT_LABEL, project)


def load_or_create_token(path: str) -> str:
    """The token kept in *path*, created (mode 0400) when there is none yet."""
    try:
        with open(path, encoding="ascii") as handle:
            token = handle.read().strip()
        if len(token) >= 32:
            return token
    except OSError:
        pass
    token = secrets.token_hex(32)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(token)
    return token


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 15) -> None:
        super().__init__("docker", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


def _daemon_get(docker_socket: str, path: str) -> Any:
    """The JSON the daemon answers ``GET path`` with, or ``None``."""
    conn = _UnixHTTPConnection(docker_socket)
    try:
        conn.request("GET", path, headers={"Host": "docker"})
        response = conn.getresponse()
        payload = response.read()
        if response.status != 200:
            return None
        return json.loads(payload)
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        conn.close()


def _daemon_delete(docker_socket: str, path: str) -> int | None:
    """``DELETE path`` on the daemon; its status, or ``None`` when it could not be sent."""
    conn = _UnixHTTPConnection(docker_socket)
    try:
        conn.request("DELETE", path, headers={"Host": "docker"})
        response = conn.getresponse()
        response.read()
        return response.status
    except (OSError, http.client.HTTPException):
        return None
    finally:
        conn.close()


# The lifetime limits that are pending, by container Id (by name when the
# daemon's answer to the create did not give the Id).
_limits: dict[str, asyncio.Task[None]] = {}
# Between the attempts of a forced removal that failed (the daemon busy or
# not reachable for a moment).
_REMOVAL_RETRY_PAUSES: tuple[float, ...] = (5.0, 15.0, 45.0)
# How long the daemon may take to answer a create or a removal before the
# answer counts as not known.
_DAEMON_ANSWER_TIMEOUT = 120.0
_CONTAINER_ID = re.compile(r"[0-9a-f]{64}")


async def _end_after(docker_socket: str, ref: str, seconds: float, name: str) -> None:
    """Stop and remove the sandbox container *ref* (Id or name) once *seconds* have passed.

    A container that has exited already is gone (it is removed on exit), and
    the daemon answers 404.
    """
    await asyncio.sleep(seconds)
    target = f"/containers/{quote(ref, safe='')}?force=1"
    for pause in (*_REMOVAL_RETRY_PAUSES, None):
        status = await asyncio.to_thread(_daemon_delete, docker_socket, target)
        if status in (200, 204):
            logger.warning("removed sandbox container %s: it outlived %.0f s", name, seconds)
            return
        if status == 404:
            return
        if pause is None:
            break
        logger.warning(
            "could not remove sandbox container %s after its lifetime (%s); trying again",
            name,
            status if status is not None else "daemon not reachable",
        )
        await asyncio.sleep(pause)
    logger.error("could not remove sandbox container %s after its lifetime", name)


def limit_lifetime(
    docker_socket: str, name: str, seconds: float, container_id: str | None = None
) -> None:
    """Schedule the removal of the sandbox container *name* (see ``_end_after``).

    The limit is kept by *container_id* when the create's answer gave it, so
    that it is that container, and no later one of the same name, that is
    removed, and only a removal of that container drops the limit. Without
    the Id it is kept by the name. A limit already pending under the same
    key is replaced.
    """
    key = container_id or name
    drop_lifetime_limit(key)
    task = asyncio.ensure_future(_end_after(docker_socket, key, seconds, name))
    _limits[key] = task

    def _forget(done: asyncio.Task[None]) -> None:
        if _limits.get(key) is done:
            del _limits[key]

    task.add_done_callback(_forget)


def drop_lifetime_limit(key: str) -> None:
    """Cancel the lifetime limit kept under *key* (container Id or name), if one is pending."""
    task = _limits.pop(key, None)
    if task is not None:
        task.cancel()


async def _sandbox_container(docker_socket: str, ref: str) -> tuple[str, str | None] | None:
    """The name and Id of the container *ref* (Id or name) if it is a sandbox's, else ``None``."""
    found = await asyncio.to_thread(
        _daemon_get, docker_socket, f"/containers/{quote(ref, safe='')}/json"
    )
    name = found.get("Name") if isinstance(found, dict) else None
    if not isinstance(name, str):
        return None
    name = name.lstrip("/")
    if not _SANDBOX_NAME.fullmatch(name):
        return None
    container_id = found.get("Id") if isinstance(found, dict) else None
    if not isinstance(container_id, str) or not _CONTAINER_ID.fullmatch(container_id):
        container_id = None
    return name, container_id


async def sandbox_name(docker_socket: str, container_id: str) -> str | None:
    """The name of the container *container_id* if it is a sandbox's, else ``None``."""
    found = await _sandbox_container(docker_socket, container_id)
    return found[0] if found is not None else None


def _created_id(body: bytes) -> str | None:
    """The Id in the daemon's answer to a create, when it is there in full."""
    try:
        answer = json.loads(body)
    except ValueError:
        return None
    found = answer.get("Id") if isinstance(answer, dict) else None
    return found if isinstance(found, str) and _CONTAINER_ID.fullmatch(found) else None


def _content_length(head: bytes) -> int | None:
    """The Content-Length of a response head, when it has exactly one plain one."""
    values = [
        line.split(b":", 1)[1].strip()
        for line in head.split(b"\r\n")[1:]
        if line.lower().startswith(b"content-length:")
    ]
    if len(values) != 1 or not values[0].isdigit() or len(values[0]) > 8:
        return None
    return int(values[0])


def _status(head: bytes) -> int | None:
    """The status code of a response head, or ``None`` when it has none."""
    parts = head.split(b"\r\n", 1)[0].split(b" ")
    return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None


async def network_is_filtered(docker_socket: str, network: str) -> bool:
    """Whether *network* is the operator's egress network, and only that.

    A user-defined IPv4 bridge carrying the label, created with
    inter-container traffic off (``com.docker.network.bridge.enable_icc=false``:
    two sandboxes on it cannot reach each other), with nothing attached but
    sandbox containers: a network that other services sit on is a way to
    them, whatever its label says.

    Limits of the check: it is made before the create, so a container
    attached between the check and the create is not seen; and the daemon
    lists running endpoints only, so a stopped container that is connected
    to the network is not seen until it starts. Both need someone who can
    already change networks on the host.
    """
    found = await asyncio.to_thread(_daemon_get, docker_socket, f"/networks/{network}")
    if not isinstance(found, dict) or found.get("Name") != network:
        return False
    labels = found.get("Labels")
    options = found.get("Options")
    attached = found.get("Containers") or {}
    return (
        found.get("Driver") == "bridge"
        and not found.get("Ingress")
        and not found.get("EnableIPv6")
        and isinstance(labels, dict)
        and labels.get(EGRESS_LABEL[0]) == EGRESS_LABEL[1]
        and isinstance(options, dict)
        and options.get(_ICC_OPTION) == "false"
        and isinstance(attached, dict)
        and all(
            isinstance(entry, dict)
            and isinstance(entry.get("Name"), str)
            and _SANDBOX_NAME.fullmatch(entry["Name"])
            for entry in attached.values()
        )
    )


class _Peers:
    """Which peer addresses may connect: those the configured host names resolve to."""

    def __init__(self, hosts: list[str], *, ttl: float = 10.0) -> None:
        self._hosts = hosts
        self._ttl = ttl
        self._addresses: set[str] = set()
        self._expires = 0.0

    def _resolve(self) -> set[str]:
        found: set[str] = set()
        for host in self._hosts:
            try:
                found.update(str(info[4][0]) for info in socket.getaddrinfo(host, None))
            except OSError:
                continue
        return found

    async def allows(self, address: str) -> bool:
        if not self._hosts:
            return True
        if address in self._addresses and time.monotonic() < self._expires:
            return True
        # Not known (or stale): look again before refusing, the application
        # container may have been recreated with another address.
        self._addresses = await asyncio.to_thread(self._resolve)
        self._expires = time.monotonic() + self._ttl
        return address in self._addresses

    def known(self, address: str) -> bool:
        """Whether *address* was among the allowed ones when they were last looked up."""
        return address in self._addresses


def _refusal(status: int, message: str) -> bytes:
    body = json.dumps({"message": f"sandbox proxy: {message}"}).encode()
    reason = {
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        413: "Payload Too Large",
        502: "Bad Gateway",
        503: "Service Unavailable",
    }[status]
    # A 503 is the proxy being busy: nothing was forwarded, and the client
    # may send the request again (``docker_api`` does, a few times).
    retry = "Retry-After: 1\r\n" if status == 503 else ""
    return (
        f"HTTP/1.1 {status} {reason}\r\n"
        f"{retry}"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode() + body


@dataclass
class _Request:
    method: str
    target: str
    headers: dict[str, str]
    body: bytes


class _BadRequestError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


async def _read_head(reader: asyncio.StreamReader) -> _Request | None:
    """The strictly read head of one HTTP/1.1 request, or ``None`` at end of stream."""
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), _AUTH_TIMEOUT)
    except asyncio.IncompleteReadError as exc:
        if not exc.partial:
            return None
        raise _BadRequestError(400, "incomplete request") from None
    except (asyncio.LimitOverrunError, TimeoutError):
        raise _BadRequestError(400, "request head too large or too slow") from None
    try:
        method, target, headers = parse_head(head)
    except ParseError as exc:
        raise _BadRequestError(400, str(exc)) from None
    return _Request(method, target, headers, b"")


async def _read_body(reader: asyncio.StreamReader, request: _Request) -> None:
    """Read the body the head announces. Called once the client is known."""
    # One way to say how long the body is, and only one.
    if "transfer-encoding" in request.headers:
        raise _BadRequestError(400, "chunked request bodies are not accepted")
    raw_length = request.headers.get("content-length", "0")
    if not raw_length.isascii() or not raw_length.isdigit() or len(raw_length) > 8:
        raise _BadRequestError(400, "malformed Content-Length")
    length = int(raw_length)
    if length > _MAX_BODY:
        raise _BadRequestError(413, "request body too large")
    if length and request.method != "POST":
        raise _BadRequestError(400, "only POST takes a body")
    if length:
        try:
            request.body = await asyncio.wait_for(reader.readexactly(length), _HEAD_TIMEOUT)
        except (asyncio.IncompleteReadError, TimeoutError):
            raise _BadRequestError(400, "incomplete request body") from None


def _forwarded(verdict: Verdict) -> bytes:
    """The request sent to the daemon, built from the verdict: one request, then nothing more.

    Not one byte of it is the client's: the method is one of four, the
    target is rebuilt from validated segments and parameters, the headers
    are these, and the body is the proxy's own serialisation.
    """
    lines = [f"{verdict.method} {verdict.target} HTTP/1.1", "Host: docker"]
    lines += [f"{name}: {value}" for name, value in verdict.headers]
    body = verdict.body or b""
    if verdict.method == "POST":
        lines.append(f"Content-Length: {len(body)}")
    if verdict.stream:
        lines += ["Connection: Upgrade", "Upgrade: tcp"]
    else:
        lines.append("Connection: close")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + body


async def _copy(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, half_close: bool
) -> None:
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, OSError):
        return
    if half_close:
        with contextlib.suppress(OSError, RuntimeError):
            if writer.can_write_eof():
                writer.write_eof()


async def _drain_client(reader: asyncio.StreamReader) -> None:
    """Wait for the client to go away; whatever it still sends is dropped, never forwarded."""
    with contextlib.suppress(ConnectionError, OSError):
        while await reader.read(65536):
            pass


def _hijacked(head: bytes) -> bool:
    """Whether the daemon answered an attach by taking the connection over."""
    text = head.decode("latin-1").lower()
    status = text.split("\r\n", 1)[0].split(" ")
    return (len(status) > 1 and status[1] == "101") or "application/vnd.docker.raw-stream" in text


async def _handle(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    policy: Policy,
    docker_socket: str,
    peers: _Peers | None = None,
    admit: Callable[[], bool] | None = None,
) -> None:
    upstream_writer: asyncio.StreamWriter | None = None
    try:
        if peers is not None:
            peer = writer.get_extra_info("peername")
            address = str(peer[0]) if peer else ""
            if not await peers.allows(address):
                logger.warning("refused a connection from %s: not the application", address)
                writer.write(_refusal(403, "this client may not use the sandbox proxy"))
                await writer.drain()
                return
        try:
            request = await _read_head(reader)
            if request is None:
                return
            # Who is asking, before anything more is read from them.
            if policy.token is not None and not hmac.compare_digest(
                request.headers.get(TOKEN_HEADER.lower(), "").encode(), policy.token.encode()
            ):
                logger.warning("refused %s: no or wrong token", request.method)
                writer.write(_refusal(401, "the sandbox token is missing or wrong"))
                await writer.drain()
                return
            if admit is not None and not admit():
                # A sandbox run holds two connections while it lasts; beyond
                # the cap a client waits its turn rather than the proxy its
                # memory.
                writer.write(_refusal(503, "too many connections; try again"))
                await writer.drain()
                return
            await _read_body(reader, request)
        except _BadRequestError as exc:
            writer.write(_refusal(exc.status, str(exc)))
            await writer.drain()
            return

        verdict = policy.authorize(request.method, request.target, request.body)
        # The sandbox container the request is about, by name.
        container_name: str | None = None
        # Its Id, where the daemon told it: what a lifetime limit is kept by.
        container_id: str | None = None
        if verdict.allowed and verdict.container_id is not None:
            found = await _sandbox_container(docker_socket, verdict.container_id)
            if found is None:
                verdict = Verdict.deny("not a sandbox container")
            else:
                container_name, container_id = found
        elif verdict.allowed and verdict.method == "DELETE":
            # ``<prefix>/containers/<name>``: the policy let only a sandbox's name through.
            last = verdict.path.rsplit("/", 1)[-1]
            container_name = last if _SANDBOX_NAME.fullmatch(last) else None
            if container_name is not None:
                # Which container the name stands for now: a 404 for an
                # earlier one must not drop the limit of a later one.
                found = await _sandbox_container(docker_socket, container_name)
                container_id = found[1] if found is not None else None
        if (
            verdict.allowed
            and verdict.network is not None
            and not await network_is_filtered(docker_socket, verdict.network)
        ):
            verdict = Verdict.deny(
                f"network {verdict.network!r} is not an IPv4 bridge network labelled "
                f"{EGRESS_LABEL[0]}={EGRESS_LABEL[1]}, created with {_ICC_OPTION}=false, "
                "with only sandbox containers on it"
            )
        if not verdict.allowed:
            # The target is the client's text: only its plain characters are logged.
            shown = re.sub(r"[^A-Za-z0-9/._-]", "?", request.target.split("?")[0])[:120]
            logger.warning("refused %s %s: %s", request.method, shown, verdict.reason)
            writer.write(_refusal(403, verdict.reason))
            await writer.drain()
            return

        try:
            upstream_reader, upstream_writer = await asyncio.open_unix_connection(docker_socket)
        except OSError as exc:
            logger.error("cannot reach the Docker daemon at %s: %s", docker_socket, exc)
            writer.write(_refusal(502, "the Docker daemon cannot be reached"))
            await writer.drain()
            return
        upstream_writer.write(_forwarded(verdict))
        await upstream_writer.drain()
        creates = verdict.method == "POST" and verdict.path.endswith("/containers/create")
        removes = verdict.method == "DELETE" and container_name is not None
        if creates or removes:
            # The daemon's answer decides the lifetime limit; it is read
            # whatever the client does meanwhile, for a bounded time.
            try:
                head = await asyncio.wait_for(
                    upstream_reader.readuntil(b"\r\n\r\n"), _DAEMON_ANSWER_TIMEOUT
                )
            except (
                asyncio.IncompleteReadError,
                asyncio.LimitOverrunError,
                OSError,
                TimeoutError,
            ):
                head = b""
            status = _status(head)
            body = b""
            if creates and status is not None and 200 <= status < 300:
                length = _content_length(head)
                if length is not None and length <= _MAX_HEAD:
                    try:
                        body = await asyncio.wait_for(
                            upstream_reader.readexactly(length), _DAEMON_ANSWER_TIMEOUT
                        )
                    except asyncio.IncompleteReadError as exc:
                        body = exc.partial
                    except (OSError, TimeoutError):
                        body = b""  # what arrived stays buffered and is relayed below
            if creates and (status is None or 200 <= status < 300 or status >= 500):
                # Created, or not known (no answer, or the daemon failing
                # midway): the container does not outlive this, whatever the
                # client does next. A 4xx created nothing.
                limit_lifetime(
                    docker_socket,
                    dict(verdict.query)["name"],
                    policy.max_lifetime,
                    _created_id(body),
                )
            elif removes and container_name is not None:
                if status in (200, 204, 404) and container_id is not None:
                    drop_lifetime_limit(container_id)
                if status in (200, 204):
                    # Removed: the container the name stood for is gone,
                    # whichever it was (a limit kept by name, see above).
                    drop_lifetime_limit(container_name)
            if not head:
                writer.write(_refusal(502, "the Docker daemon did not answer"))
                await writer.drain()
                return
            writer.write(head + body)
            await writer.drain()

        if verdict.stream:
            # Nothing more of the client's is passed on until the daemon has
            # taken the connection over.
            try:
                head = await upstream_reader.readuntil(b"\r\n\r\n")
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
                writer.write(_refusal(502, "the Docker daemon did not answer the attach"))
                await writer.drain()
                return
            writer.write(head)
            await writer.drain()
            if _hijacked(head):
                to_daemon = asyncio.create_task(_copy(reader, upstream_writer, half_close=True))
                try:
                    await _copy(upstream_reader, writer, half_close=False)
                finally:
                    to_daemon.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await to_daemon
                return

        # One request was sent with ``Connection: close``: relay the answer
        # until the daemon is done, and stop early if the client leaves.
        answer = asyncio.create_task(_copy(upstream_reader, writer, half_close=False))
        gone = asyncio.create_task(_drain_client(reader))
        try:
            await asyncio.wait({answer, gone}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (answer, gone):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
    except (ConnectionError, OSError):
        pass
    except Exception:
        logger.exception("request failed")
    finally:
        for closing in (upstream_writer, writer):
            if closing is not None:
                with contextlib.suppress(Exception):
                    closing.close()


def _victim(pending: Mapping[_T, str]) -> _T:
    """Which pending connection gives up its place (*pending*: its peer address, oldest first).

    The oldest one of the peer address that holds the most places.
    """
    held = collections.Counter(pending.values())
    most = max(held.values())
    return next(task for task, address in pending.items() if held[address] == most)


def _evictable(
    pending: Mapping[_T, tuple[str, float]],
    now: float,
    allowed: Callable[[str], bool] | None = None,
) -> _T | None:
    """The pending connection to give up its place, or ``None`` when none may.

    *pending*: its peer address and when it was accepted, oldest first. Only
    one that has had ``_EVICT_GRACE`` to send its head may be chosen; among
    those, one whose address *allowed* does not know comes first.
    """
    aged = {
        task: address
        for task, (address, accepted) in pending.items()
        if now - accepted >= _EVICT_GRACE
    }
    if allowed is not None:
        strangers = {task: address for task, address in aged.items() if not allowed(address)}
        aged = strangers or aged
    return _victim(aged) if aged else None


async def serve(
    policy: Policy,
    *,
    host: str = "0.0.0.0",
    port: int = 2375,
    docker_socket: str = "/var/run/docker.sock",
    client_hosts: list[str] | None = None,
    max_connections: int = 64,
    max_pending: int = 16,
) -> asyncio.Server:
    """Start the proxy and return the listening server.

    Two budgets: *max_pending* connections whose token has not been checked
    yet (each has a few seconds to send its head), and *max_connections*
    connections that presented the token.

    A connection that arrives while the pending places are all taken takes
    the place of a pending connection that has had ``_EVICT_GRACE`` to send
    its head: one whose peer address is not among the allowed client hosts
    when those are set, else the oldest one of the peer address that holds
    the most places (``_victim``); that one is answered 503 and closed. When
    every pending connection is younger than that, the newcomer is answered
    503 and closed at once. The application sends its head as soon as it has
    connected, so a burst of connections right after it cannot push it out,
    and connections that send nothing cannot keep it out for longer than the
    grace period. Nothing beyond the two budgets is held.
    """
    peers = _Peers(client_hosts) if client_hosts else None
    # asyncio drops its own reference to a handler's task when the client's
    # connection is lost; a handler still relaying the daemon's side would be
    # garbage-collected mid-flight. Held here until it is done.
    # Pending: the peer address, the connection and when it was accepted, oldest first.
    pending: dict[asyncio.Task[Any], tuple[str, asyncio.StreamWriter, float]] = {}
    running: set[asyncio.Task[Any]] = set()
    evicted: set[asyncio.Task[Any]] = set()

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is None:  # a handler always runs in a task of its own
            return
        now = time.monotonic()
        if len(pending) >= max_pending:
            victim = _evictable(
                {other: (address, accepted) for other, (address, _, accepted) in pending.items()},
                now,
                peers.known if peers is not None else None,
            )
            if victim is None:
                # Every pending place is a connection that has just arrived:
                # this one is the one turned away, at once.
                with contextlib.suppress(Exception):
                    writer.write(_refusal(503, "too many connections; try again"))
                    writer.close()
                return
            _, victim_writer, _ = pending.pop(victim)
            evicted.add(victim)
            with contextlib.suppress(Exception):
                # Written before the cancel: closing the connection sends it.
                victim_writer.write(_refusal(503, "too many connections; try again"))
            victim.cancel()

        def admit() -> bool:
            pending.pop(task, None)
            if len(running) >= max_connections:
                return False
            running.add(task)
            return True

        peer = writer.get_extra_info("peername")
        pending[task] = (str(peer[0]) if peer else "", writer, now)
        try:
            await _handle(reader, writer, policy, docker_socket, peers, admit)
        except asyncio.CancelledError:
            if task not in evicted:
                raise
            # The cancel was the eviction's, not the server's: the task is
            # not left marked as being cancelled.
            task.uncancel()
        finally:
            pending.pop(task, None)
            running.discard(task)
            evicted.discard(task)

    return await asyncio.start_server(handler, host, port, limit=_MAX_HEAD)


async def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        token = (os.environ.get("SANDBOX_PROXY_TOKEN") or "").strip() or load_or_create_token(
            os.environ.get("SANDBOX_PROXY_TOKEN_FILE") or "/run/fim-sandbox-proxy/token"
        )
        policy = policy_from_env(token=token)
    except (ValueError, OSError) as exc:
        # Stay up and refuse: a container that exits would be restarted in a loop.
        logger.error("%s; every request is refused", exc)
        policy = Policy(frozenset(), "/nonexistent", enabled=False, extra_reason=str(exc))
    listen = os.environ.get("SANDBOX_PROXY_LISTEN") or "0.0.0.0:2375"
    host, _, port = listen.rpartition(":")
    docker_socket = os.environ.get("SANDBOX_PROXY_DOCKER_SOCKET") or "/var/run/docker.sock"
    if policy.enabled and not any(name == PROJECT_LABEL for name, _ in policy.labels):
        # The compose project of the proxy's own container (its host name is
        # its id unless compose sets one).
        found = await asyncio.to_thread(
            _daemon_get, docker_socket, f"/containers/{quote(socket.gethostname(), safe='')}/json"
        )
        config = found.get("Config") if isinstance(found, dict) else None
        labels = config.get("Labels") if isinstance(config, dict) else None
        project = project_label(
            labels.get("com.docker.compose.project") if isinstance(labels, dict) else None
        )
        if project is not None:
            policy = dataclasses.replace(policy, labels=(*policy.labels, project))
        else:
            logger.warning(
                "the compose project is not known (set SANDBOX_PROXY_PROJECT): sandboxes "
                "are labelled %s=%s only",
                *SANDBOX_LABEL,
            )
    client_hosts = [
        item.strip()
        for item in (os.environ.get("SANDBOX_PROXY_CLIENT_HOSTS") or "").split(",")
        if item.strip()
    ]
    server = await serve(
        policy,
        host=host or "0.0.0.0",
        port=int(port),
        docker_socket=docker_socket,
        client_hosts=client_hosts,
        max_connections=_positive_int(os.environ, "SANDBOX_PROXY_MAX_CONNECTIONS", 64),
        max_pending=_positive_int(os.environ, "SANDBOX_PROXY_MAX_PENDING", 16),
    )
    if policy.enabled:
        logger.info(
            "sandbox proxy on %s: images=%s, mounts under %s, pull=%s, egress network=%s, "
            "lifetime=%ds, labels=%s",
            listen, sorted(policy.images), policy.sandbox_root, policy.allow_pull,
            policy.egress_network or "none", policy.max_lifetime, dict(policy.labels),
        )  # fmt: skip
    else:
        logger.info("sandbox proxy on %s: switched off, every request is refused", listen)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        sys.exit(0)

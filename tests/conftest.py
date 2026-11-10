"""Shared pytest fixtures for fim-one tests."""

from __future__ import annotations

import ipaddress
import json
import socket
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest

from fim_one.core.agent import ReActAgent
from fim_one.core.model import ChatMessage, LLMResult
from fim_one.core.tool import BaseTool, ToolRegistry

from .fake_llm import FakeLLM


# ---------------------------------------------------------------------------
# FakeLLM -- the shared scripted LLM, re-exported so tests can keep importing
# it from conftest alongside EchoTool.  Implementation: tests/fake_llm.py.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# EchoTool -- a trivial tool that echoes its input
# ---------------------------------------------------------------------------


class EchoTool(BaseTool):
    """A simple tool that returns its ``text`` argument verbatim."""

    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echoes the input text back."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to echo.",
                },
            },
            "required": ["text"],
        }

    async def run(self, **kwargs: Any) -> str:
        return kwargs.get("text", "")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_llm() -> FakeLLM:
    """Return a ``FakeLLM`` with a single default final-answer response."""
    default_response = LLMResult(
        message=ChatMessage(
            role="assistant",
            content=json.dumps(
                {
                    "type": "final_answer",
                    "reasoning": "No further action needed.",
                    "answer": "42",
                }
            ),
        ),
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )
    return FakeLLM(responses=[default_response])


@pytest.fixture()
def tool_registry() -> ToolRegistry:
    """Return a ``ToolRegistry`` pre-loaded with an ``EchoTool``."""
    registry = ToolRegistry()
    registry.register(EchoTool())
    return registry


@pytest.fixture()
def mock_agent(mock_llm: FakeLLM, tool_registry: ToolRegistry) -> ReActAgent:
    """Return a ``ReActAgent`` wired to the fake LLM and echo tool registry."""
    return ReActAgent(llm=mock_llm, tools=tool_registry, max_iterations=5)


@pytest.fixture(autouse=True)
def _human_intervention_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test with the Human Intervention switch at its default (off).

    The switch is read from the environment, so a developer's exported
    ``WORKFLOW_HUMAN_INTERVENTION_ENABLED`` would otherwise decide what the
    workflow tests see.
    """
    monkeypatch.delenv("WORKFLOW_HUMAN_INTERVENTION_ENABLED", raising=False)


@pytest.fixture(autouse=True)
def _fresh_refusal_throttle() -> Iterator[None]:
    """Start every test with nothing counted by the audit refusal throttle.

    It is per process: without this, the tenth failed sign-in for an unknown
    account anywhere in the run would decide what a later test sees.
    """
    from fim_one.core.audit import refusals

    refusals._throttle.reset()
    refusals._summary_pending.clear()
    yield
    refusals._throttle.reset()
    refusals._summary_pending.clear()


@pytest.fixture()
def human_intervention_enabled(
    _human_intervention_default: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Switch Human Intervention workflow nodes on (they are off by default)."""
    monkeypatch.setenv("WORKFLOW_HUMAN_INTERVENTION_ENABLED", "true")


@pytest.fixture(autouse=True, scope="session")
def _uploads_outside_the_checkout(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Point ``UPLOADS_DIR`` at the test session's temporary directory.

    ``create_app()`` creates the uploads directory; with the default
    (``uploads`` in the working directory) every test that builds the app
    left one in the checkout. Session-scoped, so module-scoped app fixtures
    see it too. A test that sets ``UPLOADS_DIR`` itself still decides.
    """
    patch = pytest.MonkeyPatch()
    patch.setenv("UPLOADS_DIR", str(tmp_path_factory.getbasetemp() / "uploads"))
    yield
    patch.undo()


class RealNetworkBlocked(OSError):
    """A test tried to connect to a host other than this machine."""


def _host_name(host: object) -> str:
    if isinstance(host, bytes | bytearray):
        return bytes(host).decode("ascii", "replace")
    return str(host)


def _is_local_host(host: object) -> bool:
    """Whether *host* names this machine (or nothing, as when binding)."""
    if host is None:
        return True
    name = _host_name(host).split("%", 1)[0].strip("[]").lower()
    if name in ("", "localhost"):
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


@pytest.fixture(autouse=True, scope="session")
def _no_real_network() -> Iterator[None]:
    """Refuse every connection that would leave this machine.

    Tests mock the services they talk to, but a fixture row is real
    configuration: a Feishu channel a test happens to send through would
    post to ``open.feishu.cn``, an email channel would dial its SMTP host.
    With this in place such a send fails (``RealNetworkBlocked``, an
    ``OSError``, so the code under test reports it like any unreachable
    host) and nothing goes out. Loopback and unix sockets stay open for
    local listeners and a local test database. Name lookups are left
    alone: the outbound-address checks resolve the hosts they vet.

    The operating system's proxy settings are ignored as well: httpx and
    urllib read them (on macOS and Windows) when no proxy variable is set,
    and a local proxy is a loopback address that would carry the request
    out. ``NO_PROXY=*`` is set for the session so no client picks one up.
    """
    patch = pytest.MonkeyPatch()
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_sendto = socket.socket.sendto

    def _check(address: object) -> None:
        # AF_INET / AF_INET6 addresses are tuples; a unix socket path is not.
        if isinstance(address, tuple) and address and not _is_local_host(address[0]):
            raise RealNetworkBlocked(f"tests may not connect to {_host_name(address[0])!r}")

    def connect(self: socket.socket, address: Any) -> None:
        _check(address)
        real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        _check(address)
        return real_connect_ex(self, address)

    def sendto(self: socket.socket, *args: Any) -> int:
        _check(args[-1])
        return real_sendto(self, *args)

    # ``NO_PROXY=*`` is what the HTTP clients themselves honour (httpx binds
    # ``getproxies`` at import, so replacing the function alone misses it);
    # with a proxy variable present the system settings are not consulted.
    patch.setenv("NO_PROXY", "*")
    patch.setenv("no_proxy", "*")
    patch.setattr(urllib.request, "getproxies", urllib.request.getproxies_environment)
    patch.setattr(socket.socket, "connect", connect)
    patch.setattr(socket.socket, "connect_ex", connect_ex)
    patch.setattr(socket.socket, "sendto", sendto)
    yield
    patch.undo()


@pytest.fixture(autouse=True)
def _assembly_ports() -> None:
    """Register the assembly layer's ports the way the app does at start-up.

    The real web implementations, so a test that replaces one of their
    ingredients (the usage numbers, the plan check) sees it take effect. A
    test of the unregistered state unregisters the port itself.
    """
    from fim_one.web.services.assembly_ports import register_assembly_ports

    register_assembly_ports()

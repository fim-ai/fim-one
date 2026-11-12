"""An API connector refuses an internal host and an unresolvable one alike.

Both answers are the one ``http_request`` gives, without a repeated prefix;
which case it was (and the address a name resolved to) goes to the server
log only.
"""

from __future__ import annotations

import logging
import socket
from typing import Any

import pytest

from fim_one.core.security import ssrf
from fim_one.core.tool.builtin.http_request import HttpRequestTool
from fim_one.core.tool.connector.adapter import ConnectorToolAdapter
from fim_one.core.tool.connector.meta_tool import (
    ActionStub,
    ConnectorMetaTool,
    ConnectorStub,
)

_INTERNAL = "intranet.example.com"
_UNRESOLVED = "nowhere.example.com"
_PUBLIC = "api.example.com"


@pytest.fixture(autouse=True)
def _dns(monkeypatch: pytest.MonkeyPatch) -> None:
    def _getaddrinfo(host: str, *_: Any, **__: Any) -> list[Any]:
        if host == _INTERNAL:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 0))]
        if host == _PUBLIC:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", _getaddrinfo)


def _adapter(host: str) -> ConnectorToolAdapter:
    return ConnectorToolAdapter(
        connector_name="crm",
        connector_base_url=f"https://{host}",
        connector_auth_type="none",
        connector_auth_config=None,
        action_name="list_items",
        action_description="list",
        action_method="GET",
        action_path="/items",
        action_parameters_schema=None,
        action_request_body_template=None,
        action_response_extract=None,
        action_requires_confirmation=False,
        on_call_complete=None,
    )


def _meta_tool(host: str) -> Any:
    action = ActionStub(
        name="list_items", description="list", method="GET", path="/items",
        parameters_schema=None, request_body_template=None, response_extract=None,
        requires_confirmation=False, action_id="act-1",
    )
    return ConnectorMetaTool(
        [ConnectorStub(name="crm", description="", action_count=1, actions=[action])],
        connector_configs={"crm": {"base_url": f"https://{host}", "auth_type": "none"}},
    )


def _expected(host: str) -> str:
    return f"[Error] SSRF blocked: the host '{host}' does not resolve to a public address."


class TestOneAnswer:
    @pytest.mark.parametrize("host", [_INTERNAL, _UNRESOLVED])
    async def test_the_meta_tool(self, host: str) -> None:
        result = await _meta_tool(host).run(
            subcommand="execute", connector="crm", action="list_items", parameters={}
        )
        assert result == _expected(host)

    @pytest.mark.parametrize("host", [_INTERNAL, _UNRESOLVED])
    async def test_the_same_as_http_request(self, host: str) -> None:
        assert await HttpRequestTool().run(url=f"https://{host}/items") == _expected(host)

    async def test_the_two_cases_differ_only_by_name(self) -> None:
        async def answer(host: str) -> str:
            result = await _meta_tool(host).run(
                subcommand="execute", connector="crm", action="list_items", parameters={}
            )
            return str(result).replace(host, "HOST")

        internal, unresolved = await answer(_INTERNAL), await answer(_UNRESOLVED)
        assert internal == unresolved
        assert internal.count("SSRF blocked") == 1

    async def test_the_address_goes_to_the_log_only(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger=ssrf.logger.name):
            result = await _meta_tool(_INTERNAL).run(
                subcommand="execute", connector="crm", action="list_items", parameters={}
            )
        assert "10.1.2.3" not in result
        assert "10.1.2.3" in caplog.text


class TestBuilding:
    @pytest.mark.parametrize("host", [_INTERNAL, _UNRESOLVED])
    def test_neither_host_builds_a_tool(self, host: str) -> None:
        """Both are refused when the tool is built, with the same message."""
        with pytest.raises(ssrf.UnreachableHostError) as refused:
            _adapter(host)
        assert f"[Error] {refused.value}" == _expected(host)

    def test_a_public_host_builds(self) -> None:
        _adapter(_PUBLIC)

    @pytest.mark.parametrize(
        ("url", "message"),
        [("ftp://files.example.com", "Blocked URL scheme"), ("https://", "no hostname")],
    )
    def test_a_malformed_base_url_is_still_refused(self, url: str, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            ConnectorToolAdapter(
                connector_name="crm", connector_base_url=url, connector_auth_type="none",
                connector_auth_config=None, action_name="a", action_description="",
                action_method="GET", action_path="/", action_parameters_schema=None,
                action_request_body_template=None, action_response_extract=None,
                action_requires_confirmation=False, on_call_complete=None,
            )


class TestAtRequestTime:
    """A host that passed when the tool was built and is refused when it is called."""

    @pytest.mark.parametrize("host", [_INTERNAL, _UNRESOLVED])
    async def test_the_same_answer(self, host: str, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = _adapter(_PUBLIC)
        monkeypatch.setattr(adapter, "_base_url", f"https://{host}")
        assert await adapter.run() == _expected(host)

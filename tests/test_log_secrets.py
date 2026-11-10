"""The server log does not keep what a URL authenticates with.

At INFO the HTTP clients log ``HTTP Request: POST <full url>`` for every
request, and the MCP SDK logs the traceback of a failed connection with the
URL in its message. ``core/security/log_secrets.py`` strips the records of
those libraries before any handler sees them.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from fim_one.core.security.log_secrets import FILTERED_LOGGERS, install_url_secret_log_filter

URL = "https://user:PWSECRET@host.example/api/mcp/s/0123456789abcdef0123456789abcdef/mcp?token=TOKSECRET"
SECRETS = ("PWSECRET", "TOKSECRET", "0123456789abcdef0123456789abcdef")
BARE = "https://host.example/api/mcp/s/***/mcp"


@pytest.fixture(autouse=True)
def _installed() -> None:
    install_url_secret_log_filter()


def _logged(caplog: pytest.LogCaptureFixture) -> str:
    """Everything a handler would print: message, traceback and stack."""
    formatter = logging.Formatter("%(name)s %(levelname)s %(message)s")
    return "\n".join(formatter.format(record) for record in caplog.records)


class TestRecordsOfTheHttpClientsAndTheMcpSdk:
    @pytest.mark.parametrize(
        "name",
        [*FILTERED_LOGGERS, "mcp.client.sse", "mcp.client.streamable_http", "httpcore.http11"],
    )
    def test_the_message_and_its_arguments_are_stripped(
        self, name: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger=name):
            logging.getLogger(name).info(
                'HTTP Request: %s %s "%s"', "POST", httpx.URL(URL), "200 OK"
            )

        text = _logged(caplog)
        assert f'HTTP Request: POST {BARE} "200 OK"' in text
        assert not [secret for secret in SECRETS if secret in text]
        # The arguments are gone: a handler formatting them again finds none.
        assert all(record.args is None for record in caplog.records)

    def test_a_traceback_is_stripped_cause_included(self, caplog: pytest.LogCaptureFixture) -> None:
        def _fail() -> None:
            try:
                raise OSError(f"connect to {URL} refused")
            except OSError as cause:
                raise RuntimeError(f"Client error '401 Unauthorized' for url '{URL}'") from cause

        with caplog.at_level(logging.DEBUG, logger="mcp.client.sse"):
            try:
                _fail()
            except RuntimeError:
                logging.getLogger("mcp.client.sse").exception("Error in sse_reader")

        text = _logged(caplog)
        assert "Error in sse_reader" in text and "Traceback" in text
        assert "OSError" in text and "RuntimeError" in text and BARE in text
        assert not [secret for secret in SECRETS if secret in text]
        assert all(record.exc_info is None for record in caplog.records)

    def test_a_message_that_cannot_be_formatted_is_not_printed_with_its_arguments(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="httpx"):
            logging.getLogger("httpx").info("request %d", URL)

        assert not [secret for secret in SECRETS if secret in _logged(caplog)]

    def test_other_loggers_are_left_as_they_are(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.DEBUG, logger="httpxtra"):
            logging.getLogger("httpxtra").info("visit %s", URL)

        (record,) = caplog.records
        assert record.args == (URL,)

    def test_installing_twice_wraps_once(self) -> None:
        before = logging.getLogRecordFactory()
        install_url_secret_log_filter()
        assert logging.getLogRecordFactory() is before


# ---------------------------------------------------------------------------
# Against a server on loopback
# ---------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    mode = "200"

    def log_message(self, *_args: Any) -> None:
        pass

    def do_GET(self) -> None:
        self.send_response(405)
        self.end_headers()

    def do_DELETE(self) -> None:
        self.send_response(200)
        self.end_headers()

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.mode == "401":
            self.send_response(401)
            self.end_headers()
            return
        if "id" not in body:
            self.send_response(202)
            self.end_headers()
            return
        method = body.get("method")
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": body["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "x", "version": "1"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "echo",
                        "description": "d",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                ]
            }
        elif method == "tools/call" and self.mode == "500":
            self.send_response(500)
            self.end_headers()
            return
        else:
            result = {"content": [{"type": "text", "text": "ok"}]}
        out = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture()
def loopback() -> Iterator[tuple[type[_Handler], int]]:
    handler = type("Handler", (_Handler,), {})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield handler, server.server_address[1]
    server.shutdown()
    server.server_close()


class TestAgainstALoopbackServer:
    @pytest.mark.timeout(60)
    @pytest.mark.parametrize("mode", ["200", "401", "500"])
    async def test_an_mcp_connection_leaves_no_secret_in_the_log(
        self, mode: str, loopback: tuple[type[_Handler], int], caplog: pytest.LogCaptureFixture
    ) -> None:
        """200: connect and call. 401: the connection is refused. 500: the call fails."""
        from fim_one.core.mcp import MCPClient

        handler, port = loopback
        handler.mode = mode
        url = f"http://user:PWSECRET@127.0.0.1:{port}/mcp?token=TOKSECRET"
        client = MCPClient()
        with (
            caplog.at_level(logging.DEBUG),
            patch("fim_one.core.mcp.client._validate_url", lambda _url: None),
        ):
            connected = called = False
            try:
                tools = await asyncio.wait_for(
                    client.connect_streamable_http(name="s", url=url, headers={"X": "y"}), 15
                )
                connected = True
                with contextlib.suppress(Exception):
                    called = await asyncio.wait_for(tools[0].run(), 15) == "ok"
            except Exception as exc:
                refused = exc
            finally:
                with contextlib.suppress(BaseException):  # closing is not under test
                    await client.disconnect_all()

        expected = {"200": (True, True), "401": (False, False), "500": (True, False)}
        assert (connected, called) == expected[mode], locals().get("refused")
        text = _logged(caplog)
        # The requests were logged, to the address without its secrets.
        assert f"HTTP Request: POST http://127.0.0.1:{port}/mcp " in text
        assert "PWSECRET" not in text and "TOKSECRET" not in text

    def test_a_plain_http_call_leaves_none_either(
        self, loopback: tuple[type[_Handler], int], caplog: pytest.LogCaptureFixture
    ) -> None:
        """A connector's base URL or a webhook, through the shared HTTP client."""
        _, port = loopback
        # Some library sets this logger's level on import; the app runs it at INFO.
        with caplog.at_level(logging.DEBUG), caplog.at_level(logging.INFO, logger="httpx"):
            response = httpx.get(
                f"http://user:PWSECRET@127.0.0.1:{port}/hook/3f9c1c2e-1d2a-4b6f-9a77-0d6f2b1c9e11",
                params={"key": "TOKSECRET"},
            )

        assert response.status_code == 405
        text = _logged(caplog)
        assert f"HTTP Request: GET http://127.0.0.1:{port}/hook/*** " in text
        for secret in ("PWSECRET", "TOKSECRET", "3f9c1c2e"):
            assert secret not in text


class TestWhereItIsInstalled:
    @pytest.mark.timeout(120)
    @pytest.mark.parametrize("module", ["fim_one.web.app", "fim_one.core.mcp"])
    def test_a_fresh_process_has_it_once_the_module_is_imported(self, module: str) -> None:
        """Each uvicorn worker imports the app; a scheduler or a script the MCP client."""
        code = (
            f"import logging, {module}\n"
            "print(getattr(logging.getLogRecordFactory(), '_fim_strips_url_secrets', False))\n"
        )
        done = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=110, check=False
        )
        assert done.stdout.strip().splitlines()[-1:] == ["True"], done.stderr[-2000:]

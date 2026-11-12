"""What ``convert_to_markdown`` is allowed to read.

The tool's arguments come from the model, so from any document the model
has read.  The property under test: the tool reads **only** (a) a file
the calling user uploaded, found through that user's own upload index and
confined to that user's directory, and (b) http(s) URLs fetched through
the SSRF-safe client, every redirect hop included.  Nothing the model
writes is ever used as a filesystem path.

These tests run against the real ``markitdown`` package.
"""

from __future__ import annotations

import http.server
import json
import socket
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("markitdown")

from fim_one.core.document import markitdown_core
from fim_one.core.security import ssrf
from fim_one.core.tool.base import ToolResult
from fim_one.core.tool.builtin import markitdown_tool
from fim_one.core.tool.builtin.markitdown_tool import MarkItDownTool

_SECRET = "TOPSECRET-b81f-other-users-data"


def _text(result: Any) -> str:
    return str(result.content) if isinstance(result, ToolResult) else str(result)


@pytest.fixture()
def uploads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An uploads tree with two users; the tool is bound to ``alice``."""
    root = tmp_path / "uploads"
    monkeypatch.setenv("UPLOADS_DIR", str(root))
    from fim_one.web.api import files

    monkeypatch.setattr(files, "UPLOAD_ROOT", root)
    for user, body in (("alice", "alice's own notes"), ("bob", _SECRET)):
        d = files._user_dir(user)
        d.mkdir(parents=True, exist_ok=True)
        (d / "f1_notes.txt").write_text(body)
        (d / "index.json").write_text(
            json.dumps({"id-" + user: {"stored_name": "f1_notes.txt", "filename": "notes.txt"}})
        )
    (tmp_path / "outside.txt").write_text(_SECRET)
    return root


@pytest.fixture()
def no_plain_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fail on any HTTP request not made through the SSRF-safe httpx client."""
    seen: list[str] = []
    import requests
    import urllib.request

    def _requests_send(self: Any, request: Any, **kw: Any) -> Any:
        seen.append(f"requests:{request.url}")
        raise AssertionError("requests used for a model-supplied URL")

    def _urlopen(url: Any, *a: Any, **kw: Any) -> Any:
        seen.append(f"urllib:{url}")
        raise AssertionError("urllib used for a model-supplied URL")

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", _requests_send)
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    return seen


class TestLocalSourcesAreRefused:
    """No model-supplied string is ever opened as a path."""

    def _candidates(self, uploads: Path) -> list[str]:
        outside = uploads.parent / "outside.txt"
        bob = uploads / "user_bob" / "f1_notes.txt"
        out: list[str] = []
        for target in (outside, bob):
            t = str(target)
            out += [
                t,
                f"file://{t}",
                f"file:{t}",
                f"FILE://{t}",
                f"File://localhost{t}",
                f"file://localhost{t}",
                f" file://{t}",
                f"\tfile://{t}",
                f"file:///{t.lstrip('/')}",
                f"./{target.name}",
                f"../{target.name}",
                f"{target.parent}/../{target.parent.name}/{target.name}",
                t.replace("/", "//"),
                f"local://{t}",
                f"view-source:file://{t}",
                f"jar:file://{t}!/",
                f"netdoc://{t}",
                f"ftp://127.0.0.1{t}",
                f"gopher://127.0.0.1:70/_{t}",
            ]
        out += [
            "/etc/hosts",
            "/etc/hostname",
            "/var/lib/app/data.txt",
            "/srv/app/settings.cfg",
            "file:///var/lib/app/data.txt",
            "file:///etc/hostname",
            "settings.cfg",
            "../settings.cfg",
            "~/notes.txt",
            "C:\\Windows\\win.ini",
            "\\\\host\\share\\file",
            "data:text/plain;base64,aGVsbG8=",
            "data:text/plain,hello",
            "about:blank",
            "javascript:alert(1)",
            "dict://127.0.0.1:11211/stats",
            "ldap://127.0.0.1/",
            "s3://bucket/key",
            "http:///etc/hostname",
            "https:/etc/hostname",
            "http:etc/hostname",
            "//etc/hostname",
        ]
        return out

    async def test_no_path_or_foreign_scheme_is_read(
        self, uploads: Path, no_plain_network: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        opened: list[str] = []
        real_open = open

        def _spy_open(file: Any, *a: Any, **kw: Any) -> Any:
            opened.append(str(file))
            return real_open(file, *a, **kw)

        monkeypatch.setattr("builtins.open", _spy_open)
        tool = MarkItDownTool(user_id="alice")

        for uri in self._candidates(uploads):
            opened.clear()
            out = _text(await tool.run(uri=uri))
            assert out.startswith("[Error]"), f"{uri!r} was accepted: {out[:120]}"
            assert _SECRET not in out
            # Nothing on disk was opened on behalf of this argument.
            assert opened == [], f"{uri!r} opened {opened}"
        assert no_plain_network == []

    async def test_kernel_refuses_uris(self) -> None:
        for uri in (
            "http://127.0.0.1/",
            "https://example.com/a.pdf",
            "file:///etc/hosts",
            "FILE:///etc/hosts",
            "data:text/plain,hi",
            "ftp://example.com/x",
        ):
            with pytest.raises(ValueError):
                markitdown_core.convert_with_markitdown(uri)

    def test_converter_session_cannot_reach_the_network(self) -> None:
        converter = markitdown_core._build_converter(llm_client=None, llm_model=None)
        for uri in ("http://127.0.0.1:9/", "https://example.com/"):
            with pytest.raises(Exception) as excinfo:
                converter.convert(uri)
            assert "No connection adapters" in str(excinfo.value)


class TestUploadsAreScopedToTheCaller:
    async def test_own_upload_is_converted(self, uploads: Path) -> None:
        out = _text(await MarkItDownTool(user_id="alice").run(file_id="id-alice"))
        assert "alice's own notes" in out

    async def test_other_users_file_id_is_unknown(self, uploads: Path) -> None:
        out = _text(await MarkItDownTool(user_id="alice").run(file_id="id-bob"))
        assert out.startswith("[Error]")
        assert _SECRET not in out

    @pytest.mark.parametrize(
        "stored_name",
        [
            "../user_bob/f1_notes.txt",
            "../../outside.txt",
            "/etc/hosts",
            "..",
            ".",
            "",
            "sub/../../user_bob/f1_notes.txt",
        ],
    )
    async def test_tampered_index_entry_cannot_leave_the_user_dir(
        self, uploads: Path, stored_name: str
    ) -> None:
        index = uploads / "user_alice" / "index.json"
        index.write_text(json.dumps({"x": {"stored_name": stored_name, "filename": "n.txt"}}))
        out = _text(await MarkItDownTool(user_id="alice").run(file_id="x"))
        assert out.startswith("[Error]")
        assert _SECRET not in out

    async def test_absolute_stored_name_outside_is_refused(self, uploads: Path) -> None:
        index = uploads / "user_alice" / "index.json"
        index.write_text(
            json.dumps({"x": {"stored_name": str(uploads.parent / "outside.txt")}})
        )
        out = _text(await MarkItDownTool(user_id="alice").run(file_id="x"))
        assert out.startswith("[Error]")
        assert _SECRET not in out

    async def test_symlink_in_user_dir_is_resolved_before_the_check(
        self, uploads: Path
    ) -> None:
        link = uploads / "user_alice" / "link.txt"
        link.symlink_to(uploads.parent / "outside.txt")
        index = uploads / "user_alice" / "index.json"
        index.write_text(json.dumps({"x": {"stored_name": "link.txt"}}))
        out = _text(await MarkItDownTool(user_id="alice").run(file_id="x"))
        assert out.startswith("[Error]")
        assert _SECRET not in out

    @pytest.mark.parametrize("file_id", ["../user_bob/index.json", "/etc/hosts", "id-bob"])
    async def test_file_id_is_a_key_not_a_path(self, uploads: Path, file_id: str) -> None:
        out = _text(await MarkItDownTool(user_id="alice").run(file_id=file_id))
        assert out.startswith("[Error]")
        assert _SECRET not in out

    async def test_no_user_means_no_uploads_and_no_fallback(self, uploads: Path) -> None:
        out = _text(
            await MarkItDownTool().run(
                file_id="id-bob", uri=str(uploads.parent / "outside.txt")
            )
        )
        assert out.startswith("[Error]")
        assert _SECRET not in out


class _Handler(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []
    redirect_to: str = ""

    def do_GET(self) -> None:  # noqa: N802
        _Handler.hits.append(self.path)
        if self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", _Handler.redirect_to)
            self.end_headers()
            return
        body = f"<html><body><p>{_SECRET}</p></body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture()
def internal_server() -> Iterator[int]:
    """A loopback HTTP service standing in for an internal endpoint."""
    _Handler.hits = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


class TestRemoteSourcesGoThroughTheSsrfGuard:
    async def test_internal_addresses_are_never_requested(
        self, internal_server: int, no_plain_network: list[str]
    ) -> None:
        port = internal_server
        tool = MarkItDownTool(user_id="alice")
        for url in (
            f"http://127.0.0.1:{port}/",
            f"http://localhost:{port}/",
            f"http://[::1]:{port}/",
            f"http://0.0.0.0:{port}/",
            f"http://127.1:{port}/",
            f"http://2130706433:{port}/",
            f"http://0x7f000001:{port}/",
            f"http://[::ffff:127.0.0.1]:{port}/",
            f"HTTP://127.0.0.1:{port}/",
            f"http://user:pw@127.0.0.1:{port}/",
            f"http://example.com@127.0.0.1:{port}/",
            "http://169.254.169.254/latest/meta-data/",
            "http://100.100.100.200/latest/meta-data/",
            "http://10.0.0.1/",
            "http://192.168.1.1/",
            "http://172.16.0.1/",
        ):
            out = _text(await tool.run(uri=url))
            assert out.startswith("[Error]"), f"{url} accepted: {out[:100]}"
            assert _SECRET not in out
        assert _Handler.hits == []
        assert no_plain_network == []

    async def test_redirect_to_internal_address_is_not_followed(
        self,
        internal_server: int,
        no_plain_network: list[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A public host that redirects inward must be stopped at the hop.

        The loopback server plays the public host: name resolution for
        ``public.test`` is pointed at it and that one name is exempted
        from the first-hop check, so the only thing between the request
        and the internal target is the per-hop check on the redirect.
        """
        port = internal_server
        _Handler.redirect_to = f"http://127.0.0.1:{port}/internal"

        real_getaddrinfo = socket.getaddrinfo

        def _getaddrinfo(host: Any, *a: Any, **kw: Any) -> Any:
            if host == "public.test":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.7", 0))]
            return real_getaddrinfo(host, *a, **kw)

        monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)

        # Below the SSRF transport, deliver the "public" address to loopback.
        real_send = httpx.AsyncHTTPTransport.handle_async_request

        async def _routed(self: Any, request: httpx.Request) -> httpx.Response:
            if request.url.host == "203.0.113.7":
                request.url = request.url.copy_with(host="127.0.0.1")
            return await real_send(self, request)

        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _routed)
        assert markitdown_tool.get_safe_async_client is ssrf.get_safe_async_client

        # Sanity: the "public" page itself is reachable through this setup.
        ok = _text(await MarkItDownTool().run(uri=f"http://public.test:{port}/page"))
        assert _SECRET in ok
        _Handler.hits.clear()

        out = _text(await MarkItDownTool().run(uri=f"http://public.test:{port}/redirect"))
        assert out.startswith("[Error]")
        assert _SECRET not in out
        assert _Handler.hits == ["/redirect"]
        assert no_plain_network == []

    async def test_download_uses_the_safe_client_factory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, headers={"content-type": "text/plain"}, content=b"hello world"
            )

        def _factory(**kw: Any) -> httpx.AsyncClient:
            captured.update(kw)
            kw.pop("transport", None)
            return httpx.AsyncClient(transport=httpx.MockTransport(_handler), **kw)

        monkeypatch.setattr(markitdown_tool, "get_safe_async_client", _factory)
        monkeypatch.setattr(markitdown_tool, "validate_url", lambda url: None)

        out = _text(await MarkItDownTool().run(uri="https://docs.example.com/a.txt"))
        assert "hello world" in out
        assert captured["follow_redirects"] is True
        assert "transport" not in captured  # the factory's own SSRF transport

    async def test_oversized_download_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _factory(**kw: Any) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda r: httpx.Response(200, content=b"x" * 4096)
                ),
                **kw,
            )

        monkeypatch.setattr(markitdown_tool, "get_safe_async_client", _factory)
        monkeypatch.setattr(markitdown_tool, "validate_url", lambda url: None)
        monkeypatch.setenv("MARKITDOWN_MAX_DOWNLOAD_BYTES", "1024")

        out = _text(await MarkItDownTool().run(uri="https://docs.example.com/big.bin"))
        assert out.startswith("[Error]")
        assert "limit" in out


class TestConnectorPathParametersKeepTheHost:
    """Sibling tool: a model-supplied path parameter never moves the request
    (and the connector's credentials) to another host."""

    @pytest.mark.parametrize("path", ["{id}/items", "{id}", ".{id}/x", ":{id}/x", "/{id}/items"])
    @pytest.mark.parametrize(
        "value",
        ["@evil.example", ":80@evil.example", "x@evil.example", ".evil.example", "@evil.example:8443", "a?b", "a#b", "a b", "a\r\nHost: x"],
    )
    async def test_request_stays_on_the_configured_origin(
        self, path: str, value: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fim_one.core.tool.connector import adapter as adapter_mod
        from fim_one.core.tool.connector.adapter import ConnectorToolAdapter

        sent: list[httpx.Request] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            return httpx.Response(200, json={"ok": True})

        monkeypatch.setattr(
            adapter_mod,
            "get_safe_async_client",
            lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(_handler), **kw),
        )
        monkeypatch.setattr(adapter_mod, "validate_url", lambda *a, **kw: None)

        tool = ConnectorToolAdapter(
            connector_name="svc",
            connector_base_url="https://api.example.com",
            connector_auth_type="bearer",
            connector_auth_config=None,
            action_name="get",
            action_description="get",
            action_method="GET",
            action_path=path,
            action_parameters_schema=None,
            action_request_body_template=None,
            action_response_extract=None,
            action_requires_confirmation=False,
            auth_credentials={"default_token": "CONNECTOR-SECRET"},
            on_call_complete=None,
        )
        try:
            await tool.run(id=value)
        except ValueError:
            pass
        for request in sent:
            assert request.url.host == "api.example.com"
            assert request.url.scheme == "https"

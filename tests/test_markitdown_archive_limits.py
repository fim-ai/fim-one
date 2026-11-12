"""Document conversion stays within fixed bounds of memory and time.

* A ZIP-based document (``.zip``, Office, EPUB) is refused before it is
  converted when it unpacks beyond the configured total size, number of
  members or nesting depth, counted over every nested archive.
* A URL download has one deadline for the whole transfer.
* The address-checking HTTP client ignores proxy settings and ``.netrc``
  credentials from the environment.
"""

from __future__ import annotations

import asyncio
import io
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from fim_one.core.document.markitdown_core import (
    ArchiveLimitError,
    check_archive_limits,
    convert_bytes_with_markitdown,
    convert_with_markitdown,
)
from fim_one.core.security.ssrf import SSRFSafeTransport, get_safe_async_client


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _nested(levels: int) -> bytes:
    data = _zip({"leaf.txt": b"hello"})
    for level in range(levels - 1):
        data = _zip({f"level{level}.zip": data})
    return data


@pytest.fixture(autouse=True)
def _limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKITDOWN_MAX_UNPACKED_BYTES", str(1024 * 1024))
    monkeypatch.setenv("MARKITDOWN_MAX_ARCHIVE_MEMBERS", "50")
    monkeypatch.setenv("MARKITDOWN_MAX_ARCHIVE_DEPTH", "3")


class TestArchiveLimits:
    def test_within_limits_passes(self) -> None:
        check_archive_limits(_zip({"a.txt": b"x" * 1000, "b/c.txt": b"y"}))
        check_archive_limits(_nested(3))
        check_archive_limits(b"plain text, not an archive")

    def test_unpacked_size_is_counted_over_all_members(self) -> None:
        # Compresses to a few kilobytes.
        bomb = _zip({f"m{i}.txt": b"\0" * 300_000 for i in range(4)})
        assert len(bomb) < 50_000
        with pytest.raises(ArchiveLimitError, match="unpacks to more than"):
            check_archive_limits(bomb)

    def test_nested_archives_count_towards_the_same_totals(self) -> None:
        inner = _zip({f"m{i}.txt": b"\0" * 300_000 for i in range(2)})
        outer = _zip({"a.zip": inner, "b.zip": inner})
        with pytest.raises(ArchiveLimitError, match="unpacks to more than"):
            check_archive_limits(outer)

    def test_member_count(self) -> None:
        with pytest.raises(ArchiveLimitError, match="more than 50 files"):
            check_archive_limits(_zip({f"f{i}.txt": b"" for i in range(51)}))

    def test_nesting_depth(self) -> None:
        with pytest.raises(ArchiveLimitError, match="more than 3 levels"):
            check_archive_limits(_nested(4))

    def test_a_nested_archive_is_found_whatever_its_name(self) -> None:
        inner = b"junk before the archive" + _nested(3)
        with pytest.raises(ArchiveLimitError, match="more than 3 levels"):
            check_archive_limits(_zip({"notes.txt": inner}))

    def test_a_local_file_is_checked_too(self, tmp_path: Path) -> None:
        path = tmp_path / "report.docx"
        path.write_bytes(_zip({f"m{i}.xml": b"<a/>" * 100_000 for i in range(4)}))
        with pytest.raises(ArchiveLimitError):
            check_archive_limits(path)
        with pytest.raises(ArchiveLimitError):
            convert_with_markitdown(path)

    def test_conversion_refuses_before_unpacking(self) -> None:
        with pytest.raises(ArchiveLimitError):
            convert_bytes_with_markitdown(_nested(5), filename="a.zip")


class _Slow(httpx.AsyncByteStream):
    """A body that keeps arriving, one small piece at a time."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(100):
            await asyncio.sleep(0.05)
            yield b"x"

    async def aclose(self) -> None:
        return None


class TestDownloadDeadline:
    async def test_the_whole_download_has_one_deadline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fim_one.core.tool.builtin import markitdown_tool

        def _client(**kwargs: Any) -> httpx.AsyncClient:
            transport = httpx.MockTransport(
                lambda request: httpx.Response(200, stream=_Slow(), headers={"content-type": "text/plain"})
            )
            return get_safe_async_client(transport=transport, **kwargs)

        monkeypatch.setattr(markitdown_tool, "validate_url", lambda url: None)
        monkeypatch.setattr(markitdown_tool, "get_safe_async_client", _client)
        monkeypatch.setattr(markitdown_tool, "_DOWNLOAD_TIMEOUT_SECONDS", 0.5)

        started = asyncio.get_running_loop().time()
        with pytest.raises(markitdown_tool._SourceRefused, match="did not finish"):
            await markitdown_tool.MarkItDownTool._download("https://docs.example.com/a.txt")
        assert asyncio.get_running_loop().time() - started < 3


class TestSafeClientIgnoresTheEnvironment:
    async def test_proxy_variables_and_netrc_are_not_used(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        netrc = tmp_path / "netrc"
        netrc.write_text("machine docs.example.com login someone password hunter2\n")
        netrc.chmod(0o600)
        monkeypatch.setenv("NETRC", str(netrc))
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
        monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")

        client = get_safe_async_client()
        assert client.trust_env is False
        for url in ("https://docs.example.com/", "http://docs.example.com/"):
            assert isinstance(client._transport_for_url(httpx.URL(url)), SSRFSafeTransport)
        await client.aclose()

        seen: list[httpx.Request] = []

        def _record(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200)

        async with get_safe_async_client(transport=httpx.MockTransport(_record)) as mocked:
            await mocked.get("https://docs.example.com/")
        assert "authorization" not in seen[0].headers

    @pytest.mark.parametrize("key", ["proxy", "mounts", "trust_env"])
    def test_proxy_options_are_refused(self, key: str) -> None:
        value: Any = {"proxy": "http://127.0.0.1:9", "mounts": {}, "trust_env": True}[key]
        with pytest.raises(TypeError):
            get_safe_async_client(**{key: value})

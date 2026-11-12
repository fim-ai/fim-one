"""Unit tests for the MarkItDownTool built-in agent tool.

Tests exercise the thin-shell behavior: parameter schema, availability,
error message formatting, and delegation to the kernel. The kernel
itself has its own test module (``test_markitdown_core.py``) — this
file deliberately does NOT re-test conversion logic.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


@pytest.fixture()
def fake_markitdown(monkeypatch: pytest.MonkeyPatch) -> type:
    """Install a fake markitdown module so the tool's availability() passes."""

    class _FakeResult:
        def __init__(self, text_content: str) -> None:
            self.text_content = text_content

    class _FakeMarkItDown:
        return_value: str = "# Fake Markdown"
        should_raise: Exception | None = None

        def __init__(self, **_: Any) -> None:
            pass

        def convert(self, uri: str) -> _FakeResult:
            raise AssertionError("convert() dereferences URIs and must not be used")

        def convert_local(self, path: str, **_: Any) -> _FakeResult:
            if _FakeMarkItDown.should_raise is not None:
                raise _FakeMarkItDown.should_raise
            return _FakeResult(_FakeMarkItDown.return_value)

        def convert_stream(self, stream: Any, **_: Any) -> _FakeResult:
            return self.convert_local("<stream>")

    _FakeMarkItDown.return_value = "# Fake Markdown"
    _FakeMarkItDown.should_raise = None

    fake_module = types.ModuleType("markitdown")
    fake_module.MarkItDown = _FakeMarkItDown  # type: ignore[attr-defined]
    fake_module.StreamInfo = lambda **kw: kw  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "markitdown", fake_module)
    yield _FakeMarkItDown


class TestMarkItDownToolContract:
    """Tool metadata should be stable — the LLM sees these strings."""

    def test_name_and_category(self) -> None:
        from fim_one.core.tool.builtin.markitdown_tool import MarkItDownTool

        tool = MarkItDownTool()
        assert tool.name == "convert_to_markdown"
        assert tool.category == "document"
        assert tool.cacheable is True

    def test_parameters_schema_has_uri_and_file_id(self) -> None:
        from fim_one.core.tool.builtin.markitdown_tool import MarkItDownTool

        tool = MarkItDownTool()
        schema = tool.parameters_schema
        assert schema["type"] == "object"
        assert "uri" in schema["properties"]
        assert "file_id" in schema["properties"]
        # Neither is strictly required — one of the two must be provided
        assert schema["required"] == []

    def test_description_mentions_key_capabilities(self) -> None:
        from fim_one.core.tool.builtin.markitdown_tool import MarkItDownTool

        desc = MarkItDownTool().description.lower()
        for keyword in ("markdown", "pdf", "url", "youtube", "ocr"):
            assert keyword in desc, f"Description missing {keyword!r}"


class TestMarkItDownToolAvailability:
    """availability() tracks whether markitdown is importable."""

    def test_available_when_markitdown_present(
        self, fake_markitdown: type
    ) -> None:
        from fim_one.core.tool.builtin.markitdown_tool import MarkItDownTool

        ok, msg = MarkItDownTool().availability()
        assert ok is True
        assert msg is None

    def test_unavailable_when_markitdown_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fim_one.core.tool.builtin.markitdown_tool import MarkItDownTool

        monkeypatch.setitem(sys.modules, "markitdown", None)
        ok, msg = MarkItDownTool().availability()
        assert ok is False
        assert msg is not None
        assert "markitdown" in msg.lower()


async def _run_url(url: str = "https://example.com/report.pdf") -> Any:
    """Run the tool on a URL with the download step stubbed out."""
    from fim_one.core.tool.builtin.markitdown_tool import MarkItDownTool

    async def _fake_download(uri: str) -> tuple[bytes, str, str | None, str | None, str | None]:
        return b"%PDF-fake", uri, "application/pdf", None, "report.pdf"

    with patch.object(MarkItDownTool, "_download", staticmethod(_fake_download)):
        return await MarkItDownTool().run(uri=url)


class TestMarkItDownToolRun:
    """Async run() method — exercise the thin-shell error/happy paths."""

    async def test_run_empty_uri_returns_error_string(
        self, fake_markitdown: type
    ) -> None:
        from fim_one.core.tool.builtin.markitdown_tool import MarkItDownTool

        result = await MarkItDownTool().run(uri="")
        assert result.startswith("[Error]")
        assert "uri" in result.lower() or "file_id" in result.lower()

    async def test_run_missing_uri_returns_error_string(
        self, fake_markitdown: type
    ) -> None:
        from fim_one.core.tool.builtin.markitdown_tool import MarkItDownTool

        result = await MarkItDownTool().run()
        assert result.startswith("[Error]")

    async def test_run_success_returns_markdown(
        self, fake_markitdown: type
    ) -> None:
        from fim_one.core.tool.builtin.markitdown_tool import MarkItDownTool

        fake_markitdown.return_value = "# Converted\n\nBody text."
        result = await _run_url()
        assert result.content == "# Converted\n\nBody text."

    async def test_run_empty_content_returns_warning(
        self, fake_markitdown: type
    ) -> None:
        from fim_one.core.tool.builtin.markitdown_tool import MarkItDownTool

        fake_markitdown.return_value = ""
        result = await _run_url()
        assert result.startswith("[Warning]")
        assert "no extractable content" in result.lower()

    async def test_run_conversion_error_returns_error_string(
        self, fake_markitdown: type
    ) -> None:
        from fim_one.core.tool.builtin.markitdown_tool import MarkItDownTool

        fake_markitdown.should_raise = RuntimeError("corrupted PDF")
        result = await _run_url()
        assert result.startswith("[Error]")
        assert "corrupted PDF" in result

    async def test_run_without_markitdown_returns_error_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing markitdown should surface as a string, not an exception."""
        from fim_one.core.tool.builtin.markitdown_tool import MarkItDownTool

        monkeypatch.setitem(sys.modules, "markitdown", None)
        result = await _run_url()
        assert result.startswith("[Error]")
        assert "markitdown" in result.lower()


class TestMarkItDownToolFileIdResolution:
    """file_id parameter resolves uploaded files to local paths."""

    async def test_file_id_resolves_to_local_path(
        self, fake_markitdown: type, tmp_path: Path
    ) -> None:
        """file_id should resolve via _load_index and _user_dir."""
        from fim_one.core.tool.builtin.markitdown_tool import MarkItDownTool

        # Create a fake uploaded file on disk
        user_dir = tmp_path / "user_u1"
        user_dir.mkdir()
        stored = user_dir / "abc123_report.pdf"
        stored.write_text("fake pdf content")

        fake_index = {
            "test-file-id": {
                "stored_name": "abc123_report.pdf",
                "filename": "report.pdf",
            }
        }

        fake_markitdown.return_value = "# Resolved File\n\nBody."

        with (
            patch(
                "fim_one.web.api.files._load_index",
                return_value=fake_index,
            ) as mock_load,
            patch(
                "fim_one.web.api.files._user_dir",
                return_value=user_dir,
            ) as mock_udir,
        ):
            tool = MarkItDownTool(user_id="u1")
            result = await tool.run(file_id="test-file-id")

        mock_load.assert_called_once_with("u1")
        mock_udir.assert_called_once_with("u1")
        assert result.content == "# Resolved File\n\nBody."

    async def test_file_id_not_found_in_index(
        self, fake_markitdown: type
    ) -> None:
        """Unknown file_id should return an error message."""
        from fim_one.core.tool.builtin.markitdown_tool import MarkItDownTool

        with patch(
            "fim_one.web.api.files._load_index",
            return_value={},
        ):
            tool = MarkItDownTool(user_id="u1")
            result = await tool.run(file_id="nonexistent-id")

        assert result.startswith("[Error]")
        assert "nonexistent-id" in result

    async def test_file_id_file_missing_on_disk(
        self, fake_markitdown: type, tmp_path: Path
    ) -> None:
        """file_id points to index entry but file was deleted from disk."""
        from fim_one.core.tool.builtin.markitdown_tool import MarkItDownTool

        user_dir = tmp_path / "user_u1"
        user_dir.mkdir()
        # Intentionally do NOT create the file on disk

        fake_index = {
            "gone-id": {
                "stored_name": "deleted_file.pdf",
                "filename": "important.pdf",
            }
        }

        with (
            patch(
                "fim_one.web.api.files._load_index",
                return_value=fake_index,
            ),
            patch(
                "fim_one.web.api.files._user_dir",
                return_value=user_dir,
            ),
        ):
            tool = MarkItDownTool(user_id="u1")
            result = await tool.run(file_id="gone-id")

        assert result.startswith("[Error]")
        assert "not found on disk" in result

    async def test_file_id_without_user_id_is_refused(
        self, fake_markitdown: type
    ) -> None:
        """file_id with no user_id is refused; it never falls back to uri."""
        from fim_one.core.tool.builtin.markitdown_tool import MarkItDownTool

        tool = MarkItDownTool(user_id=None)
        result = await tool.run(file_id="some-id", uri="/etc/hosts")
        assert result.startswith("[Error]")
        assert "not available" in result.lower()

    async def test_file_id_takes_priority_over_uri(
        self, fake_markitdown: type, tmp_path: Path
    ) -> None:
        """When both file_id and uri are provided, file_id wins."""
        from fim_one.core.tool.builtin.markitdown_tool import MarkItDownTool

        user_dir = tmp_path / "user_u1"
        user_dir.mkdir()
        stored = user_dir / "abc_doc.pdf"
        stored.write_text("real content")

        fake_index = {
            "priority-id": {
                "stored_name": "abc_doc.pdf",
                "filename": "doc.pdf",
            }
        }

        fake_markitdown.return_value = "# From file_id"

        with (
            patch(
                "fim_one.web.api.files._load_index",
                return_value=fake_index,
            ),
            patch(
                "fim_one.web.api.files._user_dir",
                return_value=user_dir,
            ),
        ):
            tool = MarkItDownTool(user_id="u1")
            result = await tool.run(
                file_id="priority-id",
                uri="https://wrong-url.example.com/file.pdf",
            )

        # Should have used the resolved path, not the uri
        assert result.content == "# From file_id"

    async def test_file_id_resolution_exception_returns_error(
        self, fake_markitdown: type
    ) -> None:
        """Unexpected exceptions during resolution should surface as errors."""
        from fim_one.core.tool.builtin.markitdown_tool import MarkItDownTool

        with patch(
            "fim_one.web.api.files._load_index",
            side_effect=OSError("disk failure"),
        ):
            tool = MarkItDownTool(user_id="u1")
            result = await tool.run(file_id="crash-id")

        assert result.startswith("[Error]")
        assert "disk failure" in result

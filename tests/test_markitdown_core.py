"""Unit tests for the shared MarkItDown conversion kernel.

The kernel (`fim_one.core.document.markitdown_core.convert_with_markitdown`)
is the single source of truth for file/URL → Markdown inside FIM One. It
is consumed by:

* ``MarkItDownTool`` (Agent-facing)
* ``MarkItDownLoader`` (RAG ingestion)
* ``_extract_text_sync`` in ``processor.py`` (file upload preview)

Tests use a fake ``markitdown`` module injected into ``sys.modules`` so
they run deterministically regardless of whether the real package is
installed. This keeps the test suite self-contained and fast.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Fake markitdown module
# ---------------------------------------------------------------------------


class _FakeResult:
    """Stand-in for ``markitdown.DocumentConverterResult``."""

    def __init__(self, text_content: str) -> None:
        self.text_content = text_content


class _FakeMarkItDown:
    """Stand-in for ``markitdown.MarkItDown``.

    Tests mutate the class-level attributes to control behavior on a
    per-test basis. ``last_init_kwargs`` is the most useful field — it
    captures what the kernel actually passed to the constructor so tests
    can assert vision injection, plugin activation, etc.
    """

    # Set by tests before calling the kernel.
    reject_kwargs: set[str] = set()
    convert_raises: Exception | None = None
    fake_output: str = "# Hello\n\nConverted content"

    # Recorded by the fake on each instantiation / call.
    last_init_kwargs: dict[str, Any] = {}
    convert_call_count: int = 0
    last_uri: str = ""

    def __init__(self, **kwargs: Any) -> None:
        # Simulate older markitdown versions rejecting new kwargs.
        for k in _FakeMarkItDown.reject_kwargs:
            if k in kwargs:
                raise TypeError(f"unexpected keyword argument {k!r}")
        _FakeMarkItDown.last_init_kwargs = dict(kwargs)

    def convert(self, uri: str) -> _FakeResult:
        _FakeMarkItDown.convert_call_count += 1
        _FakeMarkItDown.last_uri = uri
        if _FakeMarkItDown.convert_raises is not None:
            raise _FakeMarkItDown.convert_raises
        return _FakeResult(_FakeMarkItDown.fake_output)


@pytest.fixture()
def fake_markitdown(monkeypatch: pytest.MonkeyPatch) -> type[_FakeMarkItDown]:
    """Install the fake markitdown module in ``sys.modules``.

    Resets class-level state on every test so cross-test leakage is
    impossible. Returns the fake class so tests can inspect
    ``last_init_kwargs`` / ``convert_call_count`` / etc.
    """
    _FakeMarkItDown.reject_kwargs = set()
    _FakeMarkItDown.convert_raises = None
    _FakeMarkItDown.fake_output = "# Hello\n\nConverted content"
    _FakeMarkItDown.last_init_kwargs = {}
    _FakeMarkItDown.convert_call_count = 0
    _FakeMarkItDown.last_uri = ""

    fake_module = types.ModuleType("markitdown")
    fake_module.MarkItDown = _FakeMarkItDown  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "markitdown", fake_module)
    yield _FakeMarkItDown


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestConvertWithMarkitdown:
    """Core kernel behavior."""

    def test_empty_uri_returns_empty_string(
        self, fake_markitdown: type[_FakeMarkItDown]
    ) -> None:
        from fim_one.core.document.markitdown_core import convert_with_markitdown

        assert convert_with_markitdown("") == ""
        assert convert_with_markitdown("   ") == ""
        # Fake should never have been called for empty URIs.
        assert fake_markitdown.convert_call_count == 0

    def test_markitdown_not_installed_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When markitdown is absent, kernel raises a typed error."""
        from fim_one.core.document.markitdown_core import (
            MarkItDownNotInstalledError,
            convert_with_markitdown,
        )

        # Remove any existing markitdown from sys.modules and make future
        # imports fail.
        monkeypatch.setitem(sys.modules, "markitdown", None)

        with pytest.raises(MarkItDownNotInstalledError):
            convert_with_markitdown("/tmp/report.pdf")

    def test_text_only_mode_without_vision_llm(
        self, fake_markitdown: type[_FakeMarkItDown]
    ) -> None:
        """No vision_llm → kernel constructs MarkItDown with plugins but no llm_client."""
        from fim_one.core.document.markitdown_core import convert_with_markitdown

        fake_markitdown.fake_output = "# Report\n\nSome text"
        result = convert_with_markitdown("/tmp/report.docx", vision_llm=None)

        assert result == "# Report\n\nSome text"
        assert fake_markitdown.last_init_kwargs.get("enable_plugins") is True
        assert "llm_client" not in fake_markitdown.last_init_kwargs
        assert "llm_model" not in fake_markitdown.last_init_kwargs

    def test_vision_mode_injects_shim(
        self, fake_markitdown: type[_FakeMarkItDown]
    ) -> None:
        """With a vision_llm, kernel passes a LiteLLMOpenAIShim to MarkItDown."""
        from fim_one.core.document.markitdown_core import convert_with_markitdown
        from fim_one.core.model.litellm_openai_shim import LiteLLMOpenAIShim
        from fim_one.core.model.openai_compatible import OpenAICompatibleLLM

        llm = OpenAICompatibleLLM(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
        )
        convert_with_markitdown("/tmp/report.docx", vision_llm=llm)

        assert fake_markitdown.last_init_kwargs.get("enable_plugins") is True
        assert isinstance(
            fake_markitdown.last_init_kwargs.get("llm_client"), LiteLLMOpenAIShim
        )
        assert fake_markitdown.last_init_kwargs.get("llm_model") == "gpt-4o-mini"

    def test_vision_failure_falls_back_to_text_only(
        self, fake_markitdown: type[_FakeMarkItDown]
    ) -> None:
        """When vision conversion raises, kernel retries without the LLM client."""
        from fim_one.core.document.markitdown_core import convert_with_markitdown
        from fim_one.core.model.openai_compatible import OpenAICompatibleLLM

        llm = OpenAICompatibleLLM(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
        )

        # First call raises; the retry after stripping llm_client succeeds.
        call_state = {"raised": False}

        original_convert = _FakeMarkItDown.convert

        def flaky_convert(self: _FakeMarkItDown, uri: str) -> _FakeResult:
            _FakeMarkItDown.convert_call_count += 1
            _FakeMarkItDown.last_uri = uri
            if not call_state["raised"]:
                call_state["raised"] = True
                raise RuntimeError("vision endpoint returned 400")
            return _FakeResult("# Text-only fallback content")

        _FakeMarkItDown.convert = flaky_convert  # type: ignore[method-assign]
        try:
            result = convert_with_markitdown("/tmp/scanned.pdf", vision_llm=llm)
        finally:
            _FakeMarkItDown.convert = original_convert  # type: ignore[method-assign]

        assert result == "# Text-only fallback content"
        assert fake_markitdown.convert_call_count == 2  # initial + retry

    def test_no_vision_failure_does_not_retry(
        self, fake_markitdown: type[_FakeMarkItDown]
    ) -> None:
        """Errors in text-only mode bubble up — no silent swallowing."""
        from fim_one.core.document.markitdown_core import convert_with_markitdown

        fake_markitdown.convert_raises = RuntimeError("unsupported file format")

        with pytest.raises(RuntimeError, match="unsupported file format"):
            convert_with_markitdown("/tmp/mystery.xyz", vision_llm=None)

        # Only one attempt — no silent retry when there's nothing to
        # downgrade.
        assert fake_markitdown.convert_call_count == 1

    def test_older_markitdown_without_enable_plugins(
        self, fake_markitdown: type[_FakeMarkItDown]
    ) -> None:
        """Older MarkItDown versions reject enable_plugins — kernel falls back gracefully."""
        from fim_one.core.document.markitdown_core import convert_with_markitdown

        fake_markitdown.reject_kwargs = {"enable_plugins"}
        fake_markitdown.fake_output = "# Legacy output"

        result = convert_with_markitdown("/tmp/report.docx", vision_llm=None)
        assert result == "# Legacy output"
        # After the fallback, the zero-arg constructor was used.
        assert fake_markitdown.last_init_kwargs == {}

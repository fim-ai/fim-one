"""Built-in tool that converts any file or URL into clean Markdown.

This is the Agent-facing "thin shell" over the shared
:func:`fim_one.core.document.markitdown_core.convert_with_markitdown`
kernel. The kernel owns the real conversion logic; this file owns
agent-specific concerns:

* JSON schema declaration for the LLM
* Stringly-typed error messages the LLM can read
* Asyncio dispatch (the kernel is sync so we wrap it with
  :func:`asyncio.to_thread` to avoid stalling the event loop)

Vision LLM is **injected at construction time** by ``_resolve_tools`` in
:mod:`fim_one.web.api.chat`, not pulled from environment variables at
``run()`` time. This matches the pattern already used by
:class:`GroundedRetrieveTool` and keeps the tool decoupled from DB
sessions / env var lookups. When the caller passes ``vision_llm=None``
(e.g. because no vision-capable model is configured in the active
workspace), the tool runs in text-only mode — still useful for all
the non-image-heavy formats MarkItDown supports.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from fim_one.core.document.markitdown_core import (
    MarkItDownNotInstalledError,
    convert_with_markitdown,
)

from ..base import BaseTool, ToolResult

if TYPE_CHECKING:
    from fim_one.core.model.openai_compatible import OpenAICompatibleLLM

logger = logging.getLogger(__name__)


class MarkItDownTool(BaseTool):
    """Convert any document, URL, or media file into clean Markdown.

    Supported sources (delegated to Microsoft's ``markitdown`` library):
    PDF, Word (.docx), Excel (.xlsx / .xls), PowerPoint (.pptx), HTML,
    JSON, CSV, XML, ZIP, EPUB, images (EXIF + OCR), audio (speech → text),
    YouTube URLs, Outlook (.msg) files. Accepts local paths, http(s) URLs,
    ``file://`` and ``data:`` URIs.

    When a vision-capable LLM is injected at construction time, the
    official ``markitdown-ocr`` plugin automatically OCRs embedded images
    inside Office documents and full-page scanned PDFs via the same
    LiteLLM routing the rest of FIM One uses. When no vision LLM is
    available, the tool transparently falls back to text-only extraction.
    """

    def __init__(
        self,
        *,
        vision_llm: "OpenAICompatibleLLM | None" = None,
        user_id: str | None = None,
    ) -> None:
        """Construct the tool.

        Args:
            vision_llm: Optional FIM One LLM instance with vision
                support. Passed by ``_resolve_tools`` after running
                :func:`_resolve_vision_llm` against the active workspace.
                ``None`` means "no vision-capable model found, OCR
                disabled" — the tool still works for text-bearing
                formats.
            user_id: Optional user ID for resolving ``file_id`` parameters
                to local file paths. When set, the tool can convert
                user-uploaded files by their UUID instead of requiring
                the caller to guess the file URI.
        """
        self._vision_llm = vision_llm
        self._user_id = user_id

    @property
    def name(self) -> str:
        return "convert_to_markdown"

    @property
    def category(self) -> str:
        return "document"

    @property
    def cacheable(self) -> bool:
        # Within a single DAG execution the same URI won't drift, so
        # caching avoids redundant conversions when multiple steps need
        # the same document.  The ToolCache is scoped per-execution and
        # discarded afterwards, so staleness is not a concern.
        return True

    @property
    def description(self) -> str:
        return (
            "Convert any file or URL into clean Markdown suitable for LLM "
            "consumption. Supports PDF, Word (.docx), Excel (.xlsx/.xls), "
            "PowerPoint (.pptx), HTML, JSON, CSV, XML, ZIP, EPUB, images "
            "(EXIF + OCR), audio (speech → text), YouTube URLs, and Outlook "
            ".msg files. Accepts local paths, http://, https://, file://, "
            "and data: URIs. When the workspace has a vision-capable model "
            "configured, embedded images and scanned PDF pages are OCR'd "
            "automatically. Returns the full Markdown as a string."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": (
                        "The source to convert. Can be a local file path "
                        "('/tmp/report.pdf'), an http(s) URL "
                        "('https://example.com/deck.pptx'), a YouTube link "
                        "('https://www.youtube.com/watch?v=...'), a file:// "
                        "URI, or a data: URI. Not needed when file_id is "
                        "provided."
                    ),
                },
                "file_id": {
                    "type": "string",
                    "description": (
                        "The ID of an uploaded file to convert. Preferred "
                        "over uri for user-uploaded files — resolves the "
                        "correct local path automatically."
                    ),
                },
            },
            # Neither is strictly required — one of the two must be provided
            "required": [],
        }

    def availability(self) -> tuple[bool, str | None]:
        """Report whether the underlying ``markitdown`` package is importable."""
        try:
            import markitdown  # noqa: F401
        except ImportError:
            return (
                False,
                "markitdown is not installed. Run: "
                "uv pip install 'markitdown[docx,xlsx,pptx,pdf,xls,"
                "outlook,audio-transcription,youtube-transcription]>=0.1.5' "
                "markitdown-ocr",
            )
        return True, None

    async def run(self, **kwargs: Any) -> str | ToolResult:  # type: ignore[override]
        file_id = (kwargs.get("file_id") or "").strip()
        uri = (kwargs.get("uri") or "").strip()

        # Resolve file_id → local path (same pattern as ReadUploadedFileTool)
        if file_id and self._user_id:
            try:
                from fim_one.web.api.files import _load_index, _user_dir

                index = _load_index(self._user_id)
                meta = index.get(file_id)
                if meta is not None:
                    user_dir = _user_dir(self._user_id)
                    file_path = user_dir / str(meta["stored_name"])
                    if file_path.exists():
                        uri = str(file_path.resolve())
                        logger.info("Resolved file_id %s → %s", file_id, uri)
                    else:
                        return (
                            f"[Error] File {meta.get('filename', file_id)} "
                            "not found on disk."
                        )
                else:
                    return f"[Error] file_id '{file_id}' not found in upload index."
            except Exception as exc:
                logger.warning(
                    "Failed to resolve file_id %r: %s", file_id, exc
                )
                return f"[Error] Failed to resolve file_id: {exc}"

        if not uri:
            return "[Error] Either `uri` or `file_id` is required."

        # The kernel is sync (MarkItDown itself is sync; some sources
        # block on disk / network / subprocess). Dispatch to a worker
        # thread so we don't stall the event loop for the whole agent.
        try:
            content = await asyncio.to_thread(
                convert_with_markitdown,
                uri,
                vision_llm=self._vision_llm,
            )
        except MarkItDownNotInstalledError as exc:
            return f"[Error] {exc}"
        except Exception as exc:
            logger.warning("MarkItDown conversion failed for %r", uri, exc_info=True)
            return f"[Error] Failed to convert {uri!r}: {exc}"

        if not content.strip():
            return f"[Warning] No extractable content found in {uri!r}."
        return ToolResult(content=content, content_type="markdown")

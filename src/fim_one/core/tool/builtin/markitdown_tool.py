"""Built-in tool that converts an uploaded file or a web URL into Markdown.

This is the Agent-facing "thin shell" over the shared
:func:`fim_one.core.document.markitdown_core.convert_with_markitdown`
kernel. The kernel owns the real conversion logic; this file owns
agent-specific concerns:

* JSON schema declaration for the LLM
* Stringly-typed error messages the LLM can read
* Asyncio dispatch (the kernel is sync so we wrap it with
  :func:`asyncio.to_thread` to avoid stalling the event loop)
* **Deciding what may be read.** The arguments are model-supplied, so
  the tool never treats one as a filesystem path: a local file is only
  reachable as a ``file_id`` looked up in the calling user's own upload
  index, and a remote document only as an ``http(s)`` URL downloaded
  through the SSRF-safe client (every redirect hop re-checked) before
  the bytes are handed to the kernel.

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
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

import httpx

from fim_one.core.document.markitdown_core import (
    ArchiveLimitError,
    MarkItDownNotInstalledError,
    convert_bytes_with_markitdown,
    convert_with_markitdown,
)
from fim_one.core.security import get_safe_async_client, validate_url

from ..base import BaseTool, ToolResult

if TYPE_CHECKING:
    from fim_one.core.model.openai_compatible import OpenAICompatibleLLM

logger = logging.getLogger(__name__)

# For the whole download, redirects included, not per read.
_DOWNLOAD_TIMEOUT_SECONDS = 60
_MAX_REDIRECTS = 5
_FILENAME_RE = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', re.IGNORECASE)


def _max_download_bytes() -> int:
    try:
        return max(1, int(os.environ.get("MARKITDOWN_MAX_DOWNLOAD_BYTES", "")))
    except ValueError:
        return 50 * 1024 * 1024


class _SourceRefused(ValueError):
    """The requested source is not one this tool may read."""


class MarkItDownTool(BaseTool):
    """Convert any document, URL, or media file into clean Markdown.

    Supported sources (delegated to Microsoft's ``markitdown`` library):
    PDF, Word (.docx), Excel (.xlsx / .xls), PowerPoint (.pptx), HTML,
    JSON, CSV, XML, ZIP, EPUB, images (EXIF + OCR), audio (speech → text),
    YouTube URLs, Outlook (.msg) files. Accepts the ``file_id`` of one of
    the calling user's uploads, or an ``http(s)`` URL.

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
            "Convert an uploaded file or a web URL into clean Markdown "
            "suitable for LLM consumption. Supports PDF, Word (.docx), Excel "
            "(.xlsx/.xls), PowerPoint (.pptx), HTML, JSON, CSV, XML, ZIP, "
            "EPUB, images (EXIF + OCR), audio (speech → text), YouTube URLs, "
            "and Outlook .msg files. Pass `file_id` for a file the user "
            "uploaded, or `uri` with an http:// or https:// URL. Local "
            "paths and other URI schemes are not accepted. "
            "When the workspace has a vision-capable model "
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
                        "An http(s) URL to download and convert "
                        "('https://example.com/deck.pptx'), or a YouTube "
                        "link ('https://www.youtube.com/watch?v=...'). "
                        "Local file paths are not accepted — use file_id "
                        "for uploaded files. Not needed when file_id is "
                        "provided."
                    ),
                },
                "file_id": {
                    "type": "string",
                    "description": (
                        "The ID of an uploaded file to convert. Preferred "
                        "The only way to convert a user-uploaded file."
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
        file_id = str(kwargs.get("file_id") or "").strip()
        uri = str(kwargs.get("uri") or "").strip()

        if not file_id and not uri:
            return "[Error] Either `uri` or `file_id` is required."

        label = file_id or uri
        try:
            if file_id:
                # An uploaded file wins over ``uri`` when both are given.
                local_path, label = self._resolve_upload(file_id)
                # The kernel is sync (MarkItDown itself is sync; some
                # sources block on disk / subprocess). Dispatch to a
                # worker thread so we don't stall the event loop.
                content = await asyncio.to_thread(
                    convert_with_markitdown,
                    local_path,
                    vision_llm=self._vision_llm,
                )
            else:
                data, final_url, mimetype, charset, filename = await self._download(uri)
                content = await asyncio.to_thread(
                    convert_bytes_with_markitdown,
                    data,
                    filename=filename,
                    mimetype=mimetype,
                    charset=charset,
                    url=final_url,
                    vision_llm=self._vision_llm,
                )
        except _SourceRefused as exc:
            return f"[Error] {exc}"
        except (MarkItDownNotInstalledError, ArchiveLimitError) as exc:
            return f"[Error] {exc}"
        except Exception as exc:
            logger.warning("MarkItDown conversion failed for %r", label, exc_info=True)
            return f"[Error] Failed to convert {label!r}: {exc}"

        if not content.strip():
            return f"[Warning] No extractable content found in {label!r}."
        return ToolResult(content=content, content_type="markdown")

    # ------------------------------------------------------------------
    # Source resolution
    # ------------------------------------------------------------------

    def _resolve_upload(self, file_id: str) -> tuple[Path, str]:
        """Map *file_id* to a file inside the calling user's upload directory.

        Returns ``(path, display_name)``.  The lookup goes through the
        user's own upload index; the stored name is then confined to that
        user's directory, so neither the id nor a tampered index entry can
        name a file elsewhere.
        """
        if not self._user_id:
            raise _SourceRefused(
                "Uploaded files are not available in this context."
            )
        try:
            from fim_one.web.api.files import _load_index, _user_dir

            meta = _load_index(self._user_id).get(file_id)
            user_dir = _user_dir(self._user_id)
        except Exception as exc:
            logger.warning("Failed to resolve file_id %r: %s", file_id, exc)
            raise _SourceRefused(f"Failed to resolve file_id: {exc}") from exc

        if not isinstance(meta, dict) or not meta.get("stored_name"):
            raise _SourceRefused(f"file_id '{file_id}' not found in upload index.")

        display = str(meta.get("filename") or file_id)
        root = user_dir.resolve()
        candidate = (user_dir / str(meta["stored_name"])).resolve()
        if candidate == root or not candidate.is_relative_to(root):
            raise _SourceRefused(f"file_id '{file_id}' not found in upload index.")
        if not candidate.is_file():
            raise _SourceRefused(f"File {display} not found on disk.")
        return candidate, display

    @staticmethod
    async def _download(
        uri: str,
    ) -> tuple[bytes, str, str | None, str | None, str | None]:
        """Fetch *uri* through the SSRF-safe client.

        Returns ``(body, final_url, mimetype, charset, filename)``.
        """
        parts = urlsplit(uri)
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            raise _SourceRefused(
                "`uri` must be an http:// or https:// URL. Local paths and "
                "other URI schemes are not accepted; use `file_id` for "
                "uploaded files."
            )
        try:
            # Fast, readable refusal for the first hop; the transport
            # re-resolves and re-checks this and every redirect hop.
            await asyncio.to_thread(validate_url, uri)
        except ValueError as exc:
            raise _SourceRefused(f"URL not allowed: {exc}") from exc

        limit = _max_download_bytes()
        chunks: list[bytes] = []
        size = 0
        try:
            async with asyncio.timeout(_DOWNLOAD_TIMEOUT_SECONDS), get_safe_async_client(
                timeout=_DOWNLOAD_TIMEOUT_SECONDS,
                follow_redirects=True,
                max_redirects=_MAX_REDIRECTS,
                headers={"User-Agent": "FIM-One/convert_to_markdown"},
            ) as client:
                async with client.stream("GET", uri) as resp:
                    resp.raise_for_status()
                    async for chunk in resp.aiter_bytes():
                        size += len(chunk)
                        if size > limit:
                            raise _SourceRefused(
                                f"Download exceeds the {limit}-byte limit."
                            )
                        chunks.append(chunk)
                    final_url = str(resp.url)
                    content_type = resp.headers.get("content-type", "")
                    disposition = resp.headers.get("content-disposition", "")
                    charset = resp.charset_encoding
        except _SourceRefused:
            raise
        except TimeoutError as exc:
            raise _SourceRefused(
                f"Download did not finish within {_DOWNLOAD_TIMEOUT_SECONDS} seconds."
            ) from exc
        except ValueError as exc:
            # Raised by the transport when a hop resolves to a blocked address.
            raise _SourceRefused(f"URL not allowed: {exc}") from exc
        except httpx.HTTPError as exc:
            raise _SourceRefused(f"Download failed: {exc}") from exc

        mimetype = content_type.split(";", 1)[0].strip().lower() or None
        filename: str | None = None
        match = _FILENAME_RE.search(disposition)
        if match:
            filename = Path(unquote(match.group(1).strip())).name or None
        if not filename:
            filename = Path(unquote(urlsplit(final_url).path)).name or None
        return b"".join(chunks), final_url, mimetype, charset, filename

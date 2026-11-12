"""Shared MarkItDown conversion kernel.

This module is the **single source of truth** for "document → Markdown"
inside FIM One. Three callers depend on it:

1. ``MarkItDownTool`` (built-in tool) — Agent-facing, invoked mid-conversation
2. ``MarkItDownLoader`` (RAG ingestion) — background KB loading pipeline
3. ``_extract_text_sync`` in :mod:`fim_one.core.document.processor` — file
   upload preview / search indexing

Keeping the conversion logic here (instead of duplicating across three
callers) guarantees that the Markdown an Agent sees in chat and the
Markdown ingested into the KB are byte-identical for the same input,
and that fixes / upgrades land everywhere at once.

Why a function and not another BaseTool?
----------------------------------------
RAG ingestion and the file upload preview are **not Agent contexts**.
Forcing them to instantiate a Tool and interpret its stringly-typed
``[Error] ...`` return values would be a semantic mismatch. The kernel
is a plain async-friendly sync function so every caller can wrap it in
whatever error/async shape they need.
"""

from __future__ import annotations

import importlib
import io
import logging
import os
import re
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fim_one.core.model.openai_compatible import OpenAICompatibleLLM

logger = logging.getLogger(__name__)


class MarkItDownNotInstalledError(RuntimeError):
    """Raised when the ``markitdown`` package is not importable."""


class ArchiveLimitError(ValueError):
    """A ZIP-based document unpacks to more than the configured limits allow."""


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "")))
    except ValueError:
        return default


def _archive_limits() -> tuple[int, int, int]:
    """``(unpacked bytes, members, nesting depth)`` allowed for one document."""
    return (
        _env_int("MARKITDOWN_MAX_UNPACKED_BYTES", 256 * 1024 * 1024),
        _env_int("MARKITDOWN_MAX_ARCHIVE_MEMBERS", 5000),
        _env_int("MARKITDOWN_MAX_ARCHIVE_DEPTH", 3),
    )


def check_archive_limits(source: str | Path | bytes) -> None:
    """Refuse a ZIP-based document that unpacks beyond the configured limits.

    Applies to every ZIP container MarkItDown opens: ``.zip`` archives,
    whose members it converts one by one (archives inside them included),
    and the Office and EPUB formats.  Counted over the whole document,
    nested archives included: the uncompressed size of all members
    (``MARKITDOWN_MAX_UNPACKED_BYTES``, 256 MB), the number of members
    (``MARKITDOWN_MAX_ARCHIVE_MEMBERS``, 5000) and how deep archives are
    nested (``MARKITDOWN_MAX_ARCHIVE_DEPTH``, 3; the outer archive is 1).
    Anything that is not a ZIP file passes unchanged.

    Raises:
        ArchiveLimitError: when a limit is exceeded.
    """
    max_bytes, max_members, max_depth = _archive_limits()
    totals = {"bytes": 0, "members": 0}

    def walk(fileobj: Any, depth: int) -> None:
        if depth > max_depth:
            raise ArchiveLimitError(
                f"The document nests archives more than {max_depth} levels deep."
            )
        try:
            archive = zipfile.ZipFile(fileobj)
        except (zipfile.BadZipFile, OSError):
            return  # not a readable ZIP; the converter reports it
        with archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            totals["members"] += len(infos)
            if totals["members"] > max_members:
                raise ArchiveLimitError(
                    f"The document holds more than {max_members} files."
                )
            for info in infos:
                totals["bytes"] += info.file_size
                if totals["bytes"] > max_bytes:
                    raise ArchiveLimitError(
                        f"The document unpacks to more than {max_bytes} bytes."
                    )
            for info in infos:
                # A member is unpacked to see whether it is an archive itself,
                # whatever its name. The declared sizes counted above bound
                # what each read returns.
                try:
                    with archive.open(info) as member:
                        data = member.read()
                except (zipfile.BadZipFile, OSError, NotImplementedError, RuntimeError):
                    continue  # unreadable member; the converter reports it
                nested = io.BytesIO(data)
                if zipfile.is_zipfile(nested):
                    walk(nested, depth + 1)

    if isinstance(source, (bytes, bytearray)):
        buffer = io.BytesIO(bytes(source))
        if zipfile.is_zipfile(buffer):
            walk(buffer, 1)
        return
    if not zipfile.is_zipfile(source):
        return
    with open(source, "rb") as handle:
        walk(handle, 1)


_URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def convert_with_markitdown(
    path: str | Path,
    *,
    vision_llm: "OpenAICompatibleLLM | None" = None,
) -> str:
    """Convert a **local file** into Markdown.

    This is the **synchronous** kernel — callers that need to run inside
    an event loop should wrap this call with :func:`asyncio.to_thread`
    themselves. We deliberately do not dispatch here so that the kernel
    stays callable from sync contexts (e.g. the file upload API path).

    The kernel reads exactly the file it is given and never fetches
    anything: a value that looks like a URI (``http:``, ``file:``,
    ``data:`` …) is refused rather than handed to MarkItDown, whose
    ``convert()`` would dereference it with its own HTTP client.  Deciding
    *which* file a caller may read is the caller's job — pass a path the
    application looked up itself, never a model- or user-supplied string.
    Remote content goes through :func:`convert_bytes_with_markitdown`
    after the caller downloaded it with the SSRF-safe client.

    Args:
        path: Filesystem path of the file to convert.
        vision_llm: Optional FIM One LLM instance with vision support.
            When provided, MarkItDown's built-in image-description path
            and the ``markitdown-ocr`` plugin will OCR embedded images
            and scanned PDF pages. When ``None``, MarkItDown runs in
            text-only mode — image placeholders remain, scanned PDFs
            produce no text. Text-only mode is never worse than the
            pre-kernel behavior.

    Returns:
        The full Markdown text. Returns an empty string when the source
        produced no extractable content (caller decides how to surface
        that — e.g. KB loaders return `[]`, the built-in tool returns a
        ``[Warning]`` string).

    Raises:
        MarkItDownNotInstalledError: If the ``markitdown`` package is
            not installed at all. Caller decides whether to surface this
            as an error string or re-raise.
        ValueError: If *path* is a URI rather than a filesystem path.
        ArchiveLimitError: If a ZIP-based document unpacks beyond the
            limits of :func:`check_archive_limits`.
        Exception: Any other error bubbles up untouched. The kernel does
            NOT swallow conversion errors — only the "vision LLM call
            failed" inner path triggers a text-only retry (see below),
            because that's the specific failure mode we know about and
            can recover from. All other errors are real problems the
            caller needs to see.
    """
    _require_markitdown()

    text = str(path)
    if not text.strip():
        return ""
    if isinstance(path, str):
        text = text.strip()
        if _URI_SCHEME_RE.match(text) and not _WINDOWS_DRIVE_RE.match(text):
            raise ValueError(
                "convert_with_markitdown only reads local files; "
                "download remote content first and use "
                "convert_bytes_with_markitdown."
            )

    check_archive_limits(text)
    return _convert(
        lambda converter: converter.convert_local(text),
        label=text,
        vision_llm=vision_llm,
    )


def convert_bytes_with_markitdown(
    data: bytes,
    *,
    filename: str | None = None,
    mimetype: str | None = None,
    charset: str | None = None,
    url: str | None = None,
    vision_llm: "OpenAICompatibleLLM | None" = None,
) -> str:
    """Convert already-downloaded content into Markdown.

    Args:
        data: The raw bytes.
        filename: Name used for format detection (only its extension
            matters).
        mimetype: Declared media type, if any.
        charset: Declared character set, if any.
        url: Where the bytes came from.  Used for format detection only
            (some converters key on the page URL); the kernel never
            requests it.
        vision_llm: See :func:`convert_with_markitdown`.

    Returns / Raises: as :func:`convert_with_markitdown`.
    """
    _require_markitdown()
    if not data:
        return ""
    check_archive_limits(data)

    from markitdown import StreamInfo

    extension: str | None = None
    if filename:
        suffix = Path(filename).suffix
        extension = suffix or None
    info = StreamInfo(
        mimetype=mimetype or None,
        charset=charset or None,
        filename=filename or None,
        extension=extension,
        url=url or None,
    )
    return _convert(
        lambda converter: converter.convert_stream(io.BytesIO(data), stream_info=info),
        label=url or filename or "<bytes>",
        vision_llm=vision_llm,
    )


def _require_markitdown() -> None:
    try:
        import markitdown  # noqa: F401
    except ImportError as exc:
        raise MarkItDownNotInstalledError(
            "markitdown is not installed. Install with: "
            "uv pip install 'markitdown[docx,xlsx,pptx,pdf,xls,outlook,"
            "audio-transcription,youtube-transcription]>=0.1.5' markitdown-ocr"
        ) from exc


def _convert(
    run: Callable[[Any], Any],
    *,
    label: str,
    vision_llm: "OpenAICompatibleLLM | None",
) -> str:
    if vision_llm is not None:
        logger.info(
            "Converting %r with vision OCR (model: %s)",
            label[:80],
            getattr(vision_llm, "model_id", "unknown"),
        )
    else:
        logger.debug("Converting %r in text-only mode (no vision LLM)", label[:80])

    # --- Attempt 1: full-featured (plugins + optional vision) ---
    llm_client, llm_model = _build_vision_client(vision_llm)
    converter = _build_converter(
        llm_client=llm_client,
        llm_model=llm_model,
    )

    try:
        return _extract_content(run(converter))
    except Exception as exc:
        if llm_client is None:
            # No vision in the first attempt — nothing to retry without.
            # Bubble up so the caller sees the real error.
            raise

        # Vision path failed — most likely because the resolved model is
        # not OpenAI-compatible at the wire level (Anthropic / Gemini
        # native APIs that LiteLLM would normally route around, but
        # MarkItDown calls our shim directly), or credentials are
        # expired, or the remote endpoint hit a transient error. Retry
        # once in text-only mode so the user still gets Markdown without
        # OCR, instead of failing the whole conversion.
        logger.warning(
            "MarkItDown vision path failed for %r, retrying text-only: %s",
            label,
            exc,
        )

    converter = _build_converter(llm_client=None, llm_model=None)
    return _extract_content(run(converter))


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _build_converter(
    *,
    llm_client: Any | None,
    llm_model: str | None,
) -> Any:
    """Instantiate ``MarkItDown`` with plugins + optional vision.

    Wrapped in a try/except because older ``markitdown`` versions may
    reject ``enable_plugins`` or ``llm_client`` kwargs. In that case we
    fall back to the zero-arg constructor so the caller still gets
    *something* instead of an ImportError propagating up the stack.
    """
    from markitdown import MarkItDown

    kwargs: dict[str, Any] = {"enable_plugins": True}
    session = _no_network_session()
    if session is not None:
        # The kernel never asks MarkItDown to fetch; this makes sure a
        # converter that tries to use the shared session cannot either.
        kwargs["requests_session"] = session
    if llm_client is not None and llm_model:
        kwargs["llm_client"] = llm_client
        kwargs["llm_model"] = llm_model

    try:
        return MarkItDown(**kwargs)
    except TypeError:
        logger.warning(
            "MarkItDown rejected enable_plugins/llm_client kwargs — "
            "falling back to zero-arg constructor. Upgrade markitdown "
            "to >=0.1.5 to enable the plugin ecosystem and OCR."
        )
        return MarkItDown()


def _no_network_session() -> Any | None:
    """A ``requests.Session`` with no transport adapters.

    Any request made through it fails with ``InvalidSchema`` before a
    socket is opened.
    """
    try:
        requests = importlib.import_module("requests")
    except ImportError:
        return None
    session = requests.Session()
    session.adapters.clear()
    return session


def _build_vision_client(
    vision_llm: "OpenAICompatibleLLM | None",
) -> tuple[Any | None, str | None]:
    """Adapt a FIM One LLM into an openai.OpenAI-shaped client.

    Returns ``(None, None)`` when no LLM was supplied. When an LLM is
    supplied, returns ``(shim, model_id)`` where ``shim`` is a
    :class:`LiteLLMOpenAIShim` that MarkItDown can duck-type-call via
    ``client.chat.completions.create(...)``.
    """
    if vision_llm is None:
        return None, None

    # Late import to avoid a heavy module import on the hot path when
    # vision is disabled (which is most requests).
    from fim_one.core.model.litellm_openai_shim import LiteLLMOpenAIShim

    try:
        shim = LiteLLMOpenAIShim(vision_llm)
    except Exception:
        logger.warning(
            "Failed to build LiteLLM OpenAI shim for vision LLM, "
            "falling back to text-only conversion",
            exc_info=True,
        )
        return None, None

    return shim, vision_llm.model_id


def _extract_content(result: Any) -> str:
    """Pull the Markdown string out of a MarkItDown result.

    MarkItDown's result object exposes ``.text_content``. We defensively
    handle missing attributes so a future upstream rename surfaces as
    an empty string rather than an AttributeError — consistent with how
    the RAG loaders treat empty conversions.
    """
    content = getattr(result, "text_content", None) or ""
    return content if isinstance(content, str) else ""

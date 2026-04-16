"""Shared MarkItDown conversion kernel.

This module is the **single source of truth** for "file/URL → Markdown"
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

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fim_one.core.model.openai_compatible import OpenAICompatibleLLM

logger = logging.getLogger(__name__)


class MarkItDownNotInstalledError(RuntimeError):
    """Raised when the ``markitdown`` package is not importable."""


def convert_with_markitdown(
    uri: str,
    *,
    vision_llm: "OpenAICompatibleLLM | None" = None,
) -> str:
    """Convert any file / URL / URI into Markdown.

    This is the **synchronous** kernel — callers that need to run inside
    an event loop should wrap this call with :func:`asyncio.to_thread`
    themselves. We deliberately do not dispatch here so that the kernel
    stays callable from sync contexts (e.g. the file upload API path).

    Args:
        uri: The source to convert. Accepts local file paths, http(s)
            URLs, YouTube links, ``file://``, and ``data:`` URIs —
            anything MarkItDown's ``.convert()`` accepts.
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
        Exception: Any other error bubbles up untouched. The kernel does
            NOT swallow conversion errors — only the "vision LLM call
            failed" inner path triggers a text-only retry (see below),
            because that's the specific failure mode we know about and
            can recover from. All other errors are real problems the
            caller needs to see.
    """
    try:
        from markitdown import MarkItDown
    except ImportError as exc:
        raise MarkItDownNotInstalledError(
            "markitdown is not installed. Install with: "
            "uv pip install 'markitdown[docx,xlsx,pptx,pdf,xls,outlook,"
            "audio-transcription,youtube-transcription]>=0.1.5' markitdown-ocr"
        ) from exc

    if not uri or not uri.strip():
        return ""

    if vision_llm is not None:
        logger.info(
            "Converting %r with vision OCR (model: %s)",
            uri[:80],
            getattr(vision_llm, "model_id", "unknown"),
        )
    else:
        logger.debug("Converting %r in text-only mode (no vision LLM)", uri[:80])

    # --- Attempt 1: full-featured (plugins + optional vision) ---
    llm_client, llm_model = _build_vision_client(vision_llm)
    converter = _build_converter(
        llm_client=llm_client,
        llm_model=llm_model,
    )

    try:
        result = converter.convert(uri)
        return _extract_content(result)
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
            uri,
            exc,
        )

    converter = _build_converter(llm_client=None, llm_model=None)
    result = converter.convert(uri)
    return _extract_content(result)


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

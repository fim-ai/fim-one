"""Document loader for Office / PDF / media files using MarkItDown.

Thin shell over :func:`fim_one.core.document.markitdown_core.convert_with_markitdown`.
The kernel owns the conversion logic (plugins, vision OCR, fallback
retries); this loader owns RAG-specific concerns (async dispatch,
``LoadedDocument`` wrapping, metadata).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from fim_one.core.document.markitdown_core import (
    MarkItDownNotInstalledError,
    convert_with_markitdown,
)

from .base import BaseLoader, LoadedDocument

if TYPE_CHECKING:
    from fim_one.core.model.openai_compatible import OpenAICompatibleLLM

# File extensions handled by this loader. Expanded to match the upstream
# MarkItDown 0.1.5 format coverage so RAG ingestion does not have to
# route through a second fallback pipeline for these types.
SUPPORTED_EXTENSIONS = {
    ".docx",
    ".xlsx",
    ".xls",
    ".pptx",
    ".pdf",
    ".msg",
    ".epub",
    ".mp3",
    ".wav",
    ".m4a",
}


class MarkItDownLoader(BaseLoader):
    """Load Office / PDF / media files via markitdown.

    The entire document is returned as a single ``LoadedDocument`` whose
    content is Markdown text. Because ``markitdown`` is synchronous, the
    heavy work is dispatched to a thread via ``asyncio.to_thread``.

    Args:
        vision_llm: Optional FIM One LLM instance with vision support.
            When provided, MarkItDown's built-in image description and
            the ``markitdown-ocr`` plugin automatically OCR embedded
            images. Injected by the RAG manager when the workspace has
            a vision-capable default model configured.
    """

    def __init__(
        self,
        *,
        vision_llm: "OpenAICompatibleLLM | None" = None,
    ) -> None:
        self._vision_llm = vision_llm

    async def load(self, path: Path) -> list[LoadedDocument]:
        return await asyncio.to_thread(self._load_sync, path)

    def _load_sync(self, path: Path) -> list[LoadedDocument]:
        try:
            content = convert_with_markitdown(
                str(path),
                vision_llm=self._vision_llm,
            )
        except MarkItDownNotInstalledError as exc:
            raise ImportError(str(exc)) from exc

        if not content.strip():
            return []

        return [
            LoadedDocument(
                content=content,
                metadata={
                    "source": str(path),
                    "format": path.suffix.lower().lstrip("."),
                },
            )
        ]

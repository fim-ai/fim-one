"""Document loader for DOCX, XLSX, and PPTX using MarkItDown.

MarkItDown converts Office documents to Markdown, preserving headings,
tables, and structure better than plain text extraction.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from .base import BaseLoader, LoadedDocument

# File extensions handled by this loader.
SUPPORTED_EXTENSIONS = {".docx", ".xlsx", ".xls", ".pptx"}


class MarkItDownLoader(BaseLoader):
    """Load DOCX / XLSX / PPTX files via markitdown.

    The entire document is returned as a single ``LoadedDocument`` whose
    content is Markdown text.  Because ``markitdown`` is synchronous, the
    heavy work is dispatched to a thread via ``asyncio.to_thread``.
    """

    async def load(self, path: Path) -> list[LoadedDocument]:
        return await asyncio.to_thread(self._load_sync, path)

    def _load_sync(self, path: Path) -> list[LoadedDocument]:
        try:
            from markitdown import MarkItDown  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "markitdown is required for Office document loading. "
                "Install it with: uv pip install 'markitdown[docx,xlsx,pptx]'"
            ) from exc

        converter = MarkItDown()
        result = converter.convert(str(path))
        content: str = result.text_content or ""

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

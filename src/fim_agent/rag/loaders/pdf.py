"""PDF document loader using pdfplumber."""

from __future__ import annotations

import asyncio
from pathlib import Path

from .base import BaseLoader, LoadedDocument


class PDFLoader(BaseLoader):
    """Load PDF files using pdfplumber. Returns one document per page."""

    async def load(self, path: Path) -> list[LoadedDocument]:
        return await asyncio.to_thread(self._load_sync, path)

    def _load_sync(self, path: Path) -> list[LoadedDocument]:
        import pdfplumber

        docs: list[LoadedDocument] = []
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                if text.strip():
                    docs.append(
                        LoadedDocument(
                            content=text,
                            metadata={
                                "source": str(path),
                                "page_number": i,
                                "total_pages": len(pdf.pages),
                            },
                        )
                    )
        return docs

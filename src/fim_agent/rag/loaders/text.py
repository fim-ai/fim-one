"""Plain text document loader."""

from __future__ import annotations

import asyncio
from pathlib import Path

from .base import BaseLoader, LoadedDocument


class TextLoader(BaseLoader):
    """Load plain text files."""

    async def load(self, path: Path) -> list[LoadedDocument]:
        content = await asyncio.to_thread(path.read_text, encoding="utf-8")
        if not content.strip():
            return []
        return [
            LoadedDocument(
                content=content,
                metadata={"source": str(path)},
            )
        ]

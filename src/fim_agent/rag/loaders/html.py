"""HTML document loader with simple tag stripping."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from .base import BaseLoader, LoadedDocument


class HTMLLoader(BaseLoader):
    """Load HTML files by stripping tags."""

    _TAG_RE = re.compile(r"<[^>]+>")
    _MULTI_NEWLINE_RE = re.compile(r"\n{3,}")

    async def load(self, path: Path) -> list[LoadedDocument]:
        raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
        text = self._strip_tags(raw)
        if not text.strip():
            return []
        return [
            LoadedDocument(
                content=text.strip(),
                metadata={"source": str(path)},
            )
        ]

    def _strip_tags(self, html: str) -> str:
        # Remove script and style blocks
        text = re.sub(
            r"<(script|style)[^>]*>.*?</\1>",
            "",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        # Replace br/p/div/li tags with newlines
        text = re.sub(
            r"<(br|/p|/div|/li|/h[1-6])[^>]*>",
            "\n",
            text,
            flags=re.IGNORECASE,
        )
        # Strip remaining tags
        text = self._TAG_RE.sub("", text)
        # Collapse multiple newlines
        text = self._MULTI_NEWLINE_RE.sub("\n\n", text)
        return text

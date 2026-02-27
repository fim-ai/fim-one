"""CSV document loader."""

from __future__ import annotations

import asyncio
import csv
import io
from pathlib import Path

from .base import BaseLoader, LoadedDocument


class CSVLoader(BaseLoader):
    """Load CSV files. Each row becomes a text entry."""

    async def load(self, path: Path) -> list[LoadedDocument]:
        return await asyncio.to_thread(self._load_sync, path)

    def _load_sync(self, path: Path) -> list[LoadedDocument]:
        text = path.read_text(encoding="utf-8")
        reader = csv.DictReader(io.StringIO(text))

        rows: list[str] = []
        for row in reader:
            parts = [f"{k}: {v}" for k, v in row.items() if v]
            if parts:
                rows.append("; ".join(parts))

        if not rows:
            return []

        content = "\n".join(rows)
        return [
            LoadedDocument(
                content=content,
                metadata={
                    "source": str(path),
                    "row_count": len(rows),
                },
            )
        ]

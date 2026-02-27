"""Fixed-size text chunker with overlap."""

from __future__ import annotations

from typing import Any

from .base import BaseChunker, Chunk


class FixedSizeChunker(BaseChunker):
    """Split text into fixed-size chunks with configurable overlap.

    Args:
        chunk_size: Maximum characters per chunk.
        overlap: Number of overlapping characters between consecutive chunks.
    """

    def __init__(self, chunk_size: int = 1000, overlap: int = 200) -> None:
        if overlap >= chunk_size:
            raise ValueError("overlap must be less than chunk_size")
        self._chunk_size = chunk_size
        self._overlap = overlap

    async def chunk(
        self, text: str, metadata: dict[str, Any] | None = None
    ) -> list[Chunk]:
        if not text.strip():
            return []

        base_meta = metadata or {}
        chunks: list[Chunk] = []
        step = self._chunk_size - self._overlap
        idx = 0

        for start in range(0, len(text), step):
            end = start + self._chunk_size
            chunk_text = text[start:end]
            if not chunk_text.strip():
                continue
            chunks.append(
                Chunk(
                    text=chunk_text,
                    metadata={**base_meta, "chunk_strategy": "fixed"},
                    index=idx,
                )
            )
            idx += 1
            if end >= len(text):
                break

        return chunks

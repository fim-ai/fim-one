"""Recursive character text splitter."""

from __future__ import annotations

from typing import Any

from .base import BaseChunker, Chunk


class RecursiveCharacterChunker(BaseChunker):
    """Split text recursively using a hierarchy of separators.

    Tries each separator in order. When a separator produces chunks that are
    still too large, it recurses with the next separator.

    Args:
        chunk_size: Maximum characters per chunk.
        overlap: Overlap between chunks.
        separators: Ordered list of separators to try.
    """

    DEFAULT_SEPARATORS = ["\n\n", "\n", "\u3002", ". ", " ", ""]

    def __init__(
        self,
        chunk_size: int = 1000,
        overlap: int = 200,
        separators: list[str] | None = None,
    ) -> None:
        if overlap >= chunk_size:
            raise ValueError("overlap must be less than chunk_size")
        self._chunk_size = chunk_size
        self._overlap = overlap
        self._separators = separators or self.DEFAULT_SEPARATORS

    async def chunk(
        self, text: str, metadata: dict[str, Any] | None = None
    ) -> list[Chunk]:
        if not text.strip():
            return []

        base_meta = metadata or {}
        raw_chunks = self._split_recursive(text, self._separators)
        merged = self._merge_with_overlap(raw_chunks)

        chunks: list[Chunk] = []
        for idx, chunk_text in enumerate(merged):
            if chunk_text.strip():
                chunks.append(
                    Chunk(
                        text=chunk_text,
                        metadata={**base_meta, "chunk_strategy": "recursive"},
                        index=idx,
                    )
                )
        return chunks

    def _split_recursive(self, text: str, separators: list[str]) -> list[str]:
        """Recursively split text using the separator hierarchy."""
        if not text:
            return []

        if len(text) <= self._chunk_size:
            return [text]

        # Find the best separator (first one that actually appears in text)
        separator = ""
        remaining_seps = separators
        for i, sep in enumerate(separators):
            if sep == "":
                separator = sep
                remaining_seps = []
                break
            if sep in text:
                separator = sep
                remaining_seps = separators[i + 1 :]
                break

        # Split with the chosen separator
        if separator:
            splits = text.split(separator)
        else:
            # Character-level split as last resort
            splits = list(text)

        # Merge small splits, recurse on large ones
        result: list[str] = []
        current = ""
        for split in splits:
            piece = (
                split
                if not separator
                else (current + separator + split if current else split)
            )

            if not current:
                current = split
                continue

            if len(piece) <= self._chunk_size:
                current = piece
            else:
                if current:
                    if len(current) > self._chunk_size and remaining_seps:
                        result.extend(
                            self._split_recursive(current, remaining_seps)
                        )
                    else:
                        result.append(current)
                current = split

        if current:
            if len(current) > self._chunk_size and remaining_seps:
                result.extend(self._split_recursive(current, remaining_seps))
            else:
                result.append(current)

        return result

    def _merge_with_overlap(self, chunks: list[str]) -> list[str]:
        """Apply overlap between consecutive chunks."""
        if not chunks or self._overlap == 0:
            return chunks

        merged: list[str] = []
        for i, chunk in enumerate(chunks):
            if i > 0 and self._overlap > 0:
                # Prepend overlap from previous chunk
                prev = chunks[i - 1]
                overlap_text = (
                    prev[-self._overlap :] if len(prev) > self._overlap else prev
                )
                chunk = overlap_text + chunk
            merged.append(chunk)
        return merged

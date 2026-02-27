"""Markdown-aware text chunker that splits by headers first."""

from __future__ import annotations

import re
from typing import Any

from .base import BaseChunker, Chunk


class MarkdownChunker(BaseChunker):
    """Split markdown text by headers, then recursively within sections.

    Strategy:
    1. Split the document at markdown header lines (``# ... ######``).
    2. Each header is kept as a prefix of its section content.
    3. If a section exceeds *chunk_size*, apply recursive character splitting
       using the same separator hierarchy as
       :class:`RecursiveCharacterChunker`.
    4. If the document contains no headers, fall back to recursive splitting
       of the entire text.
    5. Overlap is applied between consecutive chunks within each section.

    Args:
        chunk_size: Maximum characters per chunk.
        overlap: Number of overlapping characters between consecutive chunks.
    """

    _HEADER_RE = re.compile(r"^[ \t]{0,3}#{1,6}\s+", re.MULTILINE)
    _SEPARATORS = ["\n\n", "\n", "\u3002", ". ", " ", ""]

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
        sections = self._split_by_headers(text)

        # Fallback: no headers detected -- treat as plain text
        if not sections:
            return await self._recursive_fallback(text, base_meta)

        chunks: list[Chunk] = []
        global_idx = 0

        for header, body in sections:
            section_text = f"{header}\n{body}".strip() if header else body.strip()
            if not section_text:
                continue

            section_meta: dict[str, Any] = {
                **base_meta,
                "chunk_strategy": "markdown",
            }
            if header:
                section_meta["section"] = header.strip()

            if len(section_text) <= self._chunk_size:
                chunks.append(
                    Chunk(text=section_text, metadata=dict(section_meta), index=global_idx)
                )
                global_idx += 1
            else:
                # Recursive split within the section
                raw = self._split_recursive(section_text, self._SEPARATORS)
                merged = self._merge_with_overlap(raw)
                for part in merged:
                    if part.strip():
                        chunks.append(
                            Chunk(
                                text=part,
                                metadata=dict(section_meta),
                                index=global_idx,
                            )
                        )
                        global_idx += 1

        return chunks

    # ------------------------------------------------------------------
    # Header splitting
    # ------------------------------------------------------------------

    def _split_by_headers(self, text: str) -> list[tuple[str, str]]:
        """Split *text* into ``(header_line, body)`` pairs.

        Returns an empty list when no headers are found so the caller can
        fall back to recursive splitting.
        """
        # Find all header line positions
        header_matches = list(self._HEADER_RE.finditer(text))
        if not header_matches:
            return []

        # Determine actual line starts for each match
        sections: list[tuple[str, str]] = []

        # Content before the first header (if any)
        first_start = self._line_start(text, header_matches[0].start())
        if first_start > 0:
            preamble = text[:first_start].strip()
            if preamble:
                sections.append(("", preamble))

        for i, match in enumerate(header_matches):
            line_start = self._line_start(text, match.start())
            # End of this section is the start of the next header line
            if i + 1 < len(header_matches):
                next_line_start = self._line_start(text, header_matches[i + 1].start())
                section_text = text[line_start:next_line_start]
            else:
                section_text = text[line_start:]

            # Separate the header line from the body
            newline_pos = section_text.find("\n")
            if newline_pos == -1:
                header_line = section_text
                body = ""
            else:
                header_line = section_text[:newline_pos]
                body = section_text[newline_pos + 1:]

            sections.append((header_line, body))

        return sections

    @staticmethod
    def _line_start(text: str, pos: int) -> int:
        """Return the index of the beginning of the line containing *pos*."""
        idx = text.rfind("\n", 0, pos)
        return idx + 1 if idx != -1 else 0

    # ------------------------------------------------------------------
    # Recursive splitting (same logic as RecursiveCharacterChunker)
    # ------------------------------------------------------------------

    def _split_recursive(self, text: str, separators: list[str]) -> list[str]:
        if not text:
            return []
        if len(text) <= self._chunk_size:
            return [text]

        separator = ""
        remaining_seps = separators
        for i, sep in enumerate(separators):
            if sep == "":
                separator = sep
                remaining_seps = []
                break
            if sep in text:
                separator = sep
                remaining_seps = separators[i + 1:]
                break

        if separator:
            splits = text.split(separator)
        else:
            splits = list(text)

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
                        result.extend(self._split_recursive(current, remaining_seps))
                    else:
                        result.append(current)
                current = split

        if current:
            if len(current) > self._chunk_size and remaining_seps:
                result.extend(self._split_recursive(current, remaining_seps))
            else:
                result.append(current)

        return result

    # ------------------------------------------------------------------
    # Overlap merging
    # ------------------------------------------------------------------

    def _merge_with_overlap(self, chunks: list[str]) -> list[str]:
        if not chunks or self._overlap == 0:
            return chunks

        merged: list[str] = []
        for i, chunk in enumerate(chunks):
            if i > 0 and self._overlap > 0:
                prev = chunks[i - 1]
                overlap_text = prev[-self._overlap:] if len(prev) > self._overlap else prev
                chunk = overlap_text + chunk
            merged.append(chunk)
        return merged

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------

    async def _recursive_fallback(
        self, text: str, base_meta: dict[str, Any]
    ) -> list[Chunk]:
        """Fall back to pure recursive splitting when no headers are found."""
        raw = self._split_recursive(text, self._SEPARATORS)
        merged = self._merge_with_overlap(raw)

        chunks: list[Chunk] = []
        for idx, part in enumerate(merged):
            if part.strip():
                chunks.append(
                    Chunk(
                        text=part,
                        metadata={**base_meta, "chunk_strategy": "markdown"},
                        index=idx,
                    )
                )
        return chunks

"""Semantic text chunker using embedding similarity."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import BaseChunker, Chunk

if TYPE_CHECKING:
    from fim_one.core.embedding.base import BaseEmbedding


class SemanticChunker(BaseChunker):
    """Split text based on semantic similarity between sentences.

    Computes embeddings for consecutive sentences and splits where the
    cosine similarity drops below a threshold.

    Args:
        embedding: Embedding model to use for similarity computation.
        threshold: Cosine similarity threshold. Sentences with similarity
            below this are split into separate chunks.
        min_chunk_size: Minimum characters per chunk to avoid tiny fragments.
    """

    def __init__(
        self,
        embedding: BaseEmbedding | None = None,
        threshold: float = 0.5,
        min_chunk_size: int = 100,
    ) -> None:
        self._embedding = embedding
        self._threshold = threshold
        self._min_chunk_size = min_chunk_size

    async def chunk(
        self, text: str, metadata: dict[str, Any] | None = None
    ) -> list[Chunk]:
        if not text.strip():
            return []

        base_meta = metadata or {}

        # If no embedding model, fall back to paragraph splitting
        if self._embedding is None:
            return self._fallback_chunk(text, base_meta)

        # Split into sentences
        sentences = self._split_sentences(text)
        if len(sentences) <= 1:
            return [
                Chunk(
                    text=text,
                    metadata={**base_meta, "chunk_strategy": "semantic"},
                    index=0,
                )
            ]

        # Get embeddings for all sentences
        embeddings = await self._embedding.embed_texts(sentences)

        # Find split points based on similarity drops
        groups: list[list[str]] = [[sentences[0]]]
        for i in range(1, len(sentences)):
            sim = self._cosine_similarity(embeddings[i - 1], embeddings[i])
            if sim < self._threshold:
                groups.append([sentences[i]])
            else:
                groups[-1].append(sentences[i])

        # Merge small groups with previous
        merged_groups: list[list[str]] = []
        for group in groups:
            group_text = " ".join(group)
            if merged_groups and len(group_text) < self._min_chunk_size:
                merged_groups[-1].extend(group)
            else:
                merged_groups.append(group)

        chunks: list[Chunk] = []
        for idx, group in enumerate(merged_groups):
            chunk_text = " ".join(group)
            if chunk_text.strip():
                chunks.append(
                    Chunk(
                        text=chunk_text,
                        metadata={**base_meta, "chunk_strategy": "semantic"},
                        index=idx,
                    )
                )
        return chunks

    def _fallback_chunk(
        self, text: str, base_meta: dict[str, Any]
    ) -> list[Chunk]:
        """Fall back to paragraph-based chunking when no embedding is available."""
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            return [
                Chunk(
                    text=text,
                    metadata={**base_meta, "chunk_strategy": "semantic"},
                    index=0,
                )
            ]
        return [
            Chunk(
                text=p,
                metadata={**base_meta, "chunk_strategy": "semantic"},
                index=i,
            )
            for i, p in enumerate(paragraphs)
        ]

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Split text into sentences."""
        import re

        # Split on sentence-ending punctuation followed by space or newline
        sentences = re.split(r"(?<=[.!?\u3002\uff01\uff1f])\s+", text)
        return [s.strip() for s in sentences if s.strip()]

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot / (norm_a * norm_b))

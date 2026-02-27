"""Abstract base class for embedding providers."""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseEmbedding(ABC):
    """Abstract base for all embedding implementations.

    Subclasses must implement embed_texts and embed_query.
    """

    @abstractmethod
    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of documents/texts.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of embedding vectors, one per input text.
        """
        ...

    @abstractmethod
    async def embed_query(self, query: str) -> list[float]:
        """Embed a single query string.

        Args:
            query: The query text to embed.

        Returns:
            The embedding vector for the query.
        """
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """The dimensionality of the embedding vectors."""
        ...

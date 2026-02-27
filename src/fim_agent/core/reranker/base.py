"""Abstract base class for reranker providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class RerankResult:
    """A single reranking result.

    Attributes:
        index: Original index of the document in the input list.
        score: Relevance score assigned by the reranker.
        text: The document text.
    """

    index: int
    score: float
    text: str


class BaseReranker(ABC):
    """Abstract interface for document reranking.

    Rerankers take a query and a list of candidate documents, then return
    them reordered by relevance with scores.
    """

    @abstractmethod
    async def rerank(
        self, query: str, documents: list[str], *, top_k: int = 5
    ) -> list[RerankResult]:
        """Rerank documents by relevance to the query.

        Args:
            query: The search query.
            documents: List of document texts to rerank.
            top_k: Maximum number of results to return.

        Returns:
            List of RerankResult sorted by relevance (highest first).
        """
        ...

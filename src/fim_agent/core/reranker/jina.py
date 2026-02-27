"""Jina AI reranker implementation."""

from __future__ import annotations

import logging

import httpx

from fim_agent.core.model.retry import RetryConfig, retry_async_call

from .base import BaseReranker, RerankResult

logger = logging.getLogger(__name__)


class JinaReranker(BaseReranker):
    """Reranker using Jina AI's rerank API.

    Args:
        api_key: Jina API key.
        model: Reranker model identifier.
        base_url: Base URL for the rerank endpoint.
        retry_config: Retry configuration for resilience.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "jina-reranker-v2-base-multilingual",
        base_url: str = "https://api.jina.ai/v1",
        retry_config: RetryConfig | None = RetryConfig(),
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._retry_config = retry_config or RetryConfig(max_retries=0)

    async def rerank(
        self, query: str, documents: list[str], *, top_k: int = 5
    ) -> list[RerankResult]:
        if not documents:
            return []

        return await retry_async_call(
            self._rerank_impl, self._retry_config, query, documents, top_k=top_k
        )

    async def _rerank_impl(
        self, query: str, documents: list[str], *, top_k: int = 5
    ) -> list[RerankResult]:
        """Single rerank attempt."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self._base_url}/rerank",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "query": query,
                    "documents": documents,
                    "top_n": top_k,
                },
            )
            response.raise_for_status()
            data = response.json()

        results: list[RerankResult] = []
        for item in data.get("results", []):
            results.append(
                RerankResult(
                    index=item["index"],
                    score=item["relevance_score"],
                    text=documents[item["index"]],
                )
            )

        # Sort by score descending
        results.sort(key=lambda r: r.score, reverse=True)
        return results

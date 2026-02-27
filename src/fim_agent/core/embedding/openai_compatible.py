"""OpenAI-compatible embedding implementation."""

from __future__ import annotations

import logging

from openai import AsyncOpenAI

from fim_agent.core.model.retry import RetryConfig, retry_async_call

from .base import BaseEmbedding

logger = logging.getLogger(__name__)


class OpenAICompatibleEmbedding(BaseEmbedding):
    """Embedding implementation for any OpenAI-compatible /v1/embeddings endpoint.

    Works with OpenAI, Jina AI, and other providers.

    Args:
        api_key: API key for authentication.
        base_url: Base URL of the API (default: Jina AI).
        model: Model identifier.
        dim: Embedding dimension.
        batch_size: Max texts per API call.
        retry_config: Retry configuration for resilience.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.jina.ai/v1",
        model: str = "jina-embeddings-v3",
        dim: int = 1024,
        batch_size: int = 100,
        retry_config: RetryConfig | None = RetryConfig(),
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._dim = dim
        self._batch_size = batch_size
        self._retry_config = retry_config or RetryConfig(max_retries=0)

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed texts in batches."""
        if not texts:
            return []

        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            embeddings = await retry_async_call(
                self._embed_batch, self._retry_config, batch
            )
            all_embeddings.extend(embeddings)
        return all_embeddings

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a single batch (one API call)."""
        response = await self._client.embeddings.create(
            model=self._model,
            input=texts,
        )
        # Sort by index to guarantee order
        sorted_data = sorted(response.data, key=lambda x: x.index)
        return [item.embedding for item in sorted_data]

    async def embed_query(self, query: str) -> list[float]:
        """Embed a single query."""
        results = await self.embed_texts([query])
        return results[0]

    @property
    def dimension(self) -> int:
        return self._dim

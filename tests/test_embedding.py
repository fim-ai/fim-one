"""Tests for the embedding layer."""

import pytest

from fim_agent.core.embedding.base import BaseEmbedding
from fim_agent.core.embedding.openai_compatible import OpenAICompatibleEmbedding


class FakeEmbedding(BaseEmbedding):
    """Mock embedding that returns deterministic vectors."""

    def __init__(self, dim: int = 128) -> None:
        self._dim = dim

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[float(i)] * self._dim for i in range(len(texts))]

    async def embed_query(self, query: str) -> list[float]:
        return [1.0] * self._dim

    @property
    def dimension(self) -> int:
        return self._dim


async def test_fake_embedding_texts():
    emb = FakeEmbedding(dim=64)
    results = await emb.embed_texts(["hello", "world"])
    assert len(results) == 2
    assert len(results[0]) == 64
    assert results[0] == [0.0] * 64
    assert results[1] == [1.0] * 64


async def test_fake_embedding_query():
    emb = FakeEmbedding(dim=64)
    result = await emb.embed_query("test query")
    assert len(result) == 64


async def test_fake_embedding_empty():
    emb = FakeEmbedding()
    results = await emb.embed_texts([])
    assert results == []


async def test_dimension_property():
    emb = FakeEmbedding(dim=256)
    assert emb.dimension == 256


async def test_openai_compatible_init():
    """Test that OpenAICompatibleEmbedding can be instantiated."""
    emb = OpenAICompatibleEmbedding(
        api_key="fake-key",
        base_url="https://api.jina.ai/v1",
        model="jina-embeddings-v3",
        dim=1024,
    )
    assert emb.dimension == 1024

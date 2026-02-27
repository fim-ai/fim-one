"""Tests for the reranker layer."""

import pytest

from fim_agent.core.reranker.base import BaseReranker, RerankResult
from fim_agent.core.reranker.jina import JinaReranker


class FakeReranker(BaseReranker):
    """Mock reranker that scores by position (first doc gets highest score)."""

    async def rerank(
        self, query: str, documents: list[str], *, top_k: int = 5
    ) -> list[RerankResult]:
        results = []
        for i, doc in enumerate(documents):
            score = 1.0 - (i * 0.1)
            results.append(RerankResult(index=i, score=score, text=doc))
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]


async def test_fake_reranker():
    reranker = FakeReranker()
    results = await reranker.rerank("test query", ["doc1", "doc2", "doc3"])
    assert len(results) == 3
    assert results[0].score > results[1].score
    assert results[0].text == "doc1"


async def test_fake_reranker_top_k():
    reranker = FakeReranker()
    results = await reranker.rerank(
        "test query", ["a", "b", "c", "d", "e"], top_k=2
    )
    assert len(results) == 2


async def test_fake_reranker_empty():
    reranker = FakeReranker()
    results = await reranker.rerank("test", [])
    assert results == []


async def test_rerank_result_dataclass():
    result = RerankResult(index=0, score=0.95, text="hello world")
    assert result.index == 0
    assert result.score == 0.95
    assert result.text == "hello world"


async def test_jina_reranker_init():
    """Test that JinaReranker can be instantiated."""
    reranker = JinaReranker(api_key="fake-key")
    assert isinstance(reranker, BaseReranker)


async def test_jina_reranker_empty_docs():
    """Empty docs should return empty list without API call."""
    reranker = JinaReranker(api_key="fake-key")
    results = await reranker.rerank("test", [])
    assert results == []

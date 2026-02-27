"""Tests for the retriever layer."""

from __future__ import annotations

import pytest

from fim_agent.rag.base import BaseRetriever, Document
from fim_agent.rag.retriever.hybrid import HybridRetriever
from fim_agent.core.reranker.base import BaseReranker, RerankResult


class FakeRetriever(BaseRetriever):
    """Mock retriever returning predefined docs."""

    def __init__(self, docs: list[Document]) -> None:
        self._docs = docs

    async def retrieve(self, query: str, *, top_k: int = 5) -> list[Document]:
        return self._docs[:top_k]


class FailingRetriever(BaseRetriever):
    """Retriever that always fails."""

    async def retrieve(self, query: str, *, top_k: int = 5) -> list[Document]:
        raise RuntimeError("FTS unavailable")


class FakeReranker(BaseReranker):
    """Mock reranker that reverses the order."""

    async def rerank(
        self, query: str, documents: list[str], *, top_k: int = 5
    ) -> list[RerankResult]:
        results = [
            RerankResult(index=i, score=1.0 - i * 0.1, text=doc)
            for i, doc in enumerate(reversed(documents))
        ]
        return results[:top_k]


async def test_hybrid_rrf_fusion():
    dense = FakeRetriever([
        Document(content="doc_a", score=0.9),
        Document(content="doc_b", score=0.8),
    ])
    sparse = FakeRetriever([
        Document(content="doc_b", score=0.7),
        Document(content="doc_c", score=0.6),
    ])

    hybrid = HybridRetriever(dense, sparse)
    results = await hybrid.retrieve("test", top_k=3)

    assert len(results) == 3
    # doc_b should score highest (appears in both)
    assert results[0].content == "doc_b"
    assert results[0].score is not None


async def test_hybrid_fts_degradation():
    """When FTS fails, should fall back to dense-only."""
    dense = FakeRetriever([
        Document(content="dense_doc", score=0.9),
    ])
    sparse = FailingRetriever()

    hybrid = HybridRetriever(dense, sparse)
    results = await hybrid.retrieve("test", top_k=5)

    assert len(results) == 1
    assert results[0].content == "dense_doc"


async def test_hybrid_with_reranker():
    dense = FakeRetriever([
        Document(content="first", score=0.9),
        Document(content="second", score=0.8),
    ])
    sparse = FakeRetriever([])
    reranker = FakeReranker()

    hybrid = HybridRetriever(dense, sparse, reranker=reranker)
    results = await hybrid.retrieve("test", top_k=2)

    assert len(results) == 2
    # Reranker reverses order, so scores should reflect that
    assert all(r.score is not None for r in results)


async def test_hybrid_empty():
    dense = FakeRetriever([])
    sparse = FakeRetriever([])

    hybrid = HybridRetriever(dense, sparse)
    results = await hybrid.retrieve("test")

    assert results == []


async def test_hybrid_top_k():
    docs = [Document(content=f"doc_{i}", score=1.0 - i * 0.1) for i in range(10)]
    dense = FakeRetriever(docs)
    sparse = FakeRetriever([])

    hybrid = HybridRetriever(dense, sparse)
    results = await hybrid.retrieve("test", top_k=3)

    assert len(results) == 3


# ------------------------------------------------------------------
# Score tracing tests
# ------------------------------------------------------------------


async def test_hybrid_score_tracing():
    """Verify that fused results carry original scores and ranks."""
    dense = FakeRetriever([
        Document(content="doc_a", score=0.9),
        Document(content="doc_b", score=0.8),
    ])
    sparse = FakeRetriever([
        Document(content="doc_b", score=0.7),
        Document(content="doc_c", score=0.6),
    ])

    hybrid = HybridRetriever(dense, sparse)
    results = await hybrid.retrieve("test", top_k=5)

    # Build lookup
    by_content = {r.content: r for r in results}

    # doc_a: only in dense (rank 0, score 0.9)
    a = by_content["doc_a"]
    assert a.vector_score == 0.9
    assert a.vector_rank == 0
    assert a.fts_score is None
    assert a.fts_rank is None

    # doc_b: in dense (rank 1, score 0.8) AND sparse (rank 0, score 0.7)
    b = by_content["doc_b"]
    assert b.vector_score == 0.8
    assert b.vector_rank == 1
    assert b.fts_score == 0.7
    assert b.fts_rank == 0

    # doc_c: only in sparse (rank 1, score 0.6)
    c = by_content["doc_c"]
    assert c.vector_score is None
    assert c.vector_rank is None
    assert c.fts_score == 0.6
    assert c.fts_rank == 1


# ------------------------------------------------------------------
# Linear fusion tests
# ------------------------------------------------------------------


async def test_hybrid_linear_fusion():
    """Linear fusion: doc in both lists should score higher than unique docs."""
    dense = FakeRetriever([
        Document(content="shared", score=0.9),
        Document(content="dense_only", score=0.5),
    ])
    sparse = FakeRetriever([
        Document(content="shared", score=0.8),
        Document(content="sparse_only", score=0.4),
    ])

    hybrid = HybridRetriever(dense, sparse, fusion_mode="linear")
    results = await hybrid.retrieve("test", top_k=5)

    assert len(results) == 3
    # "shared" appears in both lists, so it should be ranked first
    assert results[0].content == "shared"
    assert results[0].score is not None
    # Verify score tracing is populated
    assert results[0].vector_score == 0.9
    assert results[0].fts_score == 0.8
    assert results[0].vector_rank == 0
    assert results[0].fts_rank == 0


async def test_hybrid_linear_with_reranker():
    """Linear fusion + reranker: score tracing should be preserved after rerank."""
    dense = FakeRetriever([
        Document(content="first", score=0.9),
        Document(content="second", score=0.7),
    ])
    sparse = FakeRetriever([
        Document(content="second", score=0.8),
        Document(content="third", score=0.5),
    ])
    reranker = FakeReranker()

    hybrid = HybridRetriever(
        dense, sparse, reranker=reranker, fusion_mode="linear"
    )
    results = await hybrid.retrieve("test", top_k=3)

    assert len(results) == 3
    # All results should have reranker-assigned scores
    assert all(r.score is not None for r in results)
    # Score tracing must survive through the rerank step
    by_content = {r.content: r for r in results}

    # "first" only in dense
    first = by_content["first"]
    assert first.vector_score == 0.9
    assert first.vector_rank == 0
    assert first.fts_score is None
    assert first.fts_rank is None

    # "second" in both
    second = by_content["second"]
    assert second.vector_score == 0.7
    assert second.vector_rank == 1
    assert second.fts_score == 0.8
    assert second.fts_rank == 0

    # "third" only in sparse
    third = by_content["third"]
    assert third.vector_score is None
    assert third.vector_rank is None
    assert third.fts_score == 0.5
    assert third.fts_rank == 1

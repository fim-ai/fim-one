"""Tests for the Grounding Pipeline."""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock

import pytest

from fim_agent.rag.base import Document
from fim_agent.rag.grounding import (
    Citation,
    Conflict,
    EvidenceUnit,
    GroundedResult,
    GroundingPipeline,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_doc(content: str, score: float, metadata: dict | None = None) -> Document:
    return Document(content=content, metadata=metadata or {}, score=score)


def _mock_kb_manager(results_per_kb: dict[str, list[Document]]) -> AsyncMock:
    manager = AsyncMock()

    async def _retrieve(query, *, kb_id, user_id, top_k=5):
        return results_per_kb.get(kb_id, [])

    manager.retrieve = AsyncMock(side_effect=_retrieve)
    return manager


def _mock_embedding(dim: int = 3) -> AsyncMock:
    emb = AsyncMock()
    emb.dimension = dim

    async def _embed_query(query: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    async def _embed_texts(texts: list[str]) -> list[list[float]]:
        # Return slightly different vectors for each text
        vecs = []
        for i, _ in enumerate(texts):
            angle = i * 0.1
            vecs.append([math.cos(angle), math.sin(angle), 0.0])
        return vecs

    emb.embed_query = AsyncMock(side_effect=_embed_query)
    emb.embed_texts = AsyncMock(side_effect=_embed_texts)
    return emb


# ---------------------------------------------------------------------------
# Tests: GroundedResult basics
# ---------------------------------------------------------------------------


def test_grounded_result_empty():
    result = GroundedResult()
    assert result.confidence == 0.0
    assert result.evidence == []
    assert result.conflicts == []
    assert result.total_sources == 0


# ---------------------------------------------------------------------------
# Tests: Confidence computation
# ---------------------------------------------------------------------------


def test_confidence_computation():
    pipeline = GroundingPipeline(
        kb_manager=AsyncMock(),
        embedding=AsyncMock(),
        config={"top_k": 10},
    )

    units = [
        EvidenceUnit(chunk=_make_doc("a", 0.8), query_alignment=0.9, rank=0),
        EvidenceUnit(chunk=_make_doc("b", 0.7), query_alignment=0.8, rank=1),
        EvidenceUnit(chunk=_make_doc("c", 0.6), query_alignment=0.7, rank=2),
    ]

    confidence = pipeline._compute_confidence(units)

    avg_retrieval = (0.8 + 0.7 + 0.6) / 3
    avg_alignment = (0.9 + 0.8 + 0.7) / 3
    coverage = min(3 / 10, 1.0)
    expected = (avg_retrieval * 0.4) + (avg_alignment * 0.4) + (coverage * 0.2)

    assert abs(confidence - expected) < 1e-9


def test_confidence_empty():
    pipeline = GroundingPipeline(
        kb_manager=AsyncMock(),
        embedding=AsyncMock(),
    )
    assert pipeline._compute_confidence([]) == 0.0


def test_confidence_coverage_capped_at_1():
    """When evidence count exceeds top_k, coverage should be capped at 1.0."""
    pipeline = GroundingPipeline(
        kb_manager=AsyncMock(),
        embedding=AsyncMock(),
        config={"top_k": 2},
    )

    units = [
        EvidenceUnit(chunk=_make_doc("a", 1.0), query_alignment=1.0, rank=0),
        EvidenceUnit(chunk=_make_doc("b", 1.0), query_alignment=1.0, rank=1),
        EvidenceUnit(chunk=_make_doc("c", 1.0), query_alignment=1.0, rank=2),
    ]

    # coverage = min(3/2, 1.0) = 1.0
    # confidence = (1.0 * 0.4) + (1.0 * 0.4) + (1.0 * 0.2) = 1.0
    assert pipeline._compute_confidence(units) == pytest.approx(1.0, abs=1e-6)


def test_confidence_single_evidence():
    """Single evidence unit should use that unit alone for top-3 averages."""
    pipeline = GroundingPipeline(
        kb_manager=AsyncMock(),
        embedding=AsyncMock(),
        config={"top_k": 5},
    )

    units = [
        EvidenceUnit(chunk=_make_doc("a", 0.5), query_alignment=0.6, rank=0),
    ]

    # avg top3 (only 1): retrieval=0.5, alignment=0.6
    # coverage = 1/5 = 0.2
    expected = (0.5 * 0.4) + (0.6 * 0.4) + (0.2 * 0.2)
    assert pipeline._compute_confidence(units) == pytest.approx(expected, abs=1e-6)


# ---------------------------------------------------------------------------
# Tests: Multi-KB retrieval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_kb_retrieval():
    docs_kb1 = [
        _make_doc("doc1", 0.9, {"chunk_id": "c1", "document_id": "d1"}),
        _make_doc("doc2", 0.5, {"chunk_id": "c2", "document_id": "d2"}),
    ]
    docs_kb2 = [
        _make_doc("doc3", 0.7, {"chunk_id": "c3", "document_id": "d3"}),
    ]

    manager = _mock_kb_manager({"kb1": docs_kb1, "kb2": docs_kb2})
    embedding = _mock_embedding()

    pipeline = GroundingPipeline(
        kb_manager=manager,
        embedding=embedding,
        config={"top_k": 10, "min_score": 0.3},
    )

    evidence = await pipeline._multi_kb_retrieve("test query", ["kb1", "kb2"], "user1")

    assert len(evidence) == 3
    # Sorted by score descending
    assert evidence[0].chunk.score == 0.9
    assert evidence[1].chunk.score == 0.7
    assert evidence[2].chunk.score == 0.5
    # KB IDs assigned
    assert evidence[0].kb_id == "kb1"
    assert evidence[1].kb_id == "kb2"
    assert evidence[2].kb_id == "kb1"


@pytest.mark.asyncio
async def test_min_score_filter():
    docs = [
        _make_doc("above", 0.6),
        _make_doc("below", 0.2),
    ]
    manager = _mock_kb_manager({"kb1": docs})
    embedding = _mock_embedding()

    pipeline = GroundingPipeline(
        kb_manager=manager,
        embedding=embedding,
        config={"top_k": 10, "min_score": 0.5},
    )

    evidence = await pipeline._multi_kb_retrieve("q", ["kb1"], "user1")
    assert len(evidence) == 1
    assert evidence[0].chunk.content == "above"


# ---------------------------------------------------------------------------
# Tests: Citation extraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_citation_extraction_with_llm():
    content = "Python is a programming language. It was created by Guido van Rossum."
    doc = _make_doc(content, 0.9, {
        "chunk_id": "c1",
        "document_id": "d1",
        "source": "python_intro.pdf",
    })

    llm = AsyncMock()
    llm_response = MagicMock()
    llm_response.message.content = (
        '[{"text": "Python is a programming language", "char_offset": 0}]'
    )
    llm.chat = AsyncMock(return_value=llm_response)

    embedding = _mock_embedding()
    manager = AsyncMock()

    pipeline = GroundingPipeline(
        kb_manager=manager,
        embedding=embedding,
        llm=llm,
    )

    unit = EvidenceUnit(chunk=doc, kb_id="kb1", rank=0)
    await pipeline._extract_citations("What is Python?", [unit])

    assert len(unit.citations) == 1
    cit = unit.citations[0]
    assert cit.text == "Python is a programming language"
    assert cit.document_id == "d1"
    assert cit.kb_id == "kb1"
    assert cit.chunk_id == "c1"
    assert cit.source_name == "python_intro.pdf"
    assert cit.char_offset == 0


@pytest.mark.asyncio
async def test_citation_extraction_fallback():
    content = "First sentence. Second sentence. Third sentence."
    doc = _make_doc(content, 0.8, {
        "chunk_id": "c1",
        "document_id": "d1",
        "source": "test.txt",
    })

    embedding = _mock_embedding()
    manager = AsyncMock()

    pipeline = GroundingPipeline(
        kb_manager=manager,
        embedding=embedding,
        llm=None,  # No LLM → fallback mode
    )

    unit = EvidenceUnit(chunk=doc, kb_id="kb1", rank=0)
    await pipeline._extract_citations("query", [unit])

    assert len(unit.citations) <= 3
    for cit in unit.citations:
        assert cit.text in content
        assert cit.document_id == "d1"
        assert cit.source_name == "test.txt"


# ---------------------------------------------------------------------------
# Tests: Conflict detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conflict_detection():
    """Citations from different docs with high cosine similarity but low text overlap → conflict."""
    cit_a = Citation(
        text="The project started in 2020",
        document_id="d1",
        kb_id="kb1",
        chunk_id="c1",
        source_name="report_a.pdf",
    )
    cit_b = Citation(
        text="The initiative began in 2019",
        document_id="d2",
        kb_id="kb1",
        chunk_id="c2",
        source_name="report_b.pdf",
    )

    unit_a = EvidenceUnit(
        chunk=_make_doc("text a", 0.9),
        citations=[cit_a],
        kb_id="kb1",
    )
    unit_b = EvidenceUnit(
        chunk=_make_doc("text b", 0.8),
        citations=[cit_b],
        kb_id="kb1",
    )

    # Mock embedding to return nearly identical vectors for both citations
    embedding = AsyncMock()

    async def _embed_texts(texts):
        return [[1.0, 0.0, 0.0] for _ in texts]

    embedding.embed_texts = AsyncMock(side_effect=_embed_texts)

    manager = AsyncMock()
    pipeline = GroundingPipeline(
        kb_manager=manager,
        embedding=embedding,
        config={"conflict_threshold": 0.85},
    )

    conflicts = await pipeline._detect_conflicts([unit_a, unit_b])
    assert len(conflicts) == 1
    assert conflicts[0].similarity >= 0.85


@pytest.mark.asyncio
async def test_no_conflict_same_doc():
    """Citations from the same document_id should never be flagged as conflicts."""
    cit_a = Citation(
        text="Claim A",
        document_id="d1",  # Same doc
        kb_id="kb1",
        chunk_id="c1",
        source_name="doc.pdf",
    )
    cit_b = Citation(
        text="Claim B",
        document_id="d1",  # Same doc
        kb_id="kb1",
        chunk_id="c2",
        source_name="doc.pdf",
    )

    unit = EvidenceUnit(
        chunk=_make_doc("content", 0.9),
        citations=[cit_a, cit_b],
        kb_id="kb1",
    )

    embedding = AsyncMock()
    embedding.embed_texts = AsyncMock(return_value=[[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]])

    pipeline = GroundingPipeline(
        kb_manager=AsyncMock(),
        embedding=embedding,
        config={"conflict_threshold": 0.85},
    )

    conflicts = await pipeline._detect_conflicts([unit])
    assert len(conflicts) == 0


# ---------------------------------------------------------------------------
# Tests: Cosine similarity helper
# ---------------------------------------------------------------------------


def test_cosine_similarity():
    sim = GroundingPipeline._cosine_similarity

    # Identical vectors
    assert abs(sim([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-9

    # Orthogonal vectors
    assert abs(sim([1.0, 0.0], [0.0, 1.0]) - 0.0) < 1e-9

    # Opposite vectors
    assert abs(sim([1.0, 0.0], [-1.0, 0.0]) - (-1.0)) < 1e-9

    # Zero vector
    assert sim([0.0, 0.0], [1.0, 0.0]) == 0.0


# ---------------------------------------------------------------------------
# Tests: Text overlap helper
# ---------------------------------------------------------------------------


def test_text_overlap():
    overlap = GroundingPipeline._text_overlap

    # Identical text
    assert overlap("hello world", "hello world") == pytest.approx(1.0)

    # No overlap
    assert overlap("alpha beta", "gamma delta") == pytest.approx(0.0)

    # Partial overlap
    assert overlap("hello world foo", "hello bar foo") == pytest.approx(2 / 4)

    # Empty strings
    assert overlap("", "") == pytest.approx(1.0)

    # One empty
    assert overlap("hello", "") == pytest.approx(0.0)

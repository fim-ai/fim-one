"""Tests for the Grounding Pipeline."""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock

import pytest

from fim_one.rag.base import Document
from fim_one.rag.grounding import (
    Citation,
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
        EvidenceUnit(chunk=_make_doc("a", 0.8), rank=0),
        EvidenceUnit(chunk=_make_doc("b", 0.7), rank=1),
        EvidenceUnit(chunk=_make_doc("c", 0.6), rank=2),
    ]

    confidence = pipeline._compute_confidence(units)

    expected = (0.8 + 0.7 + 0.6) / 3

    assert abs(confidence - expected) < 1e-9


def test_confidence_empty():
    pipeline = GroundingPipeline(
        kb_manager=AsyncMock(),
        embedding=AsyncMock(),
    )
    assert pipeline._compute_confidence([]) == 0.0


def test_confidence_many_units_uses_top3():
    """With 3+ units, confidence is the mean of the top 3 scores."""
    pipeline = GroundingPipeline(
        kb_manager=AsyncMock(),
        embedding=AsyncMock(),
        config={"top_k": 2},
    )

    units = [
        EvidenceUnit(chunk=_make_doc("a", 1.0), rank=0),
        EvidenceUnit(chunk=_make_doc("b", 1.0), rank=1),
        EvidenceUnit(chunk=_make_doc("c", 1.0), rank=2),
    ]

    # confidence = (1.0 + 1.0 + 1.0) / 3 = 1.0
    assert pipeline._compute_confidence(units) == pytest.approx(1.0, abs=1e-6)


def test_confidence_single_evidence():
    """Single evidence unit should use that unit alone for top-3 averages."""
    pipeline = GroundingPipeline(
        kb_manager=AsyncMock(),
        embedding=AsyncMock(),
        config={"top_k": 5},
    )

    units = [
        EvidenceUnit(chunk=_make_doc("a", 0.5), rank=0),
    ]

    # Only 1 unit: confidence = 0.5 / 1 = 0.5
    expected = 0.5
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
    doc = _make_doc(
        content,
        0.9,
        {
            "chunk_id": "c1",
            "document_id": "d1",
            "source": "python_intro.pdf",
        },
    )

    llm = AsyncMock()
    llm_response = MagicMock()
    # Batched format: JSON object keyed by chunk index
    llm_response.message.content = (
        '{"0": [{"text": "Python is a programming language", "char_offset": 0}]}'
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
async def test_citation_extraction_with_fenced_json():
    """LLM response wrapped in ```json ... ``` fences should be parsed correctly."""
    content = "Python is a programming language. It was created by Guido van Rossum."
    doc = _make_doc(
        content,
        0.9,
        {
            "chunk_id": "c1",
            "document_id": "d1",
            "source": "python_intro.pdf",
        },
    )

    llm = AsyncMock()
    llm_response = MagicMock()
    llm_response.message.content = (
        "```json\n"
        '{"0": [{"text": "Python is a programming language", "char_offset": 0}]}\n'
        "```"
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
    assert unit.citations[0].text == "Python is a programming language"


@pytest.mark.asyncio
async def test_citation_extraction_with_prose_wrapped_json():
    """LLM response with JSON embedded in prose should be parsed correctly."""
    content = "Python is a programming language. It was created by Guido van Rossum."
    doc = _make_doc(
        content,
        0.9,
        {
            "chunk_id": "c1",
            "document_id": "d1",
            "source": "python_intro.pdf",
        },
    )

    llm = AsyncMock()
    llm_response = MagicMock()
    llm_response.message.content = (
        "Here are the citations:\n"
        '{"0": [{"text": "Python is a programming language", "char_offset": 0}]}\n'
        "Hope this helps!"
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
    assert unit.citations[0].text == "Python is a programming language"


@pytest.mark.asyncio
async def test_citation_extraction_unparseable_returns_empty():
    """When LLM returns completely unparseable content, citations should remain empty."""
    content = "Some document content."
    doc = _make_doc(
        content,
        0.9,
        {
            "chunk_id": "c1",
            "document_id": "d1",
            "source": "test.pdf",
        },
    )

    llm = AsyncMock()
    llm_response = MagicMock()
    llm_response.message.content = "This is not valid JSON at all."
    llm.chat = AsyncMock(return_value=llm_response)

    embedding = _mock_embedding()
    manager = AsyncMock()

    pipeline = GroundingPipeline(
        kb_manager=manager,
        embedding=embedding,
        llm=llm,
    )

    unit = EvidenceUnit(chunk=doc, kb_id="kb1", rank=0)
    await pipeline._extract_citations("query", [unit])

    assert len(unit.citations) == 0


@pytest.mark.asyncio
async def test_citation_extraction_fallback():
    content = "First sentence. Second sentence. Third sentence."
    doc = _make_doc(
        content,
        0.8,
        {
            "chunk_id": "c1",
            "document_id": "d1",
            "source": "test.txt",
        },
    )

    embedding = _mock_embedding()
    manager = AsyncMock()

    pipeline = GroundingPipeline(
        kb_manager=manager,
        embedding=embedding,
        llm=None,  # No LLM -> fallback mode
    )

    unit = EvidenceUnit(chunk=doc, kb_id="kb1", rank=0)
    await pipeline._extract_citations("query", [unit])

    assert len(unit.citations) <= 3
    for cit in unit.citations:
        assert cit.text in content
        assert cit.document_id == "d1"
        assert cit.source_name == "test.txt"


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

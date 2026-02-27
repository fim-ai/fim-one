"""Tests for the LanceDB vector store."""

import pytest
from pathlib import Path

from fim_agent.rag.store.lancedb import LanceDBVectorStore


@pytest.fixture
def store(tmp_path: Path) -> LanceDBVectorStore:
    return LanceDBVectorStore(base_dir=tmp_path, embedding_dim=4)


async def test_add_and_vector_search(store: LanceDBVectorStore):
    texts = ["hello world", "foo bar baz"]
    vectors = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]
    metadatas = [{"page": 1}, {"page": 2}]

    added = await store.add_documents(
        texts, vectors, metadatas,
        kb_id="kb1", user_id="u1", document_id="doc1",
    )
    assert added == 2

    # Search with a vector close to first doc
    results = await store.vector_search(
        [0.9, 0.1, 0.0, 0.0],
        kb_id="kb1", user_id="u1", top_k=2,
    )
    assert len(results) == 2
    assert results[0].content == "hello world"
    assert results[0].score is not None
    assert results[0].score > 0


async def test_content_dedup(store: LanceDBVectorStore):
    """Same text should not be added twice."""
    texts = ["duplicate text"]
    vectors = [[1.0, 0.0, 0.0, 0.0]]
    metadatas = [{"page": 1}]

    added1 = await store.add_documents(
        texts, vectors, metadatas,
        kb_id="kb1", user_id="u1", document_id="doc1",
    )
    assert added1 == 1

    added2 = await store.add_documents(
        texts, vectors, metadatas,
        kb_id="kb1", user_id="u1", document_id="doc1",
    )
    assert added2 == 0  # Deduped

    count = await store.count(kb_id="kb1", user_id="u1")
    assert count == 1


async def test_delete_by_document(store: LanceDBVectorStore):
    await store.add_documents(
        ["chunk1", "chunk2"], [[1.0, 0, 0, 0], [0, 1.0, 0, 0]],
        [{"p": 1}, {"p": 2}],
        kb_id="kb1", user_id="u1", document_id="doc1",
    )
    await store.add_documents(
        ["chunk3"], [[0, 0, 1.0, 0]], [{"p": 1}],
        kb_id="kb1", user_id="u1", document_id="doc2",
    )

    deleted = await store.delete_by_document(
        kb_id="kb1", user_id="u1", document_id="doc1",
    )
    assert deleted == 2

    count = await store.count(kb_id="kb1", user_id="u1")
    assert count == 1


async def test_delete_kb(store: LanceDBVectorStore):
    await store.add_documents(
        ["text"], [[1.0, 0, 0, 0]], [{}],
        kb_id="kb1", user_id="u1", document_id="doc1",
    )

    await store.delete_kb(kb_id="kb1", user_id="u1")
    count = await store.count(kb_id="kb1", user_id="u1")
    assert count == 0


async def test_empty_search(store: LanceDBVectorStore):
    results = await store.vector_search(
        [1.0, 0, 0, 0], kb_id="kb1", user_id="u1",
    )
    assert results == []


async def test_fts_search(store: LanceDBVectorStore):
    await store.add_documents(
        ["the quick brown fox", "lazy dog sleeps"],
        [[1.0, 0, 0, 0], [0, 1.0, 0, 0]],
        [{}, {}],
        kb_id="kb1", user_id="u1", document_id="doc1",
    )

    results = await store.fts_search(
        "quick fox", kb_id="kb1", user_id="u1", top_k=5,
    )
    # FTS should find at least the first document
    assert len(results) >= 1
    assert any("quick" in r.content for r in results)


async def test_data_isolation(store: LanceDBVectorStore):
    """Different users/KBs should be isolated."""
    await store.add_documents(
        ["user1 data"], [[1.0, 0, 0, 0]], [{}],
        kb_id="kb1", user_id="u1", document_id="doc1",
    )
    await store.add_documents(
        ["user2 data"], [[0, 1.0, 0, 0]], [{}],
        kb_id="kb1", user_id="u2", document_id="doc1",
    )

    count_u1 = await store.count(kb_id="kb1", user_id="u1")
    count_u2 = await store.count(kb_id="kb1", user_id="u2")
    assert count_u1 == 1
    assert count_u2 == 1

"""Tests for chunking strategies."""

import pytest

from fim_agent.rag.chunking import get_chunker, Chunk
from fim_agent.rag.chunking.fixed import FixedSizeChunker
from fim_agent.rag.chunking.recursive import RecursiveCharacterChunker
from fim_agent.rag.chunking.semantic import SemanticChunker


async def test_fixed_chunker_basic():
    chunker = FixedSizeChunker(chunk_size=20, overlap=5)
    chunks = await chunker.chunk("A" * 50)
    assert len(chunks) > 1
    assert all(isinstance(c, Chunk) for c in chunks)


async def test_fixed_chunker_small_text():
    chunker = FixedSizeChunker(chunk_size=100, overlap=20)
    chunks = await chunker.chunk("Hello world")
    assert len(chunks) == 1
    assert chunks[0].text == "Hello world"


async def test_fixed_chunker_empty():
    chunker = FixedSizeChunker()
    chunks = await chunker.chunk("")
    assert chunks == []


async def test_fixed_chunker_overlap_validation():
    with pytest.raises(ValueError, match="overlap must be less"):
        FixedSizeChunker(chunk_size=10, overlap=10)


async def test_fixed_chunker_metadata():
    chunker = FixedSizeChunker(chunk_size=20, overlap=0)
    chunks = await chunker.chunk("A" * 50, metadata={"source": "test.txt"})
    assert all(c.metadata.get("source") == "test.txt" for c in chunks)
    assert all(c.metadata.get("chunk_strategy") == "fixed" for c in chunks)


async def test_recursive_chunker_basic():
    text = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
    chunker = RecursiveCharacterChunker(chunk_size=30, overlap=0)
    chunks = await chunker.chunk(text)
    assert len(chunks) >= 2


async def test_recursive_chunker_small_text():
    chunker = RecursiveCharacterChunker(chunk_size=100, overlap=0)
    chunks = await chunker.chunk("Short text")
    assert len(chunks) == 1


async def test_recursive_chunker_empty():
    chunker = RecursiveCharacterChunker()
    chunks = await chunker.chunk("")
    assert chunks == []


async def test_recursive_chunker_chinese():
    text = "\u7b2c\u4e00\u6bb5\u5185\u5bb9\u3002\u7b2c\u4e8c\u6bb5\u5185\u5bb9\u3002\u7b2c\u4e09\u6bb5\u5185\u5bb9\u3002"
    chunker = RecursiveCharacterChunker(chunk_size=15, overlap=0)
    chunks = await chunker.chunk(text)
    assert len(chunks) >= 1


async def test_semantic_chunker_fallback():
    """Without an embedding model, should fall back to paragraph splitting."""
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    chunker = SemanticChunker(embedding=None)
    chunks = await chunker.chunk(text)
    assert len(chunks) == 3


async def test_semantic_chunker_empty():
    chunker = SemanticChunker()
    chunks = await chunker.chunk("")
    assert chunks == []


async def test_get_chunker_factory():
    chunker = get_chunker("fixed", chunk_size=500, overlap=50)
    assert isinstance(chunker, FixedSizeChunker)

    chunker = get_chunker("recursive")
    assert isinstance(chunker, RecursiveCharacterChunker)


async def test_get_chunker_unknown():
    with pytest.raises(ValueError, match="Unknown"):
        get_chunker("nonexistent")


async def test_chunk_indexes():
    chunker = FixedSizeChunker(chunk_size=10, overlap=0)
    chunks = await chunker.chunk("A" * 30)
    indexes = [c.index for c in chunks]
    assert indexes == list(range(len(chunks)))

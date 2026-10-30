"""Tests for chunking strategies."""

import pytest

from fim_one.rag.chunking import MAX_CHUNK_SIZE, get_chunker, Chunk
from fim_one.rag.chunking.fixed import FixedSizeChunker
from fim_one.rag.chunking.markdown import MarkdownChunker
from fim_one.rag.chunking.recursive import RecursiveCharacterChunker
from fim_one.rag.chunking.semantic import SemanticChunker


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


@pytest.mark.parametrize(
    "chunker_cls",
    [RecursiveCharacterChunker, MarkdownChunker],
    ids=["recursive", "markdown"],
)
@pytest.mark.parametrize(
    "text",
    [
        pytest.param("A" * 53, id="ascii"),
        pytest.param("\u6c49" * 53, id="cjk"),
        pytest.param("customerOrderIdentifier0123456789" * 3, id="identifier"),
    ],
)
async def test_character_fallback_preserves_unbroken_text(chunker_cls, text):
    chunk_size = 16
    chunker = chunker_cls(chunk_size=chunk_size, overlap=0)

    chunks = await chunker.chunk(text)

    assert len(chunks) > 1
    assert "".join(chunk.text for chunk in chunks) == text
    assert all(0 < len(chunk.text) <= chunk_size for chunk in chunks)


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


# ------------------------------------------------------------------
# MAX_CHUNK_SIZE validation
# ------------------------------------------------------------------


async def test_max_chunk_size_constant():
    """MAX_CHUNK_SIZE is 6000 (conservative limit for Jina Embeddings v3)."""
    assert MAX_CHUNK_SIZE == 6000


async def test_fixed_chunker_exceeds_max():
    with pytest.raises(ValueError, match="exceeds MAX_CHUNK_SIZE"):
        FixedSizeChunker(chunk_size=MAX_CHUNK_SIZE + 1, overlap=0)


async def test_fixed_chunker_at_max():
    """Exactly MAX_CHUNK_SIZE should be accepted."""
    chunker = FixedSizeChunker(chunk_size=MAX_CHUNK_SIZE, overlap=0)
    assert chunker._chunk_size == MAX_CHUNK_SIZE


async def test_recursive_chunker_exceeds_max():
    with pytest.raises(ValueError, match="exceeds MAX_CHUNK_SIZE"):
        RecursiveCharacterChunker(chunk_size=MAX_CHUNK_SIZE + 1, overlap=0)


async def test_recursive_chunker_at_max():
    chunker = RecursiveCharacterChunker(chunk_size=MAX_CHUNK_SIZE, overlap=0)
    assert chunker._chunk_size == MAX_CHUNK_SIZE


async def test_markdown_chunker_exceeds_max():
    with pytest.raises(ValueError, match="exceeds MAX_CHUNK_SIZE"):
        MarkdownChunker(chunk_size=MAX_CHUNK_SIZE + 1, overlap=0)


async def test_markdown_chunker_at_max():
    chunker = MarkdownChunker(chunk_size=MAX_CHUNK_SIZE, overlap=0)
    assert chunker._chunk_size == MAX_CHUNK_SIZE


async def test_get_chunker_factory_exceeds_max():
    """Factory function propagates the ValueError from the chunker."""
    with pytest.raises(ValueError, match="exceeds MAX_CHUNK_SIZE"):
        get_chunker("fixed", chunk_size=MAX_CHUNK_SIZE + 1, overlap=0)

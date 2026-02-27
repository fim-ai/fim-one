"""Tests for the Markdown-aware chunker."""

import pytest

from fim_agent.rag.chunking import get_chunker, Chunk
from fim_agent.rag.chunking.markdown import MarkdownChunker


async def test_markdown_splits_by_headers():
    """Document with # H1 and ## H2 sections produces separate chunks with correct metadata."""
    text = (
        "# Introduction\n"
        "This is the intro paragraph.\n"
        "\n"
        "## Details\n"
        "Here are some details about the topic.\n"
        "\n"
        "## Conclusion\n"
        "This wraps it up.\n"
    )
    chunker = MarkdownChunker(chunk_size=500, overlap=0)
    chunks = await chunker.chunk(text)

    assert len(chunks) == 3

    assert chunks[0].metadata["section"] == "# Introduction"
    assert chunks[0].metadata["chunk_strategy"] == "markdown"
    assert "This is the intro paragraph." in chunks[0].text

    assert chunks[1].metadata["section"] == "## Details"
    assert "Here are some details" in chunks[1].text

    assert chunks[2].metadata["section"] == "## Conclusion"
    assert "This wraps it up." in chunks[2].text


async def test_markdown_large_section_recursive_split():
    """A section longer than chunk_size gets recursively split."""
    body = "Some content. " * 100  # ~1400 chars
    text = f"# Big Section\n{body}"
    chunker = MarkdownChunker(chunk_size=200, overlap=0)
    chunks = await chunker.chunk(text)

    # Must have been split into multiple chunks
    assert len(chunks) > 1

    # All chunks belong to the same section
    for c in chunks:
        assert c.metadata["section"] == "# Big Section"
        assert c.metadata["chunk_strategy"] == "markdown"

    # Indices are sequential
    assert [c.index for c in chunks] == list(range(len(chunks)))


async def test_markdown_no_headers_fallback():
    """Plain text without headers falls back to recursive splitting."""
    text = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
    chunker = MarkdownChunker(chunk_size=30, overlap=0)
    chunks = await chunker.chunk(text)

    assert len(chunks) >= 2
    for c in chunks:
        assert c.metadata["chunk_strategy"] == "markdown"
        # No section key when there are no headers
        assert "section" not in c.metadata


async def test_markdown_empty_text():
    """Empty or whitespace-only input returns an empty list."""
    chunker = MarkdownChunker()
    assert await chunker.chunk("") == []
    assert await chunker.chunk("   ") == []
    assert await chunker.chunk("\n\n") == []


async def test_markdown_preserves_header_in_chunk():
    """The header line is included in the chunk text."""
    text = "# My Header\nSome body text."
    chunker = MarkdownChunker(chunk_size=500, overlap=0)
    chunks = await chunker.chunk(text)

    assert len(chunks) == 1
    assert chunks[0].text.startswith("# My Header")
    assert "Some body text." in chunks[0].text


async def test_markdown_overlap():
    """Overlap is applied between chunks within a section."""
    body = "Word " * 200  # ~1000 chars
    text = f"# Section\n{body}"
    overlap = 50
    chunker = MarkdownChunker(chunk_size=200, overlap=overlap)
    chunks = await chunker.chunk(text)

    assert len(chunks) > 1

    # Second chunk should start with the tail of the first chunk (overlap)
    for i in range(1, len(chunks)):
        # The overlap text from the previous raw chunk should appear at the
        # start of the current chunk.  We verify by checking that the first
        # `overlap` characters of chunk[i] come from the tail of the
        # previous chunk's content.
        prev_tail = chunks[i - 1].text[-overlap:]
        assert chunks[i].text.startswith(prev_tail), (
            f"Chunk {i} should begin with the last {overlap} chars of chunk {i - 1}"
        )


async def test_markdown_factory():
    """get_chunker('markdown') returns a MarkdownChunker."""
    chunker = get_chunker("markdown", chunk_size=500, overlap=50)
    assert isinstance(chunker, MarkdownChunker)


async def test_markdown_preamble_before_first_header():
    """Content before the first header is captured in its own chunk."""
    text = "Some preamble text.\n\n# First Header\nBody of first section."
    chunker = MarkdownChunker(chunk_size=500, overlap=0)
    chunks = await chunker.chunk(text)

    assert len(chunks) == 2
    assert "Some preamble text." in chunks[0].text
    assert "section" not in chunks[0].metadata  # preamble has no header
    assert chunks[1].metadata["section"] == "# First Header"


async def test_markdown_overlap_validation():
    """overlap >= chunk_size raises ValueError."""
    with pytest.raises(ValueError, match="overlap must be less"):
        MarkdownChunker(chunk_size=100, overlap=100)


async def test_markdown_metadata_passthrough():
    """User-supplied metadata is preserved on every chunk."""
    text = "# Section A\nContent A.\n\n## Section B\nContent B."
    chunker = MarkdownChunker(chunk_size=500, overlap=0)
    chunks = await chunker.chunk(text, metadata={"source": "readme.md"})

    for c in chunks:
        assert c.metadata["source"] == "readme.md"
        assert c.metadata["chunk_strategy"] == "markdown"

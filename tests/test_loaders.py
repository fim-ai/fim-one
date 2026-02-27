"""Tests for document loaders."""

import pytest
from pathlib import Path

from fim_agent.rag.loaders import loader_for_extension, LoadedDocument
from fim_agent.rag.loaders.markdown import MarkdownLoader
from fim_agent.rag.loaders.html import HTMLLoader
from fim_agent.rag.loaders.csv import CSVLoader
from fim_agent.rag.loaders.text import TextLoader


async def test_markdown_loader(tmp_path: Path):
    f = tmp_path / "test.md"
    f.write_text("# Hello\n\nWorld", encoding="utf-8")
    docs = await MarkdownLoader().load(f)
    assert len(docs) == 1
    assert "Hello" in docs[0].content
    assert docs[0].metadata["source"] == str(f)


async def test_html_loader(tmp_path: Path):
    f = tmp_path / "test.html"
    f.write_text(
        "<html><body><h1>Title</h1><p>Content here</p></body></html>",
        encoding="utf-8",
    )
    docs = await HTMLLoader().load(f)
    assert len(docs) == 1
    assert "Title" in docs[0].content
    assert "Content here" in docs[0].content
    assert "<" not in docs[0].content  # no HTML tags


async def test_html_loader_strips_script(tmp_path: Path):
    f = tmp_path / "test.html"
    f.write_text(
        "<html><script>alert('xss')</script><body><p>Safe</p></body></html>",
        encoding="utf-8",
    )
    docs = await HTMLLoader().load(f)
    assert len(docs) == 1
    assert "alert" not in docs[0].content
    assert "Safe" in docs[0].content


async def test_csv_loader(tmp_path: Path):
    f = tmp_path / "test.csv"
    f.write_text("name,age\nAlice,30\nBob,25", encoding="utf-8")
    docs = await CSVLoader().load(f)
    assert len(docs) == 1
    assert "Alice" in docs[0].content
    assert "Bob" in docs[0].content
    assert docs[0].metadata["row_count"] == 2


async def test_text_loader(tmp_path: Path):
    f = tmp_path / "test.txt"
    f.write_text("Hello world!", encoding="utf-8")
    docs = await TextLoader().load(f)
    assert len(docs) == 1
    assert docs[0].content == "Hello world!"


async def test_empty_file(tmp_path: Path):
    f = tmp_path / "empty.md"
    f.write_text("", encoding="utf-8")
    docs = await MarkdownLoader().load(f)
    assert docs == []


async def test_loader_for_extension():
    loader = loader_for_extension(".md")
    assert isinstance(loader, MarkdownLoader)

    loader = loader_for_extension(".csv")
    assert isinstance(loader, CSVLoader)

    loader = loader_for_extension(".txt")
    assert isinstance(loader, TextLoader)


async def test_loader_for_extension_case_insensitive():
    loader = loader_for_extension(".MD")
    assert isinstance(loader, MarkdownLoader)


async def test_loader_for_extension_unsupported():
    with pytest.raises(ValueError, match="Unsupported"):
        loader_for_extension(".xyz")

"""Tests for core/web search & fetch provider abstractions."""

from __future__ import annotations

import os

import pytest

from fim_one.core.web.fetch import HttpxFetch, JinaFetch, get_web_fetcher
from fim_one.core.web.fetch.base import BaseWebFetch
from fim_one.core.web.fetch.httpx_fetch import _strip_html
from fim_one.core.web.search import (
    BraveSearch,
    ExaSearch,
    JinaSearch,
    SearchResult,
    TavilySearch,
    format_results,
    get_web_searcher,
)
from fim_one.core.web.search.base import BaseWebSearch
from fim_one.core.web.search.jina import _parse_jina_markdown
from fim_one.core.reranker.cohere import CohereReranker
from fim_one.core.reranker.openai import OpenAIReranker, _cosine
from fim_one.core.reranker.base import BaseReranker


# ---------------------------------------------------------------------------
# SearchResult dataclass
# ---------------------------------------------------------------------------


def test_search_result_defaults():
    r = SearchResult(title="Test", url="https://example.com", snippet="hello")
    assert r.score == 0.0


def test_search_result_with_score():
    r = SearchResult(title="T", url="u", snippet="s", score=0.9)
    assert r.score == 0.9


# ---------------------------------------------------------------------------
# format_results
# ---------------------------------------------------------------------------


def test_format_results_with_url():
    results = [SearchResult(title="Foo", url="https://foo.com", snippet="bar")]
    text = format_results(results)
    assert "## 1. [Foo](https://foo.com)" in text
    assert "bar" in text


def test_format_results_no_url():
    results = [SearchResult(title="Foo", url="", snippet="baz")]
    text = format_results(results)
    assert "## 1. Foo" in text


def test_format_results_truncation():
    results = [SearchResult(title="T", url="u", snippet="x" * 20_000)]
    text = format_results(results, max_chars=100)
    assert "Truncated" in text


def test_format_results_multiple():
    results = [
        SearchResult(title="A", url="u1", snippet="s1"),
        SearchResult(title="B", url="u2", snippet="s2"),
    ]
    text = format_results(results)
    assert "## 1." in text
    assert "## 2." in text
    assert "---" in text


# ---------------------------------------------------------------------------
# Jina markdown parser
# ---------------------------------------------------------------------------

SAMPLE_JINA_MD = """\
# Search Results

## 1. [Python Docs](https://docs.python.org)
Published: 2024-01-01
The official Python documentation.

## 2. [Real Python](https://realpython.com)
Published: 2024-02-01
Tutorials and articles for Python developers.
"""


def test_parse_jina_markdown_extracts_results():
    results = _parse_jina_markdown(SAMPLE_JINA_MD, 10)
    assert len(results) == 2
    assert results[0].title == "Python Docs"
    assert results[0].url == "https://docs.python.org"
    assert "official Python documentation" in results[0].snippet


def test_parse_jina_markdown_respects_max():
    results = _parse_jina_markdown(SAMPLE_JINA_MD, 1)
    assert len(results) == 1


def test_parse_jina_markdown_fallback():
    results = _parse_jina_markdown("no matching content here", 5)
    assert len(results) == 1
    assert results[0].title == "Search Results"


# ---------------------------------------------------------------------------
# HttpxFetch HTML stripping
# ---------------------------------------------------------------------------


def test_strip_html_basic():
    html = "<html><head><title>T</title></head><body><p>Hello world</p></body></html>"
    text = _strip_html(html)
    assert "Hello world" in text


def test_strip_html_skips_script():
    html = "<html><body><script>evil()</script><p>content</p></body></html>"
    text = _strip_html(html)
    assert "evil" not in text
    assert "content" in text


def test_strip_html_skips_style():
    html = "<html><body><style>body{color:red}</style><p>text</p></body></html>"
    text = _strip_html(html)
    assert "color" not in text
    assert "text" in text


def test_strip_html_entities():
    html = "<p>&amp; &lt;foo&gt;</p>"
    text = _strip_html(html)
    assert "&" in text
    assert "<foo>" in text


# ---------------------------------------------------------------------------
# Provider factories — env var routing
# ---------------------------------------------------------------------------


def test_get_web_searcher_default_is_jina(monkeypatch):
    monkeypatch.delenv("WEB_SEARCH_PROVIDER", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    searcher = get_web_searcher()
    assert isinstance(searcher, JinaSearch)


def test_get_web_searcher_tavily_via_env_var(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    searcher = get_web_searcher()
    assert isinstance(searcher, TavilySearch)


def test_get_web_searcher_brave_via_env_var(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "brave")
    monkeypatch.setenv("BRAVE_API_KEY", "test-key")
    searcher = get_web_searcher()
    assert isinstance(searcher, BraveSearch)


def test_get_web_searcher_exa_via_env_var(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "exa")
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    searcher = get_web_searcher()
    assert isinstance(searcher, ExaSearch)


def test_get_web_searcher_exa_auto_detect(monkeypatch):
    monkeypatch.delenv("WEB_SEARCH_PROVIDER", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setenv("EXA_API_KEY", "auto-key")
    searcher = get_web_searcher()
    assert isinstance(searcher, ExaSearch)


def test_get_web_searcher_tavily_auto_detect(monkeypatch):
    monkeypatch.delenv("WEB_SEARCH_PROVIDER", raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "auto-key")
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    searcher = get_web_searcher()
    assert isinstance(searcher, TavilySearch)


def test_get_web_fetcher_default_httpx(monkeypatch):
    monkeypatch.delenv("WEB_FETCH_PROVIDER", raising=False)
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    fetcher = get_web_fetcher()
    assert isinstance(fetcher, HttpxFetch)


def test_get_web_fetcher_jina_via_key(monkeypatch):
    monkeypatch.delenv("WEB_FETCH_PROVIDER", raising=False)
    monkeypatch.setenv("JINA_API_KEY", "jina-key")
    fetcher = get_web_fetcher()
    assert isinstance(fetcher, JinaFetch)


def test_get_web_fetcher_httpx_explicit(monkeypatch):
    monkeypatch.setenv("WEB_FETCH_PROVIDER", "httpx")
    fetcher = get_web_fetcher()
    assert isinstance(fetcher, HttpxFetch)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_exa_search_is_base_web_search():
    assert isinstance(ExaSearch(api_key="fake"), BaseWebSearch)


def test_jina_search_is_base_web_search():
    assert isinstance(JinaSearch(), BaseWebSearch)


def test_jina_fetch_is_base_web_fetch():
    assert isinstance(JinaFetch(), BaseWebFetch)


def test_httpx_fetch_is_base_web_fetch():
    assert isinstance(HttpxFetch(), BaseWebFetch)


# ---------------------------------------------------------------------------
# New rerankers — instantiation & protocol
# ---------------------------------------------------------------------------


def test_cohere_reranker_is_base_reranker():
    r = CohereReranker(api_key="fake")
    assert isinstance(r, BaseReranker)


async def test_cohere_reranker_empty_docs():
    r = CohereReranker(api_key="fake")
    assert await r.rerank("q", []) == []


def test_openai_reranker_is_base_reranker(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "fake")
    r = OpenAIReranker()
    assert isinstance(r, BaseReranker)


async def test_openai_reranker_empty_docs(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "fake")
    r = OpenAIReranker()
    assert await r.rerank("q", []) == []


# ---------------------------------------------------------------------------
# Cosine similarity helper
# ---------------------------------------------------------------------------


def test_cosine_identical():
    v = [1.0, 0.0, 0.0]
    assert abs(_cosine(v, v) - 1.0) < 1e-9


def test_cosine_orthogonal():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert abs(_cosine(a, b)) < 1e-9


def test_cosine_zero_vector():
    assert _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_cosine_opposite():
    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert abs(_cosine(a, b) - (-1.0)) < 1e-9

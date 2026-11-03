"""Web abstractions — search and fetch provider protocols."""

from .fetch import BaseWebFetch, HttpxFetch, JinaFetch, KeenableFetch, get_web_fetcher
from .search import (
    BaseWebSearch,
    BraveSearch,
    ExaSearch,
    JinaSearch,
    KeenableSearch,
    SearchResult,
    TavilySearch,
    format_results,
    get_web_searcher,
)

__all__ = [
    # Search
    "BaseWebSearch",
    "SearchResult",
    "JinaSearch",
    "TavilySearch",
    "BraveSearch",
    "ExaSearch",
    "KeenableSearch",
    "get_web_searcher",
    "format_results",
    # Fetch
    "BaseWebFetch",
    "JinaFetch",
    "KeenableFetch",
    "HttpxFetch",
    "get_web_fetcher",
]

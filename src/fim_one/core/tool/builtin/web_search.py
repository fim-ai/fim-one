"""Built-in tool for searching the web.

Delegates to the configured BaseWebSearch backend (Jina, Tavily, Brave, Exa, or Keenable).
Backend selection is controlled by the WEB_SEARCH_PROVIDER environment variable.
"""

from __future__ import annotations

from typing import Any

import httpx

from fim_one.core.web.search import format_results, get_web_searcher

from ..base import BaseTool

_DEFAULT_TIMEOUT: int = 30


class WebSearchTool(BaseTool):
    """Search the web and return results as Markdown.

    Supports multiple backends: Jina (default, no key needed), Tavily, Brave, Exa, and
    Keenable (no key needed).
    Backend is selected via the WEB_SEARCH_PROVIDER environment variable.
    """

    def __init__(self, *, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def cacheable(self) -> bool:
        return True

    @property
    def category(self) -> str:
        return "web"

    @property
    def description(self) -> str:
        return (
            "Search the web for a query and return relevant results. "
            "Returns titles, URLs, and content snippets. "
            "Useful for finding current information, news, prices, documentation, etc."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
            },
            "required": ["query"],
        }

    async def run(self, **kwargs: Any) -> str:
        query: str = kwargs.get("query", "").strip()
        if not query:
            return "[Error] No query provided."

        searcher = get_web_searcher(timeout=self._timeout)
        try:
            results = await searcher.search(query, num_results=10)
        except httpx.TimeoutException:
            return f"[Timeout] Search exceeded {self._timeout} seconds."
        except httpx.HTTPStatusError as exc:
            return f"[HTTP {exc.response.status_code}] {exc.response.text[:500]}"
        except httpx.RequestError as exc:
            return f"[Error] {exc}"

        return format_results(results)

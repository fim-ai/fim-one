"""Built-in tool for searching the web via Jina Search."""

from __future__ import annotations

import os
from typing import Any

import httpx

from ..base import BaseTool

_DEFAULT_TIMEOUT_SECONDS: int = 30
_JINA_SEARCH_URL = "https://s.jina.ai/"


class WebSearchTool(BaseTool):
    """Search the web and return results as Markdown.

    Uses `Jina Search <https://s.jina.ai>`_ to perform web searches and
    return relevant results.  An optional ``JINA_API_KEY`` environment
    variable enables higher rate limits and better quality.
    """

    def __init__(self, *, timeout: int = _DEFAULT_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Tool protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "web_search"

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

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def run(self, **kwargs: Any) -> str:
        query: str = kwargs.get("query", "").strip()
        if not query:
            return "[Error] No query provided."

        jina_url = f"{_JINA_SEARCH_URL}{query}"
        headers: dict[str, str] = {
            "Accept": "text/markdown",
        }
        api_key = os.environ.get("JINA_API_KEY", "")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(jina_url, headers=headers)
                resp.raise_for_status()
                content = resp.text
        except httpx.TimeoutException:
            return f"[Timeout] Search exceeded {self._timeout} seconds."
        except httpx.HTTPStatusError as exc:
            return f"[HTTP {exc.response.status_code}] {exc.response.text[:500]}"
        except httpx.RequestError as exc:
            return f"[Error] {exc}"

        # Truncate very long results.
        max_chars = 15_000
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n\n[Truncated — {len(content)} chars total]"

        return content

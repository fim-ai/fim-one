"""Built-in tool for fetching and reading web page content via Jina Reader."""

from __future__ import annotations

import os
from typing import Any

import httpx

from ..base import BaseTool

_DEFAULT_TIMEOUT_SECONDS: int = 30
_JINA_READER_URL = "https://r.jina.ai/"


class WebFetchTool(BaseTool):
    """Fetch a URL and return its content as clean Markdown.

    Uses `Jina Reader <https://r.jina.ai>`_ to convert HTML pages into
    readable Markdown text.  An optional ``JINA_API_KEY`` environment
    variable enables higher rate limits.
    """

    def __init__(self, *, timeout: int = _DEFAULT_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Tool protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return (
            "Fetch the content of a web page and return it as Markdown text. "
            "Provide a full URL (e.g. https://example.com). "
            "Useful for reading articles, documentation, API responses, etc."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch (must start with http:// or https://).",
                },
            },
            "required": ["url"],
        }

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def run(self, **kwargs: Any) -> str:
        url: str = kwargs.get("url", "").strip()
        if not url:
            return "[Error] No URL provided."

        jina_url = f"{_JINA_READER_URL}{url}"
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
            return f"[Timeout] Request exceeded {self._timeout} seconds."
        except httpx.HTTPStatusError as exc:
            return f"[HTTP {exc.response.status_code}] {exc.response.text[:500]}"
        except httpx.RequestError as exc:
            return f"[Error] {exc}"

        # Truncate very long pages to stay within LLM context limits.
        max_chars = 20_000
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n\n[Truncated — {len(content)} chars total]"

        return content

"""Built-in tool for fetching web page content.

Delegates to the configured BaseWebFetch backend (Jina or plain httpx).
Backend selection is controlled by the WEB_FETCH_PROVIDER environment variable.
"""

from __future__ import annotations

from typing import Any

import httpx

from fim_agent.core.security import validate_url as _validate_url
from fim_agent.core.web.fetch import get_web_fetcher

from ..base import BaseTool

_DEFAULT_TIMEOUT: int = 30
_MAX_CHARS: int = 20_000


class WebFetchTool(BaseTool):
    """Fetch a URL and return its content as clean Markdown or plain text.

    Supports Jina Reader (clean Markdown output) and plain httpx (text extraction).
    Backend is selected via the WEB_FETCH_PROVIDER environment variable.
    """

    def __init__(self, *, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def category(self) -> str:
        return "web"

    @property
    def description(self) -> str:
        return (
            "Fetch a web page and return its content as clean Markdown text "
            "(HTML is converted, navigation/ads stripped). "
            "Best for reading articles, blog posts, documentation, and Wikipedia. "
            "For REST APIs that return JSON, use http_request instead."
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

    async def run(self, **kwargs: Any) -> str:
        url: str = kwargs.get("url", "").strip()
        if not url:
            return "[Error] No URL provided."

        try:
            _validate_url(url, allow_dns_failure=True)
        except ValueError as exc:
            return f"[Blocked] {exc}"

        fetcher = get_web_fetcher(timeout=self._timeout)
        try:
            content = await fetcher.fetch(url)
        except httpx.TimeoutException:
            return f"[Timeout] Request exceeded {self._timeout} seconds."
        except httpx.HTTPStatusError as exc:
            return f"[HTTP {exc.response.status_code}] {exc.response.text[:500]}"
        except httpx.RequestError as exc:
            return f"[Error] {exc}"

        if len(content) > _MAX_CHARS:
            content = content[:_MAX_CHARS] + f"\n\n[Truncated — {len(content)} chars total]"
        return content

"""Plain httpx web fetch backend with stdlib HTML text extraction."""

from __future__ import annotations

from html.parser import HTMLParser

import httpx

from fim_one.core.security import get_safe_async_client

from .base import BaseWebFetch

_DEFAULT_TIMEOUT = 30
_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; FIM-One/1.0; +https://fim.ai)",
    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
}
_SKIP_TAGS = frozenset({"script", "style", "head", "noscript", "svg", "iframe", "nav", "footer"})


class _TextExtractor(HTMLParser):
    """Extracts visible text from HTML, skipping non-content tags."""

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:  # type: ignore[override]
        if tag in _SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self.parts.append(text)


def _strip_html(raw_html: str) -> str:
    parser = _TextExtractor()
    parser.feed(raw_html)
    return "\n".join(parser.parts)


class HttpxFetch(BaseWebFetch):
    """Fetches pages directly with httpx and extracts plain text from HTML."""

    def __init__(self, *, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    async def fetch(self, url: str) -> str:
        async with get_safe_async_client(
            timeout=self._timeout,
            follow_redirects=True,
            headers=_DEFAULT_HEADERS,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "html" in content_type:
                return _strip_html(resp.text)
            return resp.text

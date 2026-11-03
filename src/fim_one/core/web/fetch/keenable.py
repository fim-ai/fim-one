"""Keenable web fetch backend (https://keenable.ai)."""

from __future__ import annotations

import os

import httpx

from ..search.keenable import keenable_endpoint
from .base import BaseWebFetch

_KEENABLE_FETCH_URL = "https://api.keenable.ai/v1/fetch"
_DEFAULT_TIMEOUT = 30


class KeenableFetch(BaseWebFetch):
    """Fetches pages via Keenable, returning clean Markdown.

    Works without an API key (keyless public endpoint, rate-limited per IP);
    set KEENABLE_API_KEY for higher limits.
    """

    def __init__(self, *, api_key: str = "", timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._api_key = api_key or os.environ.get("KEENABLE_API_KEY", "")
        self._timeout = timeout

    async def fetch(self, url: str) -> str:
        endpoint, headers = keenable_endpoint(_KEENABLE_FETCH_URL, self._api_key)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(endpoint, params={"url": url}, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        title = data.get("title") or ""
        content = data.get("content") or ""
        return f"# {title}\n\n{content}" if title else content

"""Keenable Search backend (https://keenable.ai).

See https://docs.keenable.ai/api-reference/search for the API reference.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from .base import BaseWebSearch, SearchResult

_KEENABLE_SEARCH_URL = "https://api.keenable.ai/v1/search"
_DEFAULT_TIMEOUT = 30

# Required on the keyless public endpoint to identify the calling app.
_APP_TITLE = "fim-one"


def keenable_endpoint(url: str, api_key: str) -> tuple[str, dict[str, str]]:
    """Return ``(url, headers)``: keyed when *api_key* is set, else the public pool."""
    if api_key:
        return url, {"X-API-Key": api_key}
    return f"{url}/public", {"X-Keenable-Title": _APP_TITLE}


def _parse_results(data: dict[str, Any], max_results: int) -> list[SearchResult]:
    results: list[SearchResult] = []
    for item in data.get("results") or []:
        url = item.get("url") or ""
        if not url:
            continue
        snippet = item.get("description") or item.get("snippet") or ""
        results.append(SearchResult(title=item.get("title") or url, url=url, snippet=snippet[:800]))
        if len(results) >= max_results:
            break
    return results


class KeenableSearch(BaseWebSearch):
    """Uses the Keenable Search API.

    Works without an API key: requests go to the keyless public endpoint
    (rate-limited per IP). Set KEENABLE_API_KEY for the keyed endpoint and
    higher limits. Optional env var KEENABLE_SEARCH_SITE restricts results to
    one site (e.g. ``docs.python.org``).
    """

    def __init__(
        self,
        *,
        api_key: str = "",
        timeout: int = _DEFAULT_TIMEOUT,
        site: str | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("KEENABLE_API_KEY", "")
        self._timeout = timeout
        self._site = site or os.environ.get("KEENABLE_SEARCH_SITE") or None

    async def search(self, query: str, *, num_results: int = 10) -> list[SearchResult]:
        url, headers = keenable_endpoint(_KEENABLE_SEARCH_URL, self._api_key)
        payload: dict[str, Any] = {"query": query, "max_results": max(1, min(num_results, 50))}
        if self._site:
            payload["site"] = self._site

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        return _parse_results(data, num_results)

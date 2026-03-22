"""Exa Search backend (https://exa.ai)."""

from __future__ import annotations

import os

import httpx

from .base import BaseWebSearch, SearchResult

_EXA_URL = "https://api.exa.ai/search"
_DEFAULT_TIMEOUT = 30


class ExaSearch(BaseWebSearch):
    """Uses the Exa Search API. Requires EXA_API_KEY."""

    def __init__(self, *, api_key: str = "", timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._api_key = api_key or os.environ.get("EXA_API_KEY", "")
        if not self._api_key:
            raise ValueError("EXA_API_KEY is required for ExaSearch")
        self._timeout = timeout

    async def search(self, query: str, *, num_results: int = 10) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                _EXA_URL,
                headers={"x-api-key": self._api_key},
                json={
                    "query": query,
                    "numResults": num_results,
                    "type": "auto",
                    "contents": {"text": {"maxCharacters": 800}},
                },
            )
            resp.raise_for_status()
            data = resp.json()

        return [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=(item.get("text", "") or "")[:800],
                score=float(item.get("score", 0.0)),
            )
            for item in data.get("results", [])
        ]

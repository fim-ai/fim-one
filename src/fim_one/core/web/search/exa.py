"""Exa Search backend (https://exa.ai).

See https://exa.ai/docs/reference/search for the full API reference.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from .base import BaseWebSearch, SearchResult

_EXA_URL = "https://api.exa.ai/search"
_DEFAULT_TIMEOUT = 30

# Sent on every request so Exa can attribute API usage back to this integration
# in the maintainer's dashboards. Not used for identification or tracking of
# end-users — only to credit fim-one as the integration surface.
_INTEGRATION_NAME = "fim-one"

_VALID_SEARCH_TYPES = frozenset(
    {"auto", "neural", "fast", "deep-lite", "deep", "deep-reasoning", "instant"}
)
_VALID_CATEGORIES = frozenset(
    {"company", "research paper", "news", "personal site", "financial report", "people"}
)


def _env_list(name: str) -> list[str] | None:
    """Parse a comma-separated env var into a stripped list (or None if unset)."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    items = [x.strip() for x in raw.split(",") if x.strip()]
    return items or None


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


class ExaSearch(BaseWebSearch):
    """Uses the Exa Search API. Requires EXA_API_KEY.

    Exposes Exa's current search surface:
      * search types: auto (default), neural, fast, deep-lite, deep,
        deep-reasoning, instant
      * content retrieval: highlights (default), text, summary — the response
        snippet cascades through whichever fields are returned
      * filters: category, include/exclude domains, published-date range,
        maxAgeHours

    Configure via constructor args or env vars:

        EXA_API_KEY              (required)
        EXA_SEARCH_TYPE          default "auto"
        EXA_CATEGORY             e.g. "news", "research paper"
        EXA_INCLUDE_DOMAINS      comma-separated
        EXA_EXCLUDE_DOMAINS      comma-separated
        EXA_MAX_AGE_HOURS        integer
        EXA_INCLUDE_HIGHLIGHTS   "true"/"false", default true
        EXA_TEXT_MAX_CHARS       integer, default 800
        EXA_SUMMARY_QUERY        optional custom summary query
    """

    def __init__(
        self,
        *,
        api_key: str = "",
        timeout: int = _DEFAULT_TIMEOUT,
        search_type: str | None = None,
        category: str | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        start_published_date: str | None = None,
        end_published_date: str | None = None,
        max_age_hours: int | None = None,
        include_highlights: bool | None = None,
        text_max_characters: int | None = None,
        summary_query: str | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("EXA_API_KEY", "")
        if not self._api_key:
            raise ValueError("EXA_API_KEY is required for ExaSearch")
        self._timeout = timeout

        resolved_type = search_type or os.environ.get("EXA_SEARCH_TYPE", "auto")
        if resolved_type not in _VALID_SEARCH_TYPES:
            raise ValueError(
                f"Unknown EXA_SEARCH_TYPE {resolved_type!r}. "
                f"Expected one of: {sorted(_VALID_SEARCH_TYPES)}"
            )
        self._search_type = resolved_type

        resolved_category = category or os.environ.get("EXA_CATEGORY") or None
        if resolved_category is not None and resolved_category not in _VALID_CATEGORIES:
            raise ValueError(
                f"Unknown EXA_CATEGORY {resolved_category!r}. "
                f"Expected one of: {sorted(_VALID_CATEGORIES)}"
            )
        self._category = resolved_category

        self._include_domains = include_domains or _env_list("EXA_INCLUDE_DOMAINS")
        self._exclude_domains = exclude_domains or _env_list("EXA_EXCLUDE_DOMAINS")
        self._start_published_date = start_published_date or os.environ.get(
            "EXA_START_PUBLISHED_DATE"
        ) or None
        self._end_published_date = end_published_date or os.environ.get(
            "EXA_END_PUBLISHED_DATE"
        ) or None
        self._max_age_hours = (
            max_age_hours if max_age_hours is not None else _env_int("EXA_MAX_AGE_HOURS")
        )

        if include_highlights is None:
            raw = os.environ.get("EXA_INCLUDE_HIGHLIGHTS", "true").strip().lower()
            self._include_highlights = raw not in {"false", "0", "no"}
        else:
            self._include_highlights = include_highlights

        self._text_max_characters = (
            text_max_characters
            if text_max_characters is not None
            else (_env_int("EXA_TEXT_MAX_CHARS") or 800)
        )
        self._summary_query = summary_query or os.environ.get("EXA_SUMMARY_QUERY") or None

    def _build_contents(self) -> dict[str, Any]:
        """Assemble the contents object — all three types can be requested together."""
        contents: dict[str, Any] = {
            "text": {"maxCharacters": self._text_max_characters},
        }
        if self._include_highlights:
            contents["highlights"] = True
        if self._summary_query:
            contents["summary"] = {"query": self._summary_query}
        return contents

    def _build_payload(self, query: str, num_results: int) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": query,
            "numResults": num_results,
            "type": self._search_type,
            "contents": self._build_contents(),
        }
        if self._category:
            payload["category"] = self._category
        if self._include_domains:
            payload["includeDomains"] = self._include_domains
        if self._exclude_domains:
            payload["excludeDomains"] = self._exclude_domains
        if self._start_published_date:
            payload["startPublishedDate"] = self._start_published_date
        if self._end_published_date:
            payload["endPublishedDate"] = self._end_published_date
        if self._max_age_hours is not None:
            payload["maxAgeHours"] = self._max_age_hours
        return payload

    async def search(self, query: str, *, num_results: int = 10) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                _EXA_URL,
                headers={
                    "x-api-key": self._api_key,
                    "x-exa-integration": _INTEGRATION_NAME,
                },
                json=self._build_payload(query, num_results),
            )
            resp.raise_for_status()
            data = resp.json()

        return [_parse_result(item, self._text_max_characters) for item in data.get("results", [])]


def _parse_result(item: dict[str, Any], text_max_characters: int) -> SearchResult:
    """Extract a SearchResult from an Exa result item.

    The snippet cascades through highlights → text → summary so a useful body
    is returned regardless of which content types the API populated.
    """
    return SearchResult(
        title=item.get("title") or "",
        url=item.get("url") or "",
        snippet=_extract_snippet(item, text_max_characters),
        score=float(item.get("score") or 0.0),
    )


def _extract_snippet(item: dict[str, Any], text_max_characters: int) -> str:
    highlights = item.get("highlights")
    if isinstance(highlights, list) and highlights:
        joined = " … ".join(h for h in highlights if isinstance(h, str) and h)
        if joined:
            return joined[:text_max_characters]

    text = item.get("text")
    if isinstance(text, str) and text:
        return text[:text_max_characters]

    summary = item.get("summary")
    if isinstance(summary, str) and summary:
        return summary[:text_max_characters]

    return ""

"""URL → Markdown importer using Jina Reader API."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx

from fim_agent.core.security import get_safe_async_client, validate_url

logger = logging.getLogger(__name__)

_JINA_READER_BASE = "https://r.jina.ai/"
_MAX_CONCURRENT = 3

_FILE_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".xls", ".pptx", ".csv", ".txt", ".md", ".markdown"}


async def fetch_url_as_markdown(url: str, jina_api_key: str) -> dict:
    """Fetch a URL via Jina Reader and return title + markdown content.

    Returns a dict with keys: ``url``, ``title``, ``content``.
    Raises ``httpx.HTTPStatusError`` on non-2xx responses.
    """
    headers = {
        "Authorization": f"Bearer {jina_api_key}",
        "Accept": "application/json",
        "X-Return-Format": "markdown",
    }
    async with get_safe_async_client(timeout=30) as client:
        resp = await client.get(f"{_JINA_READER_BASE}{url}", headers=headers)
        resp.raise_for_status()
        data = resp.json()
        payload = data.get("data", {})
        return {
            "url": url,
            "title": payload.get("title") or url,
            "content": payload.get("content", ""),
        }


async def resolve_url(url: str, jina_api_key: str) -> dict:
    """Unified URL resolution entry point.

    Detects whether the URL points to a direct file download or a web page:

    - If the URL path ends with a known file extension (see ``_FILE_EXTENSIONS``),
      the file bytes are downloaded directly and the result includes
      ``{"mode": "file", "url": ..., "filename": ..., "ext": ..., "file_bytes": ...}``.
    - Otherwise Jina Reader is used to convert the page to Markdown and the
      result mirrors ``fetch_url_as_markdown`` with an added ``"mode": "markdown"``
      key: ``{"mode": "markdown", "url": ..., "title": ..., "content": ...}``.
    """
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()

    if suffix in _FILE_EXTENSIONS:
        validate_url(url)
        filename = Path(parsed.path).name or "download"
        async with get_safe_async_client(timeout=60) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        return {
            "mode": "file",
            "url": url,
            "filename": filename,
            "ext": suffix,
            "file_bytes": resp.content,
        }
    else:
        fetched = await fetch_url_as_markdown(url, jina_api_key)
        return {
            "mode": "markdown",
            "url": fetched["url"],
            "title": fetched["title"],
            "content": fetched["content"],
        }


def get_jina_api_key() -> str:
    key = os.environ.get("JINA_API_KEY", "")
    if not key:
        raise ValueError("JINA_API_KEY is not set")
    return key

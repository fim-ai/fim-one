"""Web fetch backends and provider factory."""

from __future__ import annotations

import os

from .base import BaseWebFetch
from .httpx_fetch import HttpxFetch
from .jina import JinaFetch
from .keenable import KeenableFetch

__all__ = ["BaseWebFetch", "JinaFetch", "KeenableFetch", "HttpxFetch", "get_web_fetcher"]


def get_web_fetcher(*, timeout: int = 30) -> BaseWebFetch:
    """Return the configured web fetch backend.

    Selection order:
    1. ``WEB_FETCH_PROVIDER`` env var (jina / keenable / httpx)
    2. Auto-detect: use Jina if JINA_API_KEY is set
    3. Default: HttpxFetch (no API key needed)
    """
    provider = os.environ.get("WEB_FETCH_PROVIDER", "").lower()

    if provider == "jina" or (not provider and os.environ.get("JINA_API_KEY")):
        return JinaFetch(timeout=timeout)
    if provider == "keenable":
        return KeenableFetch(timeout=timeout)
    return HttpxFetch(timeout=timeout)

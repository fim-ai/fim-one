"""Per-execution tool result cache for DAG steps.

Caches identical tool calls within one DAG execution to avoid
redundant API calls when multiple steps query the same data.

Whitelist approach: only tools that explicitly set ``cacheable = True``
are eligible for caching.  All other tools are treated as uncacheable
by default.  This is safer for an open tool ecosystem where third-party
or side-effectful tools should never be cached unless they opt in.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)


def _is_cacheable(tool: Any) -> bool:
    """Check whether a tool's results can safely be cached.

    Uses a whitelist approach: the tool must explicitly declare
    ``cacheable = True`` to be eligible for caching.
    """
    return getattr(tool, "cacheable", False) is True


def _cache_key(tool_name: str, kwargs: dict[str, Any]) -> str:
    """Deterministic cache key from tool name + arguments."""
    return f"{tool_name}::{json.dumps(kwargs, sort_keys=True, default=str)}"


class ToolCache:
    """In-memory cache scoped to a single DAG execution."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        self.hits = 0
        self.misses = 0

    async def _get_lock(self, key: str) -> asyncio.Lock:
        async with self._global_lock:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

    async def get_or_run(self, key: str, coro_factory: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
        """Return cached result or run the coroutine and cache it."""
        lock = await self._get_lock(key)
        async with lock:
            if key in self._store:
                self.hits += 1
                logger.debug("ToolCache HIT: %s", key[:80])
                return self._store[key]
            self.misses += 1
            result = await coro_factory()
            self._store[key] = result
            return result


class _CachedTool:
    """Wrapper that intercepts run() to use ToolCache."""

    def __init__(self, tool: Any, cache: ToolCache) -> None:
        self._tool = tool
        self._cache = cache

    def __getattr__(self, name: str) -> Any:
        return getattr(self._tool, name)

    async def run(self, **kwargs: Any) -> Any:
        key = _cache_key(self._tool.name, kwargs)
        return await self._cache.get_or_run(
            key, lambda: self._tool.run(**kwargs)
        )


def wrap_tools_with_cache(tools: list[Any], cache: ToolCache) -> list[Any]:
    """Wrap cacheable tools, leave uncacheable ones as-is."""
    result = []
    for tool in tools:
        if _is_cacheable(tool):
            result.append(_CachedTool(tool, cache))
        else:
            result.append(tool)
    return result

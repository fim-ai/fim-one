"""Tests for the DAG ToolCache — per-execution tool result caching."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from fim_one.core.planner.tool_cache import (
    ToolCache,
    _CachedTool,
    _cache_key,
    _is_cacheable,
    wrap_tools_with_cache,
)


# ======================================================================
# Helper: mock tool objects
# ======================================================================


class _MockTool:
    """Minimal mock tool with name, category, and async run()."""

    def __init__(
        self,
        name: str = "web_search",
        category: str = "search",
        display_name: str = "Web Search",
        *,
        cacheable: bool = False,
    ) -> None:
        self.name = name
        self.category = category
        self.display_name = display_name
        self.cacheable = cacheable
        self.call_count = 0

    async def run(self, **kwargs: Any) -> str:
        self.call_count += 1
        return f"result for {kwargs}"

    @property
    def description(self) -> str:
        return f"Mock tool: {self.name}"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}


# ======================================================================
# _is_cacheable
# ======================================================================


class TestIsCacheable:
    """Verify cacheability checks for different tool types."""

    def test_cacheable_tool(self) -> None:
        tool = _MockTool(name="web_search", category="search", cacheable=True)
        assert _is_cacheable(tool) is True

    def test_uncacheable_by_name(self) -> None:
        tool = _MockTool(name="python_exec", category="search")
        assert _is_cacheable(tool) is False

    def test_uncacheable_shell_exec(self) -> None:
        tool = _MockTool(name="shell_exec", category="general")
        assert _is_cacheable(tool) is False

    def test_uncacheable_node_exec(self) -> None:
        tool = _MockTool(name="node_exec", category="general")
        assert _is_cacheable(tool) is False

    def test_uncacheable_email_send(self) -> None:
        tool = _MockTool(name="email_send", category="communication")
        assert _is_cacheable(tool) is False

    def test_uncacheable_message_send(self) -> None:
        tool = _MockTool(name="message_send", category="communication")
        assert _is_cacheable(tool) is False

    def test_uncacheable_by_prefix(self) -> None:
        tool = _MockTool(name="file_read", category="general")
        assert _is_cacheable(tool) is False

    def test_uncacheable_file_write(self) -> None:
        tool = _MockTool(name="file_write", category="general")
        assert _is_cacheable(tool) is False

    def test_uncacheable_by_category_code_execution(self) -> None:
        tool = _MockTool(name="custom_runner", category="code_execution")
        assert _is_cacheable(tool) is False

    def test_uncacheable_by_category_file_ops(self) -> None:
        tool = _MockTool(name="my_file_tool", category="file_ops")
        assert _is_cacheable(tool) is False

    def test_tool_without_name_attr(self) -> None:
        """Object without name attribute is cacheable if it opts in."""

        class Bare:
            cacheable = True

        assert _is_cacheable(Bare()) is True

    def test_tool_without_cacheable_attr(self) -> None:
        """Object without cacheable attribute should not be cacheable."""

        class Bare:
            pass

        assert _is_cacheable(Bare()) is False

    def test_tool_without_category_attr(self) -> None:
        """Object with name but no category is cacheable if it opts in."""

        class NameOnly:
            name = "web_search"
            cacheable = True

        assert _is_cacheable(NameOnly()) is True


# ======================================================================
# _cache_key
# ======================================================================


class TestCacheKey:
    """Verify deterministic key generation."""

    def test_basic_key(self) -> None:
        key = _cache_key("web_search", {"query": "hello"})
        assert key.startswith("web_search::")
        assert '"query": "hello"' in key

    def test_key_is_deterministic(self) -> None:
        k1 = _cache_key("tool", {"a": 1, "b": 2})
        k2 = _cache_key("tool", {"b": 2, "a": 1})
        assert k1 == k2  # sort_keys=True ensures order independence

    def test_different_tools_different_keys(self) -> None:
        k1 = _cache_key("tool_a", {"x": 1})
        k2 = _cache_key("tool_b", {"x": 1})
        assert k1 != k2

    def test_different_args_different_keys(self) -> None:
        k1 = _cache_key("tool", {"x": 1})
        k2 = _cache_key("tool", {"x": 2})
        assert k1 != k2

    def test_empty_kwargs(self) -> None:
        key = _cache_key("tool", {})
        assert key == "tool::{}"


# ======================================================================
# ToolCache.get_or_run
# ======================================================================


class TestToolCache:
    """Verify cache hit/miss behavior."""

    async def test_cache_miss_runs_coroutine(self) -> None:
        cache = ToolCache()

        async def factory():
            return "value1"

        result = await cache.get_or_run("key1", factory)
        assert result == "value1"
        assert cache.misses == 1
        assert cache.hits == 0

    async def test_cache_miss_then_hit(self) -> None:
        cache = ToolCache()

        async def factory():
            return "value"

        r1 = await cache.get_or_run("key1", factory)
        r2 = await cache.get_or_run("key1", factory)
        assert r1 == "value"
        assert r2 == "value"
        assert cache.misses == 1
        assert cache.hits == 1

    async def test_different_keys_are_separate(self) -> None:
        cache = ToolCache()
        call_count = 0

        async def factory_a():
            nonlocal call_count
            call_count += 1
            return "a"

        async def factory_b():
            nonlocal call_count
            call_count += 1
            return "b"

        r1 = await cache.get_or_run("key_a", factory_a)
        r2 = await cache.get_or_run("key_b", factory_b)
        assert r1 == "a"
        assert r2 == "b"
        assert call_count == 2
        assert cache.misses == 2
        assert cache.hits == 0


# ======================================================================
# _CachedTool
# ======================================================================


class TestCachedTool:
    """Verify _CachedTool wraps correctly."""

    async def test_run_uses_cache(self) -> None:
        tool = _MockTool(name="web_search", cacheable=True)
        cache = ToolCache()
        cached = _CachedTool(tool, cache)

        r1 = await cached.run(query="hello")
        r2 = await cached.run(query="hello")
        assert r1 == r2
        assert tool.call_count == 1  # Only called once
        assert cache.hits == 1
        assert cache.misses == 1

    async def test_run_different_args_calls_twice(self) -> None:
        tool = _MockTool(name="web_search", cacheable=True)
        cache = ToolCache()
        cached = _CachedTool(tool, cache)

        r1 = await cached.run(query="hello")
        r2 = await cached.run(query="world")
        assert r1 != r2
        assert tool.call_count == 2
        assert cache.hits == 0
        assert cache.misses == 2

    def test_attribute_delegation(self) -> None:
        tool = _MockTool(name="web_search", category="search", display_name="Web Search", cacheable=True)
        cache = ToolCache()
        cached = _CachedTool(tool, cache)

        assert cached.name == "web_search"
        assert cached.category == "search"
        assert cached.display_name == "Web Search"
        assert cached.description == "Mock tool: web_search"


# ======================================================================
# wrap_tools_with_cache
# ======================================================================


class TestWrapToolsWithCache:
    """Verify wrap_tools_with_cache wraps cacheable and skips uncacheable."""

    def test_cacheable_tool_is_wrapped(self) -> None:
        tool = _MockTool(name="web_search", category="search", cacheable=True)
        cache = ToolCache()
        wrapped = wrap_tools_with_cache([tool], cache)
        assert len(wrapped) == 1
        assert isinstance(wrapped[0], _CachedTool)

    def test_uncacheable_tool_is_not_wrapped(self) -> None:
        tool = _MockTool(name="python_exec", category="code_execution")
        cache = ToolCache()
        wrapped = wrap_tools_with_cache([tool], cache)
        assert len(wrapped) == 1
        assert wrapped[0] is tool  # Not wrapped

    def test_mixed_tools(self) -> None:
        search = _MockTool(name="web_search", category="search", cacheable=True)
        exec_tool = _MockTool(name="python_exec", category="code_execution")
        file_tool = _MockTool(name="file_read", category="file_ops")
        cache = ToolCache()
        wrapped = wrap_tools_with_cache([search, exec_tool, file_tool], cache)
        assert len(wrapped) == 3
        assert isinstance(wrapped[0], _CachedTool)  # web_search: cached
        assert wrapped[1] is exec_tool  # python_exec: not cached
        assert wrapped[2] is file_tool  # file_read: not cached (prefix)

    def test_empty_list(self) -> None:
        cache = ToolCache()
        wrapped = wrap_tools_with_cache([], cache)
        assert wrapped == []


# ======================================================================
# Concurrent access — thundering herd prevention
# ======================================================================


class TestToolCacheConcurrency:
    """Verify per-key locking prevents duplicate execution."""

    async def test_concurrent_same_key_runs_once(self) -> None:
        """Multiple coroutines requesting the same key only run the factory once."""
        cache = ToolCache()
        call_count = 0

        async def slow_factory():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)  # Simulate work
            return "result"

        # Launch 5 concurrent requests for the same key.
        tasks = [
            asyncio.create_task(cache.get_or_run("shared_key", slow_factory))
            for _ in range(5)
        ]
        results = await asyncio.gather(*tasks)

        # All should get the same result.
        assert all(r == "result" for r in results)
        # Factory should only have been called once.
        assert call_count == 1
        assert cache.misses == 1
        assert cache.hits == 4

    async def test_concurrent_different_keys_run_in_parallel(self) -> None:
        """Different keys should not block each other."""
        cache = ToolCache()
        call_count = 0

        async def factory(val: str):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.01)
            return val

        tasks = [
            asyncio.create_task(cache.get_or_run(f"key_{i}", lambda i=i: factory(f"val_{i}")))
            for i in range(3)
        ]
        results = await asyncio.gather(*tasks)

        assert len(results) == 3
        assert call_count == 3
        assert cache.misses == 3
        assert cache.hits == 0

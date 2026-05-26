"""Tests for model group cache invalidation.

Covers the two layers that together make admin LLM config edits take
effect without a process restart:

1. ``_invalidate_model_group_cache`` in ``fim_one.web.api.admin`` —
   bumps the version counter AND flushes LiteLLM's internal
   ``AsyncOpenAI`` client cache (so cached clients bound to the old
   ``api_key``/``base_url`` don't keep serving requests for up to 600 s).

2. ``get_model_registry_with_group`` in ``fim_one.web.deps`` — rebuilds
   the registry when the version changes OR when the cache exceeds
   ``_REGISTRY_CACHE_TTL_SECONDS`` (safety net for missed invalidations).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_invalidate_bumps_version() -> None:
    """Each invalidate call increments the version counter monotonically."""
    from fim_one.web.api import admin

    start = admin.get_model_group_version()
    admin._invalidate_model_group_cache()
    admin._invalidate_model_group_cache()
    assert admin.get_model_group_version() == start + 2


def test_invalidate_flushes_litellm_client_cache() -> None:
    """Stale AsyncOpenAI clients in LiteLLM's cache must be dropped."""
    from fim_one.web.api import admin

    fake_cache = MagicMock()
    with patch("litellm.in_memory_llm_clients_cache", fake_cache):
        admin._invalidate_model_group_cache()
    fake_cache.flush_cache.assert_called_once()


def test_invalidate_tolerates_missing_litellm_cache() -> None:
    """If LiteLLM exposes no flushable cache, invalidate must not raise."""
    from fim_one.web.api import admin

    # Object that has neither flush_cache nor is None — exercises the
    # hasattr guard.
    with patch("litellm.in_memory_llm_clients_cache", object()):
        admin._invalidate_model_group_cache()  # must not raise


def test_invalidate_swallows_flush_errors() -> None:
    """A broken flush must not block the admin write that triggered it."""
    from fim_one.web.api import admin

    broken = MagicMock()
    broken.flush_cache.side_effect = RuntimeError("boom")
    with patch("litellm.in_memory_llm_clients_cache", broken):
        admin._invalidate_model_group_cache()  # must not raise


@pytest.mark.asyncio
async def test_registry_cache_rebuilds_when_version_changes() -> None:
    """Bumping the version forces a rebuild on the next access."""
    from fim_one.web import deps

    deps._cached_registry = None
    deps._cached_registry_version = -1
    deps._cached_registry_built_at = 0.0

    fake_registry_v1 = MagicMock(name="registry_v1")
    fake_registry_v2 = MagicMock(name="registry_v2")

    with (
        patch(
            "fim_one.web.deps._get_active_group_config",
            new=AsyncMock(side_effect=[{"general": MagicMock()}, {"general": MagicMock()}]),
        ),
        patch(
            "fim_one.web.deps._build_registry_from_group",
            side_effect=[fake_registry_v1, fake_registry_v2],
        ),
        patch("fim_one.web.api.admin.get_model_group_version", side_effect=[10, 10, 11]),
    ):
        db = MagicMock()
        r1 = await deps.get_model_registry_with_group(db)  # builds at v=10
        r2 = await deps.get_model_registry_with_group(db)  # cache hit
        r3 = await deps.get_model_registry_with_group(db)  # rebuild at v=11

    assert r1 is fake_registry_v1
    assert r2 is fake_registry_v1
    assert r3 is fake_registry_v2


@pytest.mark.asyncio
async def test_registry_cache_rebuilds_after_ttl_even_without_version_bump() -> None:
    """Safety-net TTL: stale cache self-heals even if no admin call fired.

    This guards against missed invalidations (direct DB edits, future
    code paths that forget to call ``_invalidate_model_group_cache``,
    or any other state-corruption scenario).
    """
    from fim_one.web import deps

    deps._cached_registry = None
    deps._cached_registry_version = -1
    deps._cached_registry_built_at = 0.0

    fake_v1 = MagicMock(name="v1")
    fake_v2 = MagicMock(name="v2")

    # Each call: one monotonic() at the cache_age check; if rebuild, one
    # more when stamping _cached_registry_built_at. Sequence:
    #   call 1 (rebuild):   check@100, stamp@100
    #   call 2 (cache hit): check@101
    #   call 3 (TTL stale): check@(100+ttl+1), stamp@(100+ttl+1)
    ttl = deps._REGISTRY_CACHE_TTL_SECONDS
    times = [100.0, 100.0, 101.0, 100.0 + ttl + 1.0, 100.0 + ttl + 1.0]

    with (
        patch(
            "fim_one.web.deps._get_active_group_config",
            new=AsyncMock(side_effect=[{"general": MagicMock()}, {"general": MagicMock()}]),
        ),
        patch(
            "fim_one.web.deps._build_registry_from_group",
            side_effect=[fake_v1, fake_v2],
        ),
        patch("fim_one.web.api.admin.get_model_group_version", return_value=42),
        patch("fim_one.web.deps.time.monotonic", side_effect=times),
    ):
        db = MagicMock()
        r1 = await deps.get_model_registry_with_group(db)  # build
        r2 = await deps.get_model_registry_with_group(db)  # fresh — cache hit
        r3 = await deps.get_model_registry_with_group(db)  # stale — rebuild

    assert r1 is fake_v1
    assert r2 is fake_v1
    assert r3 is fake_v2

"""Tests for WorkflowRateLimiter — per-user sliding window rate limiting."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from fim_one.core.workflow.rate_limiter import WorkflowRateLimiter


@pytest.fixture
def limiter() -> WorkflowRateLimiter:
    """Rate limiter with low limits for testing."""
    return WorkflowRateLimiter(max_runs_per_minute=3, max_concurrent_runs=2)


@pytest.mark.asyncio
async def test_allows_runs_under_limit(limiter: WorkflowRateLimiter) -> None:
    """Runs within the per-minute limit should be allowed."""
    for i in range(3):
        allowed, err = await limiter.check_rate_limit("user-1")
        assert allowed is True, f"Run {i} should be allowed"
        assert err is None
        await limiter.record_run_start("user-1", f"run-{i}")
        await limiter.record_run_end("user-1", f"run-{i}")


@pytest.mark.asyncio
async def test_blocks_when_per_minute_exceeded(limiter: WorkflowRateLimiter) -> None:
    """The 4th run within one minute should be rejected."""
    for i in range(3):
        allowed, _ = await limiter.check_rate_limit("user-1")
        assert allowed is True
        await limiter.record_run_start("user-1", f"run-{i}")
        # Runs complete immediately — concurrent slots free
        await limiter.record_run_end("user-1", f"run-{i}")

    # 4th run should hit the per-minute limit
    allowed, err = await limiter.check_rate_limit("user-1")
    assert allowed is False
    assert err is not None
    assert "per minute" in err.lower()


@pytest.mark.asyncio
async def test_blocks_when_concurrent_exceeded(limiter: WorkflowRateLimiter) -> None:
    """Exceeding concurrent run slots should be rejected."""
    # Start 2 runs without ending them
    for i in range(2):
        allowed, _ = await limiter.check_rate_limit("user-1")
        assert allowed is True
        await limiter.record_run_start("user-1", f"run-{i}")

    # 3rd concurrent run should be blocked
    allowed, err = await limiter.check_rate_limit("user-1")
    assert allowed is False
    assert err is not None
    assert "concurrent" in err.lower()


@pytest.mark.asyncio
async def test_per_user_isolation(limiter: WorkflowRateLimiter) -> None:
    """User A's runs should not affect user B's limits."""
    # Fill user-A's concurrent slots
    for i in range(2):
        await limiter.record_run_start("user-a", f"run-a-{i}")

    # User A is blocked
    allowed_a, _ = await limiter.check_rate_limit("user-a")
    assert allowed_a is False

    # User B should still be allowed
    allowed_b, err_b = await limiter.check_rate_limit("user-b")
    assert allowed_b is True
    assert err_b is None


@pytest.mark.asyncio
async def test_run_end_frees_concurrent_slot(limiter: WorkflowRateLimiter) -> None:
    """Completing a run should free up a concurrent slot."""
    # Fill concurrent slots
    await limiter.record_run_start("user-1", "run-0")
    await limiter.record_run_start("user-1", "run-1")

    allowed, _ = await limiter.check_rate_limit("user-1")
    assert allowed is False

    # Complete one run
    await limiter.record_run_end("user-1", "run-0")

    # Now should be allowed again
    allowed, err = await limiter.check_rate_limit("user-1")
    assert allowed is True
    assert err is None


@pytest.mark.asyncio
async def test_sliding_window_expiry(limiter: WorkflowRateLimiter) -> None:
    """Timestamps older than 60 seconds should be evicted."""
    # Inject 3 old timestamps (> 60s ago) by patching time
    old_time = time.time() - 120.0
    with patch("fim_one.core.workflow.rate_limiter.time") as mock_time:
        mock_time.time.return_value = old_time
        for i in range(3):
            await limiter.record_run_start("user-1", f"old-run-{i}")
            await limiter.record_run_end("user-1", f"old-run-{i}")

    # Now at real time, the old timestamps should have expired
    allowed, err = await limiter.check_rate_limit("user-1")
    assert allowed is True
    assert err is None


@pytest.mark.asyncio
async def test_record_run_end_idempotent(limiter: WorkflowRateLimiter) -> None:
    """Calling record_run_end for a non-existent run should not error."""
    # Should not raise
    await limiter.record_run_end("user-1", "nonexistent-run")


@pytest.mark.asyncio
async def test_default_limits() -> None:
    """Default constructor should have sensible limits."""
    limiter = WorkflowRateLimiter()
    assert limiter.max_runs_per_minute == 10
    assert limiter.max_concurrent_runs == 3


# ---------------------------------------------------------------------------
# Batch items: same pool of concurrent slots, waiting instead of refused
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_items_wait_and_leave_one_slot_free() -> None:
    """Batch items fill at most ``max_concurrent_runs - 1`` slots; the rest wait."""
    limiter = WorkflowRateLimiter(max_runs_per_minute=100, max_concurrent_runs=3)
    assert limiter.max_batch_runs == 2

    await limiter.acquire_when_free("u", "b1")
    await limiter.acquire_when_free("u", "b2")
    third = asyncio.create_task(limiter.acquire_when_free("u", "b3"))
    await asyncio.sleep(0.01)
    assert not third.done()

    # The slot the batch left is there for a run started by hand...
    assert await limiter.acquire("u", "interactive") == (True, None)
    # ...and with it the user is at the limit, batch items included.
    allowed, err = await limiter.acquire("u", "interactive-2")
    assert allowed is False
    assert err is not None and "concurrent" in err.lower()

    # A batch item ending does not let the waiting one in while the pool
    # still holds max_batch_runs runs (b2 + interactive).
    await limiter.record_run_end("u", "b1")
    await asyncio.sleep(0.01)
    assert not third.done()

    await limiter.record_run_end("u", "interactive")
    await asyncio.wait_for(third, timeout=1)
    assert limiter._active_runs["u"] == {"b2", "b3"}


@pytest.mark.asyncio
async def test_batch_items_do_not_count_against_runs_per_minute() -> None:
    """A batch counts once against runs-per-minute, however many items it has."""
    limiter = WorkflowRateLimiter(max_runs_per_minute=2, max_concurrent_runs=3)

    assert await limiter.admit_batch("u") == (True, None)
    for i in range(5):
        await limiter.acquire_when_free("u", f"b{i}")
        await limiter.record_run_end("u", f"b{i}")

    # One start used by the batch, one left, then the window is full.
    assert await limiter.acquire("u", "r1") == (True, None)
    allowed, err = await limiter.admit_batch("u")
    assert allowed is False
    assert err is not None and "per minute" in err.lower()


@pytest.mark.asyncio
async def test_cancelled_batch_item_stops_waiting() -> None:
    limiter = WorkflowRateLimiter(max_runs_per_minute=100, max_concurrent_runs=2)
    await limiter.acquire_when_free("u", "b1")
    waiting = asyncio.create_task(limiter.acquire_when_free("u", "b2"))
    await asyncio.sleep(0.01)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert limiter._slot_waiters["u"] == []
    await limiter.record_run_end("u", "b1")
    assert limiter._active_runs["u"] == set()


@pytest.mark.asyncio
async def test_single_slot_limit_still_runs_batches() -> None:
    limiter = WorkflowRateLimiter(max_runs_per_minute=100, max_concurrent_runs=1)
    assert limiter.max_batch_runs == 1
    await asyncio.wait_for(limiter.acquire_when_free("u", "b1"), timeout=1)

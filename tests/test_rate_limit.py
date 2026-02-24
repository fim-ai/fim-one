"""Tests for the token-bucket rate limiter."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from fim_agent.core.model.rate_limit import RateLimitConfig, TokenBucketRateLimiter


# ======================================================================
# RateLimitConfig
# ======================================================================


class TestRateLimitConfig:
    """Verify RateLimitConfig defaults and customisation."""

    def test_defaults(self) -> None:
        cfg = RateLimitConfig()
        assert cfg.requests_per_minute == 60
        assert cfg.tokens_per_minute == 100_000

    def test_custom_values(self) -> None:
        cfg = RateLimitConfig(requests_per_minute=10, tokens_per_minute=5000)
        assert cfg.requests_per_minute == 10
        assert cfg.tokens_per_minute == 5000

    def test_frozen(self) -> None:
        cfg = RateLimitConfig()
        with pytest.raises(AttributeError):
            cfg.requests_per_minute = 999  # type: ignore[misc]


# ======================================================================
# TokenBucketRateLimiter — basic behaviour
# ======================================================================


class TestTokenBucketRateLimiter:
    """Verify token bucket logic."""

    async def test_acquire_when_bucket_is_full(self) -> None:
        """First acquire should succeed immediately."""
        limiter = TokenBucketRateLimiter(RateLimitConfig(requests_per_minute=60))
        # Should not raise or block significantly.
        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    async def test_multiple_fast_acquires(self) -> None:
        """Multiple acquires within the budget should all pass quickly."""
        limiter = TokenBucketRateLimiter(
            RateLimitConfig(requests_per_minute=100, tokens_per_minute=100_000)
        )
        for _ in range(10):
            await limiter.acquire()

    async def test_acquire_with_token_estimate(self) -> None:
        """Passing a token estimate should consume from the token bucket."""
        limiter = TokenBucketRateLimiter(
            RateLimitConfig(requests_per_minute=1000, tokens_per_minute=1000)
        )
        # Consume all tokens at once.
        await limiter.acquire(estimated_tokens=1000)

        # The next acquire asking for tokens should need to wait.
        # We patch asyncio.sleep to avoid actual waiting.
        sleep_called = False
        original_sleep = asyncio.sleep

        async def mock_sleep(delay: float) -> None:
            nonlocal sleep_called
            sleep_called = True
            # Actually sleep a tiny bit so the refill logic works.
            await original_sleep(0.01)

        with patch("fim_agent.core.model.rate_limit.asyncio.sleep", side_effect=mock_sleep):
            await limiter.acquire(estimated_tokens=100)
        assert sleep_called

    async def test_request_bucket_exhaustion_triggers_wait(self) -> None:
        """When request tokens run out, acquire should wait."""
        # Only allow 2 requests per minute.
        limiter = TokenBucketRateLimiter(
            RateLimitConfig(requests_per_minute=2, tokens_per_minute=100_000)
        )
        await limiter.acquire()
        await limiter.acquire()

        # Third acquire should need to wait.
        sleep_called = False
        original_sleep = asyncio.sleep

        async def mock_sleep(delay: float) -> None:
            nonlocal sleep_called
            sleep_called = True
            await original_sleep(0.01)

        with patch("fim_agent.core.model.rate_limit.asyncio.sleep", side_effect=mock_sleep):
            await limiter.acquire()
        assert sleep_called

    async def test_default_config_used_when_none(self) -> None:
        """Passing None should use default RateLimitConfig."""
        limiter = TokenBucketRateLimiter(None)
        assert limiter._config.requests_per_minute == 60
        assert limiter._config.tokens_per_minute == 100_000


# ======================================================================
# report_usage
# ======================================================================


class TestReportUsage:
    """Verify post-hoc usage adjustment."""

    async def test_report_higher_usage_reduces_bucket(self) -> None:
        limiter = TokenBucketRateLimiter(
            RateLimitConfig(requests_per_minute=1000, tokens_per_minute=500)
        )
        # Acquire with estimate of 0.
        await limiter.acquire(estimated_tokens=0)

        # Report actual usage of 400 tokens.
        await limiter.report_usage(actual_tokens=400, estimated_tokens=0)

        # Bucket should now have roughly 500 - 400 = 100 tokens left.
        # Requesting 200 more should trigger a wait.
        sleep_called = False
        original_sleep = asyncio.sleep

        async def mock_sleep(delay: float) -> None:
            nonlocal sleep_called
            sleep_called = True
            await original_sleep(0.01)

        with patch("fim_agent.core.model.rate_limit.asyncio.sleep", side_effect=mock_sleep):
            await limiter.acquire(estimated_tokens=200)
        assert sleep_called

    async def test_report_lower_usage_refunds_tokens(self) -> None:
        limiter = TokenBucketRateLimiter(
            RateLimitConfig(requests_per_minute=1000, tokens_per_minute=100)
        )
        # Consume most tokens.
        await limiter.acquire(estimated_tokens=90)

        # Actual usage was less: refund 40 tokens.
        await limiter.report_usage(actual_tokens=50, estimated_tokens=90)

        # Bucket should have ~100 - 90 + 40 = 50 tokens. Requesting 40 should pass.
        start = time.monotonic()
        await limiter.acquire(estimated_tokens=40)
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    async def test_report_usage_zero_diff_is_noop(self) -> None:
        limiter = TokenBucketRateLimiter(
            RateLimitConfig(requests_per_minute=1000, tokens_per_minute=1000)
        )
        await limiter.acquire(estimated_tokens=100)
        # Same actual as estimated — should be a no-op.
        before = limiter._token_tokens
        await limiter.report_usage(actual_tokens=100, estimated_tokens=100)
        # Bucket value might differ slightly due to refill but the diff call
        # should not have changed it beyond refill.
        after = limiter._token_tokens
        # The difference should be tiny (only natural refill).
        assert abs(after - before) < 1.0


# ======================================================================
# Concurrency safety
# ======================================================================


class TestConcurrency:
    """Verify that the rate limiter is safe under concurrent access."""

    async def test_concurrent_acquires_respect_budget(self) -> None:
        """Launch concurrent acquires and verify all complete with rate limiting."""
        # Use a generous budget so all 10 fit in the initial bucket.
        limiter = TokenBucketRateLimiter(
            RateLimitConfig(requests_per_minute=100, tokens_per_minute=100_000)
        )
        completed = 0

        async def _acquire() -> None:
            nonlocal completed
            await limiter.acquire()
            completed += 1

        tasks = [asyncio.create_task(_acquire()) for _ in range(10)]
        await asyncio.wait(tasks, timeout=5.0)
        assert completed == 10

    async def test_concurrent_acquires_block_when_exhausted(self) -> None:
        """Tasks beyond the bucket capacity must wait for refill."""
        # 60 RPM = 1 req/sec refill rate.  Exhaust the bucket to 0, then
        # the third acquire needs ~1s of real time to refill 1 token.
        limiter = TokenBucketRateLimiter(
            RateLimitConfig(requests_per_minute=60, tokens_per_minute=100_000)
        )
        # Manually drain the bucket to leave exactly 2 request tokens.
        async with limiter._lock:
            limiter._request_tokens = 2.0

        acquired_times: list[float] = []

        async def _acquire() -> None:
            await limiter.acquire()
            acquired_times.append(time.monotonic())

        # Launch 3 concurrent tasks.  First 2 pass immediately; the
        # third must wait for ~1s of refill.
        tasks = [asyncio.create_task(_acquire()) for _ in range(3)]
        await asyncio.wait(tasks, timeout=5.0)

        assert len(acquired_times) == 3
        # The third acquire should have been delayed.
        gap = acquired_times[2] - acquired_times[0]
        assert gap > 0.5  # At least some measurable delay


# ======================================================================
# Bucket refill
# ======================================================================


class TestBucketRefill:
    """Verify that token buckets refill over time."""

    async def test_tokens_refill_after_time(self) -> None:
        limiter = TokenBucketRateLimiter(
            RateLimitConfig(requests_per_minute=60, tokens_per_minute=6000)
        )
        # Drain all request tokens.
        for _ in range(60):
            await limiter.acquire()

        # Wait a bit for refill (100ms -> ~0.1 request token at 1/s rate).
        await asyncio.sleep(0.15)

        # Should be able to acquire without hitting a long wait.
        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        # Might wait a tiny bit if not fully refilled, but should be well under 1s.
        assert elapsed < 1.0

    async def test_bucket_does_not_exceed_capacity(self) -> None:
        """Tokens should not accumulate beyond the configured maximum."""
        limiter = TokenBucketRateLimiter(
            RateLimitConfig(requests_per_minute=60, tokens_per_minute=1000)
        )
        # Wait to allow natural refill beyond capacity — should be capped.
        await asyncio.sleep(0.1)
        async with limiter._lock:
            limiter._refill()
            assert limiter._request_tokens <= 60.0
            assert limiter._token_tokens <= 1000.0

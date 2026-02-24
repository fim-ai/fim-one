"""Token-bucket rate limiter for LLM API calls."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitConfig:
    """Configuration for the token-bucket rate limiter.

    Args:
        requests_per_minute: Maximum number of requests allowed per minute.
        tokens_per_minute: Maximum number of tokens allowed per minute.
    """

    requests_per_minute: int = 60
    tokens_per_minute: int = 100_000


class TokenBucketRateLimiter:
    """A dual token-bucket rate limiter for request count and token count.

    Both buckets refill continuously at a steady rate.  When a bucket is empty,
    callers are made to wait (never rejected).

    This class is safe for concurrent use via an internal ``asyncio.Lock``.

    Args:
        config: Rate limit configuration.
    """

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self._config = config or RateLimitConfig()

        # Request bucket
        self._request_tokens: float = float(self._config.requests_per_minute)
        self._request_rate: float = self._config.requests_per_minute / 60.0  # per second

        # Token bucket
        self._token_tokens: float = float(self._config.tokens_per_minute)
        self._token_rate: float = self._config.tokens_per_minute / 60.0  # per second

        self._last_refill: float = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        """Refill both buckets based on elapsed time since last refill."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._last_refill = now

        self._request_tokens = min(
            float(self._config.requests_per_minute),
            self._request_tokens + elapsed * self._request_rate,
        )
        self._token_tokens = min(
            float(self._config.tokens_per_minute),
            self._token_tokens + elapsed * self._token_rate,
        )

    def _wait_time(self, estimated_tokens: int) -> float:
        """Calculate how long to wait before both buckets can serve the request.

        Args:
            estimated_tokens: Estimated number of tokens the request will consume.

        Returns:
            Seconds to wait (0.0 if the request can proceed immediately).
        """
        wait = 0.0

        # Need at least 1 request token
        if self._request_tokens < 1.0:
            wait = max(wait, (1.0 - self._request_tokens) / self._request_rate)

        # Need at least `estimated_tokens` token tokens
        needed = float(estimated_tokens)
        if self._token_tokens < needed:
            wait = max(wait, (needed - self._token_tokens) / self._token_rate)

        return wait

    async def acquire(self, estimated_tokens: int = 0) -> None:
        """Wait until the rate limiter permits the request, then consume tokens.

        Args:
            estimated_tokens: Estimated token count for the upcoming request.
                When unknown, pass 0 to only throttle on request count.
        """
        while True:
            async with self._lock:
                self._refill()
                wait = self._wait_time(estimated_tokens)

                if wait <= 0:
                    # Consume from both buckets.
                    self._request_tokens -= 1.0
                    self._token_tokens -= float(estimated_tokens)
                    return

            # Release the lock while sleeping so other coroutines can proceed.
            logger.debug(
                "Rate limiter waiting %.2fs (request_tokens=%.1f, token_tokens=%.1f)",
                wait,
                self._request_tokens,
                self._token_tokens,
            )
            await asyncio.sleep(wait)

    async def report_usage(self, actual_tokens: int, estimated_tokens: int = 0) -> None:
        """Adjust the token bucket after learning the actual token count.

        If the actual usage exceeds the initial estimate, the difference is
        subtracted from the token bucket.  If usage was lower than estimated,
        tokens are returned to the bucket.

        Args:
            actual_tokens: The real number of tokens consumed.
            estimated_tokens: The estimate passed to ``acquire()`` earlier.
        """
        diff = actual_tokens - estimated_tokens
        if diff == 0:
            return
        async with self._lock:
            self._refill()
            self._token_tokens -= float(diff)

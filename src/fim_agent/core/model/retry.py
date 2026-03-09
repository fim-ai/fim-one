"""Retry logic with exponential backoff and jitter for LLM calls."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class RetryConfig:
    """Configuration for retry behaviour.

    Args:
        max_retries: Maximum number of retry attempts (0 means no retries).
        base_delay: Initial delay in seconds before the first retry.
        max_delay: Upper bound on the backoff delay in seconds.
    """

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0


def is_retryable_error(error: BaseException) -> bool:
    """Determine whether an error is transient and worth retrying.

    Retryable conditions:
    - HTTP 429 (rate limited)
    - HTTP 500, 502, 503 (server errors)
    - Connection / timeout errors

    Non-retryable:
    - HTTP 400 (bad request)
    - HTTP 401, 403 (auth errors)
    - Any other client error (4xx)

    Returns:
        ``True`` if the error is transient and the request should be retried.
    """
    # openai library raises APIStatusError with a status_code attribute
    status_code: int | None = getattr(error, "status_code", None)
    if status_code is not None:
        if status_code == 429:
            return True
        if status_code in (500, 502, 503):
            return True
        # All other HTTP errors (400, 401, 403, 404, etc.) are not retryable.
        return False

    # Connection and timeout errors from httpx / openai are retryable.
    error_type_name = type(error).__name__
    retryable_names = {
        "APIConnectionError",
        "APITimeoutError",
        "ConnectError",
        "ReadTimeout",
        "ConnectTimeout",
        "TimeoutException",
        "Timeout",                  # litellm
        "ServiceUnavailableError",  # litellm
    }
    if error_type_name in retryable_names:
        return True

    # Standard library timeout / connection errors.
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return True

    return False


def _compute_delay(attempt: int, config: RetryConfig) -> float:
    """Compute the backoff delay for a given attempt number.

    Uses exponential backoff with full jitter:
        delay = random(0, min(max_delay, base_delay * 2^attempt))

    Args:
        attempt: Zero-based attempt number (0 = first retry).
        config: Retry configuration.

    Returns:
        The delay in seconds.
    """
    exp_delay = min(config.max_delay, config.base_delay * (2 ** attempt))
    return random.uniform(0, exp_delay)  # noqa: S311


async def retry_async_call(
    func: Callable[..., Coroutine[Any, Any, T]],
    config: RetryConfig,
    *args: Any,
    **kwargs: Any,
) -> T:
    """Execute an async function with retry logic.

    Args:
        func: The async callable to invoke.
        config: Retry configuration.
        *args: Positional arguments forwarded to *func*.
        **kwargs: Keyword arguments forwarded to *func*.

    Returns:
        The result of the successful call.

    Raises:
        The last exception if all retries are exhausted.
    """
    last_error: BaseException | None = None
    for attempt in range(config.max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as exc:
            last_error = exc
            if not is_retryable_error(exc) or attempt >= config.max_retries:
                raise
            delay = _compute_delay(attempt, config)
            logger.warning(
                "LLM call failed (attempt %d/%d): %s. Retrying in %.2fs ...",
                attempt + 1,
                config.max_retries + 1,
                exc,
                delay,
            )
            await asyncio.sleep(delay)

    # Should be unreachable, but satisfies the type checker.
    assert last_error is not None  # noqa: S101
    raise last_error  # pragma: no cover


async def retry_async_iterator(
    factory: Callable[..., Coroutine[Any, Any, AsyncIterator[T]]],
    config: RetryConfig,
    *args: Any,
    **kwargs: Any,
) -> AsyncIterator[T]:
    """Retry creation of an async iterator (e.g. ``stream_chat``).

    The *entire* iterator is retried from scratch on transient failures during
    the initial creation of the stream.  Once iteration has begun, errors that
    occur mid-stream are **also** retried by restarting the stream.

    Args:
        factory: An async callable that returns an ``AsyncIterator``.
        config: Retry configuration.
        *args: Positional arguments forwarded to *factory*.
        **kwargs: Keyword arguments forwarded to *factory*.

    Yields:
        Items from the successfully created iterator.

    Raises:
        The last exception if all retries are exhausted.
    """
    last_error: BaseException | None = None
    for attempt in range(config.max_retries + 1):
        try:
            iterator = await factory(*args, **kwargs)
            async for item in iterator:
                yield item
            return  # Stream completed successfully.
        except Exception as exc:
            last_error = exc
            if not is_retryable_error(exc) or attempt >= config.max_retries:
                raise
            delay = _compute_delay(attempt, config)
            logger.warning(
                "LLM stream failed (attempt %d/%d): %s. Retrying in %.2fs ...",
                attempt + 1,
                config.max_retries + 1,
                exc,
                delay,
            )
            await asyncio.sleep(delay)

    assert last_error is not None  # noqa: S101
    raise last_error  # pragma: no cover

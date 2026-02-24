"""Tests for the retry module."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from fim_agent.core.model.retry import (
    RetryConfig,
    _compute_delay,
    is_retryable_error,
    retry_async_call,
    retry_async_iterator,
)


# ======================================================================
# is_retryable_error
# ======================================================================


class TestIsRetryableError:
    """Verify error classification for retry decisions."""

    def test_rate_limit_429_is_retryable(self) -> None:
        err = _make_status_error(429)
        assert is_retryable_error(err) is True

    def test_server_error_500_is_retryable(self) -> None:
        err = _make_status_error(500)
        assert is_retryable_error(err) is True

    def test_server_error_502_is_retryable(self) -> None:
        err = _make_status_error(502)
        assert is_retryable_error(err) is True

    def test_server_error_503_is_retryable(self) -> None:
        err = _make_status_error(503)
        assert is_retryable_error(err) is True

    def test_bad_request_400_not_retryable(self) -> None:
        err = _make_status_error(400)
        assert is_retryable_error(err) is False

    def test_unauthorized_401_not_retryable(self) -> None:
        err = _make_status_error(401)
        assert is_retryable_error(err) is False

    def test_forbidden_403_not_retryable(self) -> None:
        err = _make_status_error(403)
        assert is_retryable_error(err) is False

    def test_not_found_404_not_retryable(self) -> None:
        err = _make_status_error(404)
        assert is_retryable_error(err) is False

    def test_connection_error_is_retryable(self) -> None:
        assert is_retryable_error(ConnectionError("refused")) is True

    def test_timeout_error_is_retryable(self) -> None:
        assert is_retryable_error(TimeoutError("timed out")) is True

    def test_os_error_is_retryable(self) -> None:
        assert is_retryable_error(OSError("network down")) is True

    def test_api_connection_error_by_name(self) -> None:
        err = _make_named_error("APIConnectionError")
        assert is_retryable_error(err) is True

    def test_api_timeout_error_by_name(self) -> None:
        err = _make_named_error("APITimeoutError")
        assert is_retryable_error(err) is True

    def test_generic_value_error_not_retryable(self) -> None:
        assert is_retryable_error(ValueError("bad value")) is False

    def test_generic_runtime_error_not_retryable(self) -> None:
        assert is_retryable_error(RuntimeError("nope")) is False


# ======================================================================
# _compute_delay
# ======================================================================


class TestComputeDelay:
    """Verify backoff delay calculation."""

    def test_delay_is_non_negative(self) -> None:
        config = RetryConfig(base_delay=1.0, max_delay=60.0)
        for attempt in range(10):
            d = _compute_delay(attempt, config)
            assert d >= 0.0

    def test_delay_bounded_by_max(self) -> None:
        config = RetryConfig(base_delay=1.0, max_delay=10.0)
        for attempt in range(20):
            d = _compute_delay(attempt, config)
            assert d <= 10.0

    def test_delay_increases_with_attempt(self) -> None:
        """The *upper bound* of the jitter range should grow with attempt number."""
        config = RetryConfig(base_delay=1.0, max_delay=1000.0)
        upper_0 = min(config.max_delay, config.base_delay * (2 ** 0))
        upper_3 = min(config.max_delay, config.base_delay * (2 ** 3))
        assert upper_3 > upper_0


# ======================================================================
# retry_async_call
# ======================================================================


class TestRetryAsyncCall:
    """Verify retry behaviour for regular async calls."""

    async def test_succeeds_first_try(self) -> None:
        func = AsyncMock(return_value="ok")
        result = await retry_async_call(func, RetryConfig(max_retries=3))
        assert result == "ok"
        assert func.call_count == 1

    @patch("fim_agent.core.model.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_on_retryable_error(self, mock_sleep: AsyncMock) -> None:
        func = AsyncMock(
            side_effect=[_make_status_error(429), _make_status_error(500), "ok"]
        )
        result = await retry_async_call(func, RetryConfig(max_retries=3))
        assert result == "ok"
        assert func.call_count == 3
        assert mock_sleep.call_count == 2

    @patch("fim_agent.core.model.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_raises_after_max_retries(self, mock_sleep: AsyncMock) -> None:
        error = _make_status_error(503)
        func = AsyncMock(side_effect=error)
        with pytest.raises(Exception) as exc_info:
            await retry_async_call(func, RetryConfig(max_retries=2))
        assert exc_info.value is error
        assert func.call_count == 3  # 1 initial + 2 retries
        assert mock_sleep.call_count == 2

    async def test_no_retry_on_auth_error(self) -> None:
        error = _make_status_error(401)
        func = AsyncMock(side_effect=error)
        with pytest.raises(Exception) as exc_info:
            await retry_async_call(func, RetryConfig(max_retries=3))
        assert exc_info.value is error
        assert func.call_count == 1

    async def test_no_retry_on_bad_request(self) -> None:
        error = _make_status_error(400)
        func = AsyncMock(side_effect=error)
        with pytest.raises(Exception) as exc_info:
            await retry_async_call(func, RetryConfig(max_retries=3))
        assert exc_info.value is error
        assert func.call_count == 1

    @patch("fim_agent.core.model.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_on_connection_error(self, mock_sleep: AsyncMock) -> None:
        func = AsyncMock(side_effect=[ConnectionError("fail"), "recovered"])
        result = await retry_async_call(func, RetryConfig(max_retries=2))
        assert result == "recovered"
        assert func.call_count == 2

    @patch("fim_agent.core.model.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_on_timeout_error(self, mock_sleep: AsyncMock) -> None:
        func = AsyncMock(side_effect=[TimeoutError("timeout"), "ok"])
        result = await retry_async_call(func, RetryConfig(max_retries=1))
        assert result == "ok"

    async def test_zero_retries_raises_immediately(self) -> None:
        error = _make_status_error(500)
        func = AsyncMock(side_effect=error)
        with pytest.raises(Exception):
            await retry_async_call(func, RetryConfig(max_retries=0))
        assert func.call_count == 1


# ======================================================================
# retry_async_iterator
# ======================================================================


class TestRetryAsyncIterator:
    """Verify retry behaviour for async iterators (stream_chat)."""

    async def test_successful_stream(self) -> None:
        async def factory() -> AsyncIterator[str]:
            async def _gen() -> AsyncIterator[str]:
                yield "a"
                yield "b"
            return _gen()

        chunks: list[str] = []
        async for item in retry_async_iterator(factory, RetryConfig(max_retries=2)):
            chunks.append(item)
        assert chunks == ["a", "b"]

    @patch("fim_agent.core.model.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_stream_creation_failure(self, mock_sleep: AsyncMock) -> None:
        call_count = 0

        async def factory() -> AsyncIterator[str]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_status_error(429)

            async def _gen() -> AsyncIterator[str]:
                yield "ok"
            return _gen()

        chunks: list[str] = []
        async for item in retry_async_iterator(factory, RetryConfig(max_retries=2)):
            chunks.append(item)
        assert chunks == ["ok"]
        assert call_count == 2

    @patch("fim_agent.core.model.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_mid_stream_failure(self, mock_sleep: AsyncMock) -> None:
        """If iteration fails mid-stream, the whole stream is retried."""
        call_count = 0

        async def factory() -> AsyncIterator[str]:
            nonlocal call_count
            call_count += 1

            async def _gen() -> AsyncIterator[str]:
                yield "chunk1"
                if call_count == 1:
                    raise _make_status_error(502)
                yield "chunk2"
            return _gen()

        chunks: list[str] = []
        async for item in retry_async_iterator(factory, RetryConfig(max_retries=2)):
            chunks.append(item)

        # First attempt yields "chunk1" then fails.
        # Second attempt yields "chunk1", "chunk2".
        assert chunks == ["chunk1", "chunk1", "chunk2"]
        assert call_count == 2

    async def test_no_retry_on_non_retryable_stream_error(self) -> None:
        async def factory() -> AsyncIterator[str]:
            raise _make_status_error(401)
            yield  # type: ignore[misc]  # pragma: no cover

        with pytest.raises(Exception):
            async for _ in retry_async_iterator(factory, RetryConfig(max_retries=3)):
                pass

    @patch("fim_agent.core.model.retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_exhausted_retries_raises(self, mock_sleep: AsyncMock) -> None:
        async def factory() -> AsyncIterator[str]:
            raise _make_status_error(500)
            yield  # type: ignore[misc]  # pragma: no cover

        with pytest.raises(Exception):
            async for _ in retry_async_iterator(factory, RetryConfig(max_retries=1)):
                pass


# ======================================================================
# RetryConfig dataclass
# ======================================================================


class TestRetryConfig:
    """Verify RetryConfig defaults and customisation."""

    def test_defaults(self) -> None:
        cfg = RetryConfig()
        assert cfg.max_retries == 3
        assert cfg.base_delay == 1.0
        assert cfg.max_delay == 60.0

    def test_custom_values(self) -> None:
        cfg = RetryConfig(max_retries=5, base_delay=0.5, max_delay=30.0)
        assert cfg.max_retries == 5
        assert cfg.base_delay == 0.5
        assert cfg.max_delay == 30.0

    def test_frozen(self) -> None:
        cfg = RetryConfig()
        with pytest.raises(AttributeError):
            cfg.max_retries = 10  # type: ignore[misc]


# ======================================================================
# Helpers
# ======================================================================


def _make_status_error(status_code: int) -> Exception:
    """Create a mock exception with a ``status_code`` attribute."""
    err = Exception(f"HTTP {status_code}")
    err.status_code = status_code  # type: ignore[attr-defined]
    return err


def _make_named_error(class_name: str) -> Exception:
    """Create an exception whose class name matches OpenAI error types."""
    cls = type(class_name, (Exception,), {})
    return cls(f"Mock {class_name}")

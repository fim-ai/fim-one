"""Tests for the fire-and-forget background task helper."""

from __future__ import annotations

import asyncio
import logging

import pytest

from fim_one.core.utils import _BACKGROUND_TASKS, spawn_background


@pytest.mark.asyncio
async def test_spawn_background_runs_to_completion() -> None:
    """The scheduled coroutine runs and the task is tracked then released."""
    ran = asyncio.Event()

    async def _work() -> None:
        ran.set()

    task = spawn_background(_work(), name="unit-work")
    # While in-flight the task is held by a strong reference in the registry.
    assert task in _BACKGROUND_TASKS

    await asyncio.wait_for(ran.wait(), timeout=1.0)
    await task

    # Done-callback removes it so the set does not grow unbounded.
    assert task not in _BACKGROUND_TASKS


@pytest.mark.asyncio
async def test_spawn_background_keeps_strong_reference() -> None:
    """A discarded handle must not let the task be garbage-collected mid-flight."""
    done = asyncio.Event()

    async def _work() -> None:
        await asyncio.sleep(0.01)
        done.set()

    # Intentionally drop the returned handle — the registry must keep it alive.
    spawn_background(_work())
    await asyncio.wait_for(done.wait(), timeout=1.0)


@pytest.mark.asyncio
async def test_spawn_background_logs_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An exception in the coroutine is logged, not silently swallowed."""

    async def _boom() -> None:
        raise ValueError("kaboom")

    with caplog.at_level(logging.ERROR, logger="fim_one.core.utils"):
        task = spawn_background(_boom(), name="boomer")
        # Let the task run and the done-callback fire.
        with pytest.raises(ValueError, match="kaboom"):
            await task

    assert any("Background task" in r.message and "failed" in r.message
               for r in caplog.records)
    assert task not in _BACKGROUND_TASKS


@pytest.mark.asyncio
async def test_spawn_background_cancellation_is_clean() -> None:
    """Cancelling a background task removes it from the registry without logging."""

    async def _slow() -> None:
        await asyncio.sleep(10)

    task = spawn_background(_slow())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task not in _BACKGROUND_TASKS

"""Per-user workflow run rate limiting.

Provides an in-memory sliding-window rate limiter that enforces both
runs-per-minute and concurrent-run limits on a per-user basis.

Two kinds of run share one pool of concurrent slots per user:

- a run someone is waiting on (interactive, trigger key, agent tool) takes a
  slot with :meth:`WorkflowRateLimiter.acquire` or is refused;
- a batch item takes one with :meth:`WorkflowRateLimiter.acquire_when_free`,
  which waits instead of refusing and always leaves one slot free, so a
  large batch neither exceeds the limit nor shuts out the user's other runs.
  The batch as a whole counts once against runs-per-minute
  (:meth:`WorkflowRateLimiter.admit_batch`).
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque


class WorkflowRateLimiter:
    """In-memory sliding window rate limiter for workflow executions.

    Parameters
    ----------
    max_runs_per_minute:
        Maximum number of workflow runs a single user can start within
        a rolling 60-second window.
    max_concurrent_runs:
        Maximum number of simultaneously active runs per user.
    """

    def __init__(
        self,
        max_runs_per_minute: int = 10,
        max_concurrent_runs: int = 3,
    ) -> None:
        self.max_runs_per_minute = max_runs_per_minute
        self.max_concurrent_runs = max_concurrent_runs
        # Slots a user's batch items may fill: one is always left for a run
        # the user starts by hand.
        self.max_batch_runs = max(1, max_concurrent_runs - 1)

        # user_id -> deque of timestamps (newest at right)
        self._run_timestamps: dict[str, deque[float]] = defaultdict(deque)

        # user_id -> set of currently active run_ids
        self._active_runs: dict[str, set[str]] = defaultdict(set)

        # user_id -> batch items waiting for a slot, woken when one frees up
        self._slot_waiters: dict[str, list[asyncio.Event]] = defaultdict(list)

        self._lock = asyncio.Lock()

    async def check_rate_limit(self, user_id: str) -> tuple[bool, str | None]:
        """Check whether the user is allowed to start a new run.

        .. deprecated::
            Use :meth:`acquire` instead for atomic check-and-record.

        Returns
        -------
        tuple[bool, str | None]
            ``(True, None)`` if allowed, ``(False, error_message)`` if denied.
        """
        async with self._lock:
            return self._check_rate_limit_locked(user_id)

    def _check_rate_limit_locked(self, user_id: str) -> tuple[bool, str | None]:
        """Rate limit check that must be called while holding ``self._lock``.

        Extracted so both :meth:`check_rate_limit` and :meth:`acquire` can
        share the same logic without re-acquiring the lock.
        """
        # --- concurrent run check ---
        active = self._active_runs.get(user_id, set())
        if len(active) >= self.max_concurrent_runs:
            return (
                False,
                f"Too many concurrent workflow runs. "
                f"Maximum is {self.max_concurrent_runs} simultaneous runs.",
            )

        return self._check_window_locked(user_id)

    def _check_window_locked(self, user_id: str) -> tuple[bool, str | None]:
        """Runs-per-minute check; call while holding ``self._lock``."""
        now = time.time()
        timestamps = self._run_timestamps[user_id]
        cutoff = now - 60.0

        # Evict expired entries
        while timestamps and timestamps[0] <= cutoff:
            timestamps.popleft()

        if len(timestamps) >= self.max_runs_per_minute:
            return (
                False,
                f"Rate limit exceeded. Maximum is "
                f"{self.max_runs_per_minute} workflow runs per minute.",
            )

        return (True, None)

    async def acquire(self, user_id: str, run_id: str) -> tuple[bool, str | None]:
        """Atomically check rate limits and record a run start.

        Combines :meth:`check_rate_limit` and :meth:`record_run_start` into
        a single operation under the lock, eliminating the TOCTOU window
        between checking and recording.

        Returns
        -------
        tuple[bool, str | None]
            ``(True, None)`` if the run was accepted and recorded,
            ``(False, error_message)`` if denied (nothing recorded).
        """
        async with self._lock:
            allowed, error = self._check_rate_limit_locked(user_id)
            if not allowed:
                return (allowed, error)

            # Record the run start atomically with the check
            self._run_timestamps[user_id].append(time.time())
            self._active_runs[user_id].add(run_id)
            return (True, None)

    async def record_run_start(self, user_id: str, run_id: str) -> None:
        """Record that a workflow run has started.

        .. deprecated::
            Use :meth:`acquire` instead for atomic check-and-record.
        """
        async with self._lock:
            self._run_timestamps[user_id].append(time.time())
            self._active_runs[user_id].add(run_id)

    async def admit_batch(self, user_id: str) -> tuple[bool, str | None]:
        """Count a whole batch as one start against runs-per-minute.

        Atomic check-and-record, like :meth:`acquire`, but takes no
        concurrent slot: the batch's items each take one with
        :meth:`acquire_when_free` as they start.

        Returns
        -------
        tuple[bool, str | None]
            ``(True, None)`` if the batch was accepted and recorded,
            ``(False, error_message)`` if denied (nothing recorded).
        """
        async with self._lock:
            allowed, error = self._check_window_locked(user_id)
            if allowed:
                self._run_timestamps[user_id].append(time.time())
            return (allowed, error)

    async def acquire_when_free(self, user_id: str, run_id: str) -> None:
        """Take a concurrent slot for a batch item, waiting until one is free.

        Never refuses. The item starts only while fewer than
        ``max_batch_runs`` of the user's runs are active, so it counts
        toward the concurrent limit like any other run and one slot always
        stays free. It does not count against runs-per-minute (the batch did,
        once, in :meth:`admit_batch`).
        """
        while True:
            async with self._lock:
                if len(self._active_runs[user_id]) < self.max_batch_runs:
                    self._active_runs[user_id].add(run_id)
                    return
                # Created here, in the waiting task's own event loop.
                freed = asyncio.Event()
                self._slot_waiters[user_id].append(freed)
            try:
                await freed.wait()
            finally:
                waiters = self._slot_waiters.get(user_id)
                if waiters is not None and freed in waiters:
                    waiters.remove(freed)

    async def record_run_end(self, user_id: str, run_id: str) -> None:
        """Record that a workflow run has completed (any final status)."""
        async with self._lock:
            self._active_runs[user_id].discard(run_id)
            # Every waiting batch item re-checks; those that still find no
            # slot wait again.
            for freed in self._slot_waiters.pop(user_id, []):
                freed.set()

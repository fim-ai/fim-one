"""Cross-process interrupt/inject broker.

Provides two implementations with the same interface:

- **LocalInterruptBroker** — in-process ``asyncio.Queue``-based queues.
  Used when ``REDIS_URL`` is not configured (default, ``WORKERS=1``).
- **RedisInterruptBroker** — Redis Pub/Sub relay that bridges interrupt
  messages across Uvicorn worker processes.  Used when ``REDIS_URL`` is set.

The active broker is selected at import time via :func:`get_broker`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared data types
# ---------------------------------------------------------------------------


@dataclass
class InjectedMessage:
    """A user message injected mid-stream into a running agent execution."""

    id: str
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class InterruptQueue:
    """Async-safe interrupt queue that supports recall (cancel) of pending messages."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[InjectedMessage] = asyncio.Queue()
        self._recalled: set[str] = set()
        self._lock = asyncio.Lock()

    def put(self, msg: InjectedMessage) -> None:
        self._queue.put_nowait(msg)

    async def recall(self, msg_id: str) -> bool:
        """Mark a message as recalled. Returns True if the id was not already recalled."""
        async with self._lock:
            if msg_id in self._recalled:
                return False
            self._recalled.add(msg_id)
            return True

    async def drain(self) -> list[InjectedMessage]:
        """Drain all non-recalled messages from the queue."""
        async with self._lock:
            result: list[InjectedMessage] = []
            while not self._queue.empty():
                try:
                    msg = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if msg.id not in self._recalled:
                    result.append(msg)
            return result

    def empty(self) -> bool:
        return self._queue.empty()


# ---------------------------------------------------------------------------
# Abstract broker
# ---------------------------------------------------------------------------


class InterruptBroker(ABC):
    """Interface for managing interrupt queues across workers."""

    @abstractmethod
    async def register(self, conversation_id: str) -> InterruptQueue:
        """Create a queue for a conversation and start listening for cross-worker messages."""

    @abstractmethod
    async def unregister(self, conversation_id: str) -> None:
        """Stop listening and remove the queue."""

    @abstractmethod
    async def get_local(self, conversation_id: str) -> InterruptQueue | None:
        """Return the local queue if this worker owns the stream, else None."""

    @abstractmethod
    async def inject(self, conversation_id: str, msg: InjectedMessage) -> bool:
        """Deliver an inject message. Returns True if delivered (locally or via Redis)."""

    @abstractmethod
    async def recall(self, conversation_id: str, msg_id: str) -> bool:
        """Recall a pending message. Returns True if delivered."""

    @abstractmethod
    async def close(self) -> None:
        """Clean up connections."""


# ---------------------------------------------------------------------------
# Local broker (single worker, no Redis)
# ---------------------------------------------------------------------------


class LocalInterruptBroker(InterruptBroker):
    """In-process interrupt broker using asyncio queues."""

    def __init__(self) -> None:
        self._queues: dict[str, InterruptQueue] = {}

    async def register(self, conversation_id: str) -> InterruptQueue:
        q = InterruptQueue()
        self._queues[conversation_id] = q
        return q

    async def unregister(self, conversation_id: str) -> None:
        self._queues.pop(conversation_id, None)

    async def get_local(self, conversation_id: str) -> InterruptQueue | None:
        return self._queues.get(conversation_id)

    async def inject(self, conversation_id: str, msg: InjectedMessage) -> bool:
        q = self._queues.get(conversation_id)
        if q is None:
            return False
        q.put(msg)
        return True

    async def recall(self, conversation_id: str, msg_id: str) -> bool:
        q = self._queues.get(conversation_id)
        if q is None:
            return False
        return await q.recall(msg_id)

    async def close(self) -> None:
        self._queues.clear()


# ---------------------------------------------------------------------------
# Redis broker (multi-worker)
# ---------------------------------------------------------------------------


class RedisInterruptBroker(InterruptBroker):
    """Redis Pub/Sub interrupt broker for cross-worker message delivery.

    When a stream is active on this worker, messages published by other
    workers are funnelled into the local ``InterruptQueue`` via a subscriber
    task.  When the local queue exists, inject/recall calls go directly to
    the queue (fast path).  When it does not (stream is on another worker),
    the message is published to Redis for the owning worker to pick up.
    """

    def __init__(self, redis_url: str) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._queues: dict[str, InterruptQueue] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def _channel(self, conversation_id: str) -> str:
        return f"interrupt:{conversation_id}"

    async def register(self, conversation_id: str) -> InterruptQueue:
        q = InterruptQueue()
        self._queues[conversation_id] = q

        # Start subscriber task for cross-worker messages
        pubsub = self._redis.pubsub()
        channel = self._channel(conversation_id)
        await pubsub.subscribe(channel)

        async def _listener() -> None:
            try:
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        data = json.loads(message["data"])
                    except (json.JSONDecodeError, TypeError):
                        continue

                    cmd = data.get("cmd")
                    if cmd == "inject":
                        q.put(InjectedMessage(
                            id=data["id"], content=data["content"],
                        ))
                    elif cmd == "recall":
                        await q.recall(data["id"])
            except asyncio.CancelledError:
                pass
            finally:
                await pubsub.unsubscribe(channel)
                await pubsub.close()

        self._tasks[conversation_id] = asyncio.create_task(_listener())
        return q

    async def unregister(self, conversation_id: str) -> None:
        self._queues.pop(conversation_id, None)
        task = self._tasks.pop(conversation_id, None)
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def get_local(self, conversation_id: str) -> InterruptQueue | None:
        return self._queues.get(conversation_id)

    async def inject(self, conversation_id: str, msg: InjectedMessage) -> bool:
        # Fast path: local queue exists (same worker owns the stream)
        q = self._queues.get(conversation_id)
        if q is not None:
            q.put(msg)
            return True
        # Slow path: publish to Redis for the owning worker
        payload = json.dumps({"cmd": "inject", "id": msg.id, "content": msg.content})
        n = await self._redis.publish(self._channel(conversation_id), payload)
        return bool(n > 0)  # n = number of subscribers that received the message

    async def recall(self, conversation_id: str, msg_id: str) -> bool:
        q = self._queues.get(conversation_id)
        if q is not None:
            return await q.recall(msg_id)
        payload = json.dumps({"cmd": "recall", "id": msg_id})
        n = await self._redis.publish(self._channel(conversation_id), payload)
        return bool(n > 0)

    async def close(self) -> None:
        for conv_id in list(self._tasks):
            await self.unregister(conv_id)
        await self._redis.close()


# ---------------------------------------------------------------------------
# Singleton broker factory
# ---------------------------------------------------------------------------

_broker: InterruptBroker | None = None


def get_broker() -> InterruptBroker:
    """Return the global interrupt broker, creating it on first call.

    Uses ``RedisInterruptBroker`` when ``REDIS_URL`` is set, otherwise
    falls back to ``LocalInterruptBroker``.
    """
    global _broker
    if _broker is not None:
        return _broker

    redis_url = os.environ.get("REDIS_URL", "").strip()
    if redis_url:
        try:
            _broker = RedisInterruptBroker(redis_url)
            logger.info("Interrupt broker: Redis (%s)", redis_url.split("@")[-1])
        except Exception:
            logger.warning("Failed to init Redis broker, falling back to local", exc_info=True)
            _broker = LocalInterruptBroker()
    else:
        _broker = LocalInterruptBroker()
        logger.info("Interrupt broker: local (single-worker mode)")
    return _broker

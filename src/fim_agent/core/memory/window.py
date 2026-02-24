"""Sliding-window conversation memory."""

from __future__ import annotations

import asyncio

from fim_agent.core.model.types import ChatMessage

from .base import BaseMemory


class WindowMemory(BaseMemory):
    """A fixed-size sliding window over the conversation history.

    Keeps the most recent *max_messages* non-system messages.  System messages
    are always preserved and never count towards the window limit.

    Args:
        max_messages: Maximum number of non-system messages to retain.
    """

    def __init__(self, max_messages: int = 20) -> None:
        self._max_messages = max_messages
        self._messages: list[ChatMessage] = []
        self._lock = asyncio.Lock()

    async def add_message(self, message: ChatMessage) -> None:
        async with self._lock:
            self._messages.append(message)
            self._trim()

    async def get_messages(self) -> list[ChatMessage]:
        async with self._lock:
            return list(self._messages)

    async def clear(self) -> None:
        async with self._lock:
            self._messages.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _trim(self) -> None:
        """Ensure non-system messages do not exceed *max_messages*."""
        system_msgs = [m for m in self._messages if m.role == "system"]
        non_system = [m for m in self._messages if m.role != "system"]

        if len(non_system) > self._max_messages:
            non_system = non_system[-self._max_messages :]

        # Rebuild: system messages first, then the windowed non-system messages.
        self._messages = system_msgs + non_system

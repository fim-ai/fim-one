"""Abstract base class for conversation memory."""

from __future__ import annotations

from abc import ABC, abstractmethod

from fim_agent.core.model.types import ChatMessage


class BaseMemory(ABC):
    """Abstract base for all memory implementations.

    A memory object stores and retrieves conversation messages so that an agent
    can maintain context across multiple ``run()`` calls.
    """

    @abstractmethod
    async def add_message(self, message: ChatMessage) -> None:
        """Persist a single message to the conversation history.

        Args:
            message: The message to store.
        """
        ...

    @abstractmethod
    async def get_messages(self) -> list[ChatMessage]:
        """Retrieve the stored conversation history.

        Returns:
            A list of messages representing the conversation so far.
        """
        ...

    @abstractmethod
    async def clear(self) -> None:
        """Remove all stored messages."""
        ...

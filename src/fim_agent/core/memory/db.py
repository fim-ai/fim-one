"""Database-backed conversation memory.

Loads persisted messages from the database so that a ReAct agent can see
prior turns in the same conversation.  Writing is a no-op because
``chat.py`` already handles full persistence (with metadata and usage
tracking).

When an optional *compact_llm* is provided, long histories are compressed
via :meth:`CompactUtils.llm_compact` instead of the heuristic
:meth:`CompactUtils.smart_truncate`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fim_agent.core.model.types import ChatMessage

from .base import BaseMemory
from .compact import CompactUtils

if TYPE_CHECKING:
    from fim_agent.core.model import BaseLLM

logger = logging.getLogger(__name__)


class DbMemory(BaseMemory):
    """Read-only memory backed by the messages table.

    Args:
        conversation_id: The conversation whose history to load.
        max_tokens: Token budget for the returned history.
        compact_llm: Optional fast LLM for summarising old turns.  When
            provided, :meth:`CompactUtils.llm_compact` is used instead of
            heuristic truncation.
    """

    def __init__(
        self,
        conversation_id: str,
        max_tokens: int = 32_000,
        compact_llm: BaseLLM | None = None,
    ) -> None:
        self._conversation_id = conversation_id
        self._max_tokens = max_tokens
        self._compact_llm = compact_llm

    async def get_messages(self) -> list[ChatMessage]:
        """Load conversation history from DB, trim trailing user msg, compact.

        When a *compact_llm* was provided at init time, long histories are
        summarised via an LLM call.  Otherwise falls back to heuristic
        truncation.

        Returns:
            A list of ``ChatMessage`` objects fitting within the token budget.
        """
        try:
            from fim_agent.db import create_session
            from fim_agent.web.models import Message as MessageModel
            from sqlalchemy import select as sa_select

            session = create_session()
            try:
                stmt = (
                    sa_select(MessageModel)
                    .where(
                        MessageModel.conversation_id == self._conversation_id,
                        MessageModel.role.in_(["user", "assistant"]),
                    )
                    .order_by(MessageModel.created_at)
                )
                result = await session.execute(stmt)
                rows = result.scalars().all()

                messages = [
                    ChatMessage(role=row.role, content=row.content or "")
                    for row in rows
                ]
            finally:
                await session.close()

            # Drop the trailing user message — chat.py already saved the
            # current query to DB before creating this memory, and the agent
            # will append it again via messages.append(user(query)).
            if messages and messages[-1].role == "user":
                messages.pop()

            if self._compact_llm is not None:
                return await CompactUtils.llm_compact(
                    messages, self._compact_llm, self._max_tokens,
                )
            return CompactUtils.smart_truncate(messages, self._max_tokens)

        except Exception:
            logger.warning(
                "DbMemory: failed to load history for conversation %s",
                self._conversation_id,
                exc_info=True,
            )
            return []

    async def add_message(self, message: ChatMessage) -> None:
        """No-op — persistence is handled by chat.py."""

    async def clear(self) -> None:
        """No-op — clearing conversation history is not supported via memory."""

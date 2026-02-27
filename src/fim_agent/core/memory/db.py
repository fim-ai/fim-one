"""Database-backed conversation memory.

Loads persisted messages from the database so that a ReAct agent can see
prior turns in the same conversation.  Writing is a no-op because
``chat.py`` already handles full persistence (with metadata and usage
tracking).

When an optional *compact_llm* is provided, long histories are compressed
via :meth:`CompactUtils.llm_compact` instead of the heuristic
:meth:`CompactUtils.smart_truncate`.

Image reconstruction
--------------------
User messages that were sent with image attachments store image metadata
(file_id, filename, mime_type) in ``MessageModel.metadata_["images"]``.
When loading history, the original base64 data-URLs are rebuilt from disk
so that subsequent LLM calls can "see" the images again.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fim_agent.core.model.types import ChatMessage

from .base import BaseMemory
from .compact import CompactUtils

if TYPE_CHECKING:
    from fim_agent.core.model import BaseLLM

logger = logging.getLogger(__name__)


def _rebuild_image_urls(
    images_meta: list[dict[str, Any]],
    user_id: str,
) -> list[str]:
    """Rebuild base64 data-URLs from stored image metadata.

    Args:
        images_meta: List of image metadata dicts (each with ``file_id``,
            ``filename``, ``mime_type``).
        user_id: Owner of the uploaded files.

    Returns:
        A list of ``data:<mime>;base64,...`` strings.
    """
    upload_dir = Path(os.environ.get("UPLOADS_DIR", "uploads"))

    # Lazy import to avoid circular dependency at module level.
    from fim_agent.web.api.files import _load_index

    index = _load_index(user_id)
    urls: list[str] = []

    for img in images_meta:
        file_id = img.get("file_id", "")
        mime_type = img.get("mime_type", "image/png")
        meta = index.get(file_id)
        if not meta:
            continue
        file_path = upload_dir / f"user_{user_id}" / meta["stored_name"]
        if not file_path.exists():
            continue
        raw = file_path.read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        urls.append(f"data:{mime_type};base64,{b64}")

    return urls


class DbMemory(BaseMemory):
    """Read-only memory backed by the messages table.

    Args:
        conversation_id: The conversation whose history to load.
        max_tokens: Token budget for the returned history.
        compact_llm: Optional fast LLM for summarising old turns.  When
            provided, :meth:`CompactUtils.llm_compact` is used instead of
            heuristic truncation.
        user_id: Optional user ID for reconstructing vision content from
            uploaded image files.
    """

    def __init__(
        self,
        conversation_id: str,
        max_tokens: int = 32_000,
        compact_llm: BaseLLM | None = None,
        user_id: str | None = None,
    ) -> None:
        self._conversation_id = conversation_id
        self._max_tokens = max_tokens
        self._compact_llm = compact_llm
        self._user_id = user_id
        # Compact tracking — set after get_messages() runs.
        self.was_compacted: bool = False
        self._original_count: int = 0
        self._compacted_count: int = 0

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

                messages: list[ChatMessage] = []
                for row in rows:
                    content: str | list[dict[str, Any]] = row.content or ""
                    # Reconstruct vision content when a user message had images.
                    if (
                        row.role == "user"
                        and self._user_id
                        and row.metadata_
                    ):
                        meta = (
                            row.metadata_
                            if isinstance(row.metadata_, dict)
                            else {}
                        )
                        images_meta = meta.get("images", [])
                        if images_meta:
                            image_urls = _rebuild_image_urls(
                                images_meta, self._user_id,
                            )
                            if image_urls:
                                content = ChatMessage.build_vision_content(
                                    row.content or "", image_urls,
                                )
                    messages.append(
                        ChatMessage(role=row.role, content=content),
                    )
            finally:
                await session.close()

            # Drop the trailing user message — chat.py already saved the
            # current query to DB before creating this memory, and the agent
            # will append it again via messages.append(user(query)).
            if messages and messages[-1].role == "user":
                messages.pop()

            self._original_count = len(messages)

            if self._compact_llm is not None:
                result = await CompactUtils.llm_compact(
                    messages, self._compact_llm, self._max_tokens,
                )
            else:
                result = CompactUtils.smart_truncate(messages, self._max_tokens)

            self._compacted_count = len(result)
            self.was_compacted = self._compacted_count < self._original_count
            return result

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

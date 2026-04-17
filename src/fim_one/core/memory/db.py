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

Conversation Recovery (MVP)
---------------------------
When a turn is interrupted mid-flight (Stop button, SSE disconnect, crash)
the DB can be left with an ``assistant`` row carrying ``tool_calls`` that
never received matching ``role="tool"`` results.  :func:`_repair_dangling_tool_calls`
heals this on the read path by injecting placeholder tool_results into
the returned ``ChatMessage`` list.

To prevent repeated synthesis on every subsequent turn, the repaired
synthetic results are also **persisted** to the DB as new ``role="tool"``
messages (tagged via ``metadata_["synthetic"]`` / ``metadata_["tool_call_id"]``).
The write is best-effort: if it fails, the in-memory repair still runs
so the read always returns a well-formed trajectory.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from fim_one.core.model.types import ChatMessage

from .base import BaseMemory
from .compact import CompactUtils

if TYPE_CHECKING:
    from fim_one.core.model import BaseLLM
    from fim_one.core.model.usage import UsageTracker

logger = logging.getLogger(__name__)


_DOC_EXTENSIONS = {".pdf", ".docx", ".doc", ".pptx", ".ppt"}


# Synthetic content used for dangling tool_calls that never received a
# matching ``role="tool"`` response (e.g. user hit Stop, SSE disconnected,
# backend crashed mid-turn).  Surfaced to the LLM on the next turn so the
# trajectory topology stays well-formed.
_INTERRUPTED_TOOL_RESULT = "[interrupted: tool execution did not complete]"


def _repair_dangling_tool_calls(
    messages: list[ChatMessage],
    conversation_id: str,
    *,
    synthesized_sink: list[ChatMessage] | None = None,
) -> list[ChatMessage]:
    """Insert synthetic ``tool`` results for any dangling assistant tool_calls.

    When a conversation is interrupted mid-turn, the DB may contain an
    ``assistant`` message whose ``tool_calls`` never received matching
    ``role="tool"`` messages with the corresponding ``tool_call_id``.  On
    the next turn, Anthropic/OpenAI compatible endpoints reject such a
    message list with a 400 error because the trajectory is broken.

    This function scans the list and, for every assistant message with
    ``tool_calls``, verifies that each call id has a matching ``tool``
    message somewhere later in the list.  Missing results are synthesized
    with a short placeholder and inserted immediately after the assistant
    message so downstream consumers see a well-formed trajectory.

    The DB is never mutated by this function — the repair is a view
    applied on the read path.  Callers wishing to **persist** the
    synthetic rows may pass a list as ``synthesized_sink``; the helper
    will append every newly-synthesized ``ChatMessage`` to that list so
    the caller can write them back to the DB (see
    :func:`_persist_synthetic_tool_results`).

    Args:
        messages: The loaded message list in chronological order.
        conversation_id: Conversation identifier (for diagnostic logging).
        synthesized_sink: Optional list that will be populated with the
            synthetic ``ChatMessage`` objects created during repair.

    Returns:
        A new list with synthetic tool results inserted where needed.
        When no repair is required, a shallow copy of the input is
        returned so callers can freely mutate it.
    """
    repaired: list[ChatMessage] = []
    total_synth = 0
    idx = 0
    n = len(messages)

    while idx < n:
        msg = messages[idx]
        repaired.append(msg)

        if msg.role != "assistant" or not msg.tool_calls:
            idx += 1
            continue

        # Collect the tool_call ids that need a matching result.
        expected_ids = [tc.id for tc in msg.tool_calls]
        if not expected_ids:
            idx += 1
            continue

        # Walk the contiguous trailing ``role="tool"`` block: in an
        # OpenAI-compatible trajectory, tool results immediately follow
        # the assistant that requested them and never interleave with
        # other roles.  Copy them into ``repaired`` preserving order.
        satisfied: set[str] = set()
        look = idx + 1
        while look < n and messages[look].role == "tool":
            follow = messages[look]
            repaired.append(follow)
            if follow.tool_call_id is not None:
                satisfied.add(follow.tool_call_id)
            look += 1

        missing = [tc_id for tc_id in expected_ids if tc_id not in satisfied]
        if missing:
            total_synth += len(missing)
            # Append synthetic results AFTER any real results so the
            # original trajectory ordering is preserved.
            for tc_id in missing:
                synth = ChatMessage(
                    role="tool",
                    content=_INTERRUPTED_TOOL_RESULT,
                    tool_call_id=tc_id,
                )
                repaired.append(synth)
                if synthesized_sink is not None:
                    synthesized_sink.append(synth)

        # Skip over the already-consumed tool block.
        idx = look

    if total_synth:
        logger.warning(
            "DbMemory: repaired %d dangling tool_call(s) for conversation %s "
            "(synthetic tool_result injected on read path)",
            total_synth,
            conversation_id,
        )

    return repaired


async def _persist_synthetic_tool_results(
    conversation_id: str,
    synthesized: list[ChatMessage],
) -> int:
    """Write synthetic ``role="tool"`` rows to the DB idempotently.

    Each synthetic ``ChatMessage`` is stored as a new ``Message`` row with:

    - ``role = "tool"``
    - ``content = _INTERRUPTED_TOOL_RESULT``
    - ``metadata_ = {"synthetic": True, "reason": "interrupted",
      "tool_call_id": <id>}``

    A per-``tool_call_id`` existence check runs first so repeated calls on
    the same conversation never duplicate rows.  JSON path syntax differs
    between engines, so the query is dialect-aware (SQLite uses
    ``json_extract``; PostgreSQL uses the ``::json->>`` operator).

    Any DB error is swallowed — the in-memory repair still renders a
    valid trajectory for the current turn.

    Args:
        conversation_id: Conversation the synthetic messages belong to.
        synthesized: The ``ChatMessage`` objects produced by
            :func:`_repair_dangling_tool_calls`.

    Returns:
        The number of rows actually inserted.  ``0`` when every
        ``tool_call_id`` already had a synthetic row or when the write
        failed.
    """
    if not synthesized:
        return 0

    tool_call_ids = [m.tool_call_id for m in synthesized if m.tool_call_id]
    if not tool_call_ids:
        return 0

    try:
        from fim_one.db import create_session
        from fim_one.web.models import Message as MessageModel
        from sqlalchemy import select as sa_select, text as sa_text

        async with create_session() as session:
            bind = session.get_bind()
            dialect = bind.dialect.name if bind is not None else "sqlite"

            # Dialect-aware JSON path extraction.  SQLite is the dev default;
            # PostgreSQL is production.  Both return the stored tool_call_id
            # (or NULL) without needing a new column.
            if dialect == "postgresql":
                expr = sa_text("metadata::json->>'tool_call_id'")
            else:
                expr = sa_text("json_extract(metadata, '$.tool_call_id')")

            stmt = sa_select(expr).where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.role == "tool",
            )
            result = await session.execute(stmt)
            existing: set[str] = {str(row[0]) for row in result.all() if row[0] is not None}

            inserted = 0
            for synth in synthesized:
                tc_id = synth.tool_call_id
                if not tc_id or tc_id in existing:
                    continue
                row = MessageModel(
                    conversation_id=conversation_id,
                    role="tool",
                    content=_INTERRUPTED_TOOL_RESULT,
                    message_type="tool_result",
                    metadata_={
                        "synthetic": True,
                        "reason": "interrupted",
                        "tool_call_id": tc_id,
                    },
                )
                session.add(row)
                existing.add(tc_id)
                inserted += 1

            if inserted:
                await session.commit()
                logger.info(
                    "DbMemory: persisted %d synthetic tool_result row(s) for conversation %s",
                    inserted,
                    conversation_id,
                )
            return inserted
    except Exception:
        logger.warning(
            "DbMemory: failed to persist synthetic tool_result rows for "
            "conversation %s — in-memory repair still applied",
            conversation_id,
            exc_info=True,
        )
        return 0


async def _rebuild_vision_urls(
    images_meta: list[dict[str, Any]],
    user_id: str,
) -> list[str]:
    """Rebuild base64 data-URLs from stored image metadata.

    Handles two sources (dispatched by ``source`` field):
      - ``"upload"`` (default): direct image file — read raw bytes, encode.
      - ``"document"``: DOCX/PDF/PPTX — extract embedded images via
        :class:`DocumentProcessor`.

    Args:
        images_meta: List of dicts with ``file_id``, ``filename``,
            ``mime_type``, and optional ``source``.
        user_id: Owner of the uploaded files.

    Returns:
        A list of ``data:<mime>;base64,...`` strings.
    """
    upload_dir = Path(os.environ.get("UPLOADS_DIR", "uploads"))

    from fim_one.web.api.files import _load_index

    index = _load_index(user_id)
    urls: list[str] = []

    for img in images_meta:
        file_id = img.get("file_id", "")
        source = img.get("source", "upload")
        meta = index.get(file_id)
        if not meta:
            continue
        file_path = upload_dir / f"user_{user_id}" / str(meta["stored_name"])
        if not file_path.exists():
            continue

        try:
            if source == "document":
                suffix = Path(str(meta.get("filename", ""))).suffix.lower()
                from fim_one.core.document import DocumentProcessor

                if suffix == ".pdf":
                    # Smart extraction: only embedded images + scanned
                    # page renders (skip full-page PNG for text pages).
                    raw_images = await DocumentProcessor.extract_pdf_images(file_path)
                    for img_bytes in raw_images:
                        if img_bytes[:2] == b"\xff\xd8":
                            mime = "image/jpeg"
                        elif img_bytes[:4] == b"\x89PNG":
                            mime = "image/png"
                        else:
                            mime = "image/png"
                        b64 = base64.b64encode(img_bytes).decode("ascii")
                        urls.append(f"data:{mime};base64,{b64}")
                elif suffix in _DOC_EXTENSIONS:
                    _, image_bytes_list = await DocumentProcessor.extract_with_images(file_path)
                    for img_bytes in image_bytes_list:
                        if img_bytes[:2] == b"\xff\xd8":
                            mime = "image/jpeg"
                        elif img_bytes[:4] == b"\x89PNG":
                            mime = "image/png"
                        else:
                            mime = "image/png"
                        b64 = base64.b64encode(img_bytes).decode("ascii")
                        urls.append(f"data:{mime};base64,{b64}")
            else:
                # Default: direct image file
                mime_type = img.get("mime_type", "image/png")
                raw = file_path.read_bytes()
                b64 = base64.b64encode(raw).decode("ascii")
                urls.append(f"data:{mime_type};base64,{b64}")
        except Exception:
            logger.warning(
                "Failed to rebuild vision for file %s (source=%s)",
                file_id,
                source,
                exc_info=True,
            )

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
        usage_tracker: UsageTracker | None = None,
    ) -> None:
        self._conversation_id = conversation_id
        self._max_tokens = max_tokens
        self._compact_llm = compact_llm
        self._user_id = user_id
        self._usage_tracker = usage_tracker
        # Compact tracking — set after get_messages() runs.
        self.was_compacted: bool = False
        self._original_count: int = 0
        self._compacted_count: int = 0
        self.compacted_message_ids: list[str] = []

    async def get_messages(self) -> list[ChatMessage]:
        """Load conversation history from DB, trim trailing user msg, compact.

        When a *compact_llm* was provided at init time, long histories are
        summarised via an LLM call.  Otherwise falls back to heuristic
        truncation.

        Returns:
            A list of ``ChatMessage`` objects fitting within the token budget.
        """
        try:
            from fim_one.db import create_session
            from fim_one.web.models import Message as MessageModel
            from sqlalchemy import select as sa_select

            async with create_session() as session:
                # Include ``role="tool"`` so previously-persisted
                # synthetic tool_result rows are visible to the repair
                # pass — without them every turn would re-synthesize
                # and the persist helper would deduplicate on write but
                # the log would stay noisy.
                stmt = (
                    sa_select(MessageModel)
                    .where(
                        MessageModel.conversation_id == self._conversation_id,
                        MessageModel.role.in_(["user", "assistant", "tool"]),
                    )
                    .order_by(MessageModel.created_at)
                )
                result = await session.execute(stmt)
                rows = result.scalars().all()

                messages: list[ChatMessage] = []
                id_for_msg: dict[int, str] = {}  # id(ChatMessage) → DB row ID
                for row in rows:
                    content: str | list[dict[str, Any]] = row.content or ""
                    meta: dict[str, Any] = row.metadata_ if isinstance(row.metadata_, dict) else {}
                    # Reconstruct vision content when a user message had images.
                    if row.role == "user" and self._user_id and meta:
                        images_meta = meta.get("images", [])
                        if images_meta:
                            image_urls = await _rebuild_vision_urls(
                                images_meta,
                                self._user_id,
                            )
                            if image_urls:
                                content = ChatMessage.build_vision_content(
                                    row.content or "",
                                    image_urls,
                                )
                    raw_role = row.role
                    if raw_role not in ("system", "user", "assistant", "tool"):
                        continue
                    role = cast(Literal["system", "user", "assistant", "tool"], raw_role)
                    msg = ChatMessage(role=role, content=content)
                    # Restore thinking block so Anthropic replay stays
                    # valid on the next turn.  Stored by chat.py as
                    # ``metadata_["thinking"] = {"content", "signature"}``.
                    if role == "assistant":
                        thinking = meta.get("thinking")
                        if isinstance(thinking, dict):
                            reasoning = thinking.get("content")
                            signature = thinking.get("signature")
                            if isinstance(reasoning, str) and reasoning:
                                msg.reasoning_content = reasoning
                            if isinstance(signature, str) and signature:
                                msg.signature = signature
                    # Restore tool_call_id for persisted ``role="tool"``
                    # rows so the repair pass can match them against the
                    # assistant's tool_calls.  Synthetic recovery rows
                    # (see ``_persist_synthetic_tool_results``) store the
                    # id under ``metadata_["tool_call_id"]``.
                    if role == "tool":
                        tc_id = meta.get("tool_call_id") if meta else None
                        if isinstance(tc_id, str) and tc_id:
                            msg.tool_call_id = tc_id
                    messages.append(msg)
                    id_for_msg[id(msg)] = str(row.id)

            # Drop the trailing user message — chat.py already saved the
            # current query to DB before creating this memory, and the agent
            # will append it again via messages.append(user(query)).
            if messages and messages[-1].role == "user":
                popped = messages.pop()
                id_for_msg.pop(id(popped), None)

            # Snapshot all IDs before filtering / compaction.
            all_ids = {id_for_msg[id(m)] for m in messages if id(m) in id_for_msg}

            # Drop empty assistant messages whose content is empty AND
            # which carry no ``tool_calls``.  An empty-content assistant
            # with ``tool_calls`` is a legitimate native FC intermediate
            # and must be preserved so the trajectory repair step below
            # can validate / heal it.
            pre_filter = len(messages)
            messages = [
                m
                for m in messages
                if not (
                    m.role == "assistant"
                    and not m.tool_calls
                    and not CompactUtils.content_as_text(m.content).strip()
                )
            ]
            if len(messages) < pre_filter:
                logger.debug(
                    "DbMemory: dropped %d empty assistant message(s)",
                    pre_filter - len(messages),
                )

            # Repair dangling tool_calls left behind by interrupted turns
            # (Stop button, SSE disconnect, crash).  The repair both:
            #   (a) returns a well-formed trajectory for this turn, and
            #   (b) collects any newly-synthesized tool_result messages
            #       so we can persist them — so the next turn doesn't
            #       re-synthesize the same rows and interrupted artefacts
            #       are durably captured in conversation history.
            synthesized: list[ChatMessage] = []
            messages = _repair_dangling_tool_calls(
                messages,
                self._conversation_id,
                synthesized_sink=synthesized,
            )
            if synthesized:
                await _persist_synthetic_tool_results(
                    self._conversation_id,
                    synthesized,
                )

            self._original_count = len(messages)

            if self._compact_llm is not None:
                compacted = await CompactUtils.llm_compact(
                    messages,
                    self._compact_llm,
                    self._max_tokens,
                    usage_tracker=self._usage_tracker,
                )
            else:
                compacted = CompactUtils.smart_truncate(messages, self._max_tokens)

            # Track which original messages survived compaction.
            surviving_ids = {id_for_msg[id(m)] for m in compacted if id(m) in id_for_msg}
            self.compacted_message_ids = sorted(all_ids - surviving_ids)
            if self.compacted_message_ids:
                logger.info(
                    "DbMemory: %d message(s) compacted (IDs: %s)",
                    len(self.compacted_message_ids),
                    self.compacted_message_ids[:10],
                )

            self._compacted_count = len(compacted)
            self.was_compacted = self._compacted_count < self._original_count
            return compacted

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

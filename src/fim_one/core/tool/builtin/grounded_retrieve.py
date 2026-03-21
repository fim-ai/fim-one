"""Evidence-grounded retrieval tool with citation extraction."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fim_one.core.tool.base import BaseTool


class GroundedRetrieveTool(BaseTool):
    """Evidence-grounded retrieval with claim-level citations and confidence scoring.

    When bound to specific knowledge bases (via ``kb_ids``), the tool does not
    require the caller to specify them explicitly — this is how agent-level
    KB bindings work.
    """

    def __init__(
        self,
        kb_ids: list[str] | None = None,
        user_id: str | None = None,
        kb_owner_map: dict[str, str] | None = None,
        confidence_threshold: float | None = None,
    ) -> None:
        self._bound_kb_ids = kb_ids or []
        self._user_id = user_id
        self._kb_owner_map = kb_owner_map or {}
        self._confidence_threshold = confidence_threshold
        self._source_offset: int = 0
        self._offset_lock: asyncio.Lock = asyncio.Lock()
        self._usage_tracker: Any = None

    def set_usage_tracker(self, tracker: Any) -> None:
        """Attach a UsageTracker for recording fast LLM token consumption."""
        self._usage_tracker = tracker

    @property
    def name(self) -> str:
        return "grounded_retrieve"

    @property
    def display_name(self) -> str:
        return "Grounded Retrieve"

    @property
    def description(self) -> str:
        return (
            "Evidence-grounded retrieval with claim-level citations "
            "and confidence scoring."
        )

    @property
    def category(self) -> str:
        return "knowledge"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        props: dict[str, Any] = {
            "query": {
                "type": "string",
                "description": "The search query to ground against knowledge bases.",
            },
        }
        required = ["query"]

        if not self._bound_kb_ids:
            props["kb_ids"] = {
                "type": "array",
                "items": {"type": "string"},
                "description": "Knowledge base IDs to search.",
            }
            required.append("kb_ids")

        props["top_k"] = {
            "type": "integer",
            "description": "Number of results to retrieve (default: 10).",
            "default": 10,
        }

        return {
            "type": "object",
            "properties": props,
            "required": required,
        }

    async def run(self, **kwargs: Any) -> str:
        query: str = kwargs.get("query", "")
        if not query:
            return "[Error] query is required"

        kb_ids: list[str] = kwargs.get("kb_ids") or self._bound_kb_ids
        if not kb_ids:
            return "[Error] kb_ids is required (no bound knowledge bases)"

        top_k: int = int(kwargs.get("top_k", 10))
        user_id = self._user_id or os.environ.get("_TOOL_USER_ID", "default")

        try:
            from fim_one.web.deps import get_embedding, get_fast_llm, get_kb_manager
            from fim_one.rag.grounding import GroundingPipeline

            kb_manager = get_kb_manager()
            embedding = get_embedding()
            fast_llm = get_fast_llm()

            config: dict[str, Any] = {"top_k": top_k}

            pipeline = GroundingPipeline(
                kb_manager=kb_manager,
                embedding=embedding,
                llm=fast_llm,
                config=config,
                usage_tracker=self._usage_tracker,
            )
            result = await pipeline.ground(
                query, kb_ids, user_id, kb_owner_map=self._kb_owner_map,
            )

            # Hard gate: if confidence below threshold, refuse to answer
            # with this evidence.  Do NOT advance the offset.
            if (
                self._confidence_threshold is not None
                and result.confidence < self._confidence_threshold
            ):
                return (
                    f"[Evidence insufficient] Confidence {result.confidence:.0%} is below "
                    f"the threshold {self._confidence_threshold:.0%}. "
                    f"The retrieved evidence is not reliable enough to answer this question. "
                    f"Please inform the user that no confident evidence was found in the knowledge base."
                )

            actual = len(result.evidence) if result.evidence else 0

            # Atomically assign offset AFTER we know the actual evidence count.
            # This avoids gaps when multiple calls run concurrently via
            # asyncio.gather() — each call only reserves exactly the slots
            # it needs, and the lock ensures no interleaving.
            async with self._offset_lock:
                offset = self._source_offset
                self._source_offset += actual

            if not result.evidence:
                # No evidence — offset did not advance (actual == 0)
                pass

            # Look up KB names for display
            kb_names: dict[str, str] = {}
            try:
                from fim_one.db import create_session
                from fim_one.web.models.knowledge_base import KnowledgeBase as KBModel
                from sqlalchemy import select

                db = create_session()
                try:
                    stmt = select(KBModel.id, KBModel.name).where(
                        KBModel.id.in_(kb_ids)
                    )
                    rows = await db.execute(stmt)
                    kb_names = {row.id: row.name for row in rows}
                finally:
                    await db.close()
            except Exception:
                pass  # Graceful fallback — just won't show KB names

            return _format_grounded_result(result, kb_names, source_offset=offset)

        except Exception as exc:
            # Pipeline failed — do NOT advance the offset
            return f"[Error] {type(exc).__name__}: {exc}"


def _format_grounded_result(
    result: Any,
    kb_names: dict[str, str] | None = None,
    source_offset: int = 0,
) -> str:
    """Format a GroundedResult into a readable string for the LLM.

    Args:
        result: The GroundedResult from the grounding pipeline.
        kb_names: Optional mapping of KB IDs to display names.
        source_offset: Starting offset for source numbering (0-based).
            Sources will be numbered ``[source_offset + 1]``,
            ``[source_offset + 2]``, etc.
    """
    if not result.evidence:
        return "No relevant evidence found."

    pct = round(result.confidence * 100)
    lines = [f"**Evidence** (confidence: {pct}%, {result.total_sources} sources):\n"]

    for i, unit in enumerate(result.evidence, start=1):
        num = source_offset + i
        score = f"{unit.chunk.score:.3f}" if unit.chunk.score is not None else "N/A"
        kb_label = ""
        if kb_names and unit.kb_id:
            name = kb_names.get(unit.kb_id)
            if name:
                kb_label = f" [KB: {name}]"
        lines.append(
            f"[{num}] Source: {_source_name(unit)}{kb_label} " f"(relevance: {score})"
        )
        if unit.citations:
            for cit in unit.citations:
                page_info = f"  p.{cit.page_number}" if cit.page_number else ""
                lines.append(f'  > "{cit.text}"{page_info}')
        else:
            # Fall back to showing a content preview (keep consistent quoted format)
            preview = unit.chunk.content[:200].replace("\n", " ").strip()
            if len(unit.chunk.content) > 200:
                preview += "..."
            lines.append(f'  > "{preview}"')
        lines.append("")

    return "\n".join(lines)


def _source_name(unit: Any) -> str:
    """Extract a human-readable source name from an EvidenceUnit."""
    for key in ("source", "filename"):
        val = unit.chunk.metadata.get(key)
        if val:
            return val
    return "unknown"

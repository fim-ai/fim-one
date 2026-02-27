"""Evidence-grounded retrieval tool with citation extraction."""

from __future__ import annotations

import os
from typing import Any

from fim_agent.core.tool.base import BaseTool


class GroundedRetrieveTool(BaseTool):
    """Evidence-grounded retrieval with claim-level citations, conflict detection, and confidence scoring.

    When bound to specific knowledge bases (via ``kb_ids``), the tool does not
    require the caller to specify them explicitly — this is how agent-level
    KB bindings work.
    """

    def __init__(self, kb_ids: list[str] | None = None) -> None:
        self._bound_kb_ids = kb_ids or []

    @property
    def name(self) -> str:
        return "grounded_retrieve"

    @property
    def description(self) -> str:
        return (
            "Evidence-grounded retrieval with claim-level citations, "
            "conflict detection, and confidence scoring."
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
        user_id = os.environ.get("_TOOL_USER_ID", "default")

        try:
            from fim_agent.web.deps import get_embedding, get_fast_llm, get_kb_manager
            from fim_agent.rag.grounding import GroundingPipeline

            kb_manager = get_kb_manager()
            embedding = get_embedding()
            fast_llm = get_fast_llm()

            pipeline = GroundingPipeline(
                kb_manager=kb_manager,
                embedding=embedding,
                llm=fast_llm,
                config={"top_k": top_k},
            )
            result = await pipeline.ground(query, kb_ids, user_id)
            return _format_grounded_result(result)

        except Exception as exc:
            return f"[Error] {type(exc).__name__}: {exc}"


def _format_grounded_result(result: Any) -> str:
    """Format a GroundedResult into a readable string for the LLM."""
    if not result.evidence:
        return "No relevant evidence found."

    pct = round(result.confidence * 100)
    lines = [f"**Evidence** (confidence: {pct}%, {result.total_sources} sources):\n"]

    for i, unit in enumerate(result.evidence, start=1):
        score = f"{unit.chunk.score:.3f}" if unit.chunk.score is not None else "N/A"
        lines.append(
            f"[{i}] Source: {_source_name(unit)} "
            f"(relevance: {score}, alignment: {unit.query_alignment:.3f})"
        )
        if unit.citations:
            for cit in unit.citations:
                page_info = f"  p.{cit.page_number}" if cit.page_number else ""
                lines.append(f'  > "{cit.text}"{page_info}')
        else:
            # Fall back to showing a content preview
            preview = unit.chunk.content[:200]
            if len(unit.chunk.content) > 200:
                preview += "..."
            lines.append(f"  > {preview}")
        lines.append("")

    if result.conflicts:
        lines.append("**Conflicts detected:**")
        for conflict in result.conflicts:
            lines.append(
                f"  - {conflict.claim_a.source_name} vs {conflict.claim_b.source_name}:"
            )
            lines.append(f'    A: "{conflict.claim_a.text}"')
            lines.append(f'    B: "{conflict.claim_b.text}"')
        lines.append("")

    return "\n".join(lines)


def _source_name(unit: Any) -> str:
    """Extract a human-readable source name from an EvidenceUnit."""
    for key in ("source", "filename"):
        val = unit.chunk.metadata.get(key)
        if val:
            return val
    return "unknown"

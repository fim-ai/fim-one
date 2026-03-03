"""Knowledge Base retrieval tool for agent use."""

from __future__ import annotations

import os
from typing import Any

from fim_agent.core.tool.base import BaseTool


class KBRetrieveTool(BaseTool):
    """Retrieve relevant documents from a knowledge base.

    This tool allows agents to search a user's knowledge base for information
    relevant to a query.  Results include the text content and a relevance score.
    """

    def __init__(self, user_id: str | None = None, kb_ids: list[str] | None = None) -> None:
        self._user_id = user_id
        self._bound_kb_ids = kb_ids or []

    @property
    def name(self) -> str:
        return "kb_retrieve"

    @property
    def description(self) -> str:
        return (
            "Search a knowledge base for documents relevant to a query. "
            "Returns the most relevant text chunks with relevance scores. "
            "Use this when you need to look up information from uploaded documents."
        )

    @property
    def category(self) -> str:
        return "knowledge"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        props: dict[str, Any] = {
            "query": {
                "type": "string",
                "description": "The search query to find relevant documents.",
            },
        }
        required = ["query"]

        if not self._bound_kb_ids:
            props["kb_id"] = {
                "type": "string",
                "description": "The knowledge base ID to search in.",
            }
            required.append("kb_id")

        props["top_k"] = {
            "type": "integer",
            "description": "Number of results to return (default: 5).",
            "default": 5,
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

        kb_ids: list[str] = self._bound_kb_ids or (
            [kwargs["kb_id"]] if kwargs.get("kb_id") else []
        )
        if not kb_ids:
            return "[Error] kb_id is required (no bound knowledge bases)"

        top_k: int = int(kwargs.get("top_k", 5))
        user_id = self._user_id or os.environ.get("_TOOL_USER_ID", "default")

        try:
            from fim_agent.web.deps import get_kb_manager

            manager = get_kb_manager()

            # Search all bound KBs and merge results
            all_docs: list[Any] = []
            for kb_id in kb_ids:
                documents = await manager.retrieve(
                    query, kb_id=kb_id, user_id=user_id, top_k=top_k
                )
                all_docs.extend(documents)

            # Sort by score descending, take top_k
            all_docs.sort(key=lambda d: d.score if d.score is not None else 0, reverse=True)
            all_docs = all_docs[:top_k]

            if not all_docs:
                return "No relevant documents found."

            parts: list[str] = []
            for i, doc in enumerate(all_docs, start=1):
                score = f"{doc.score:.3f}" if doc.score is not None else "N/A"
                parts.append(f"[{i}] (score: {score})\n{doc.content}")

            return "\n\n---\n\n".join(parts)

        except Exception as exc:
            return f"[Error] {type(exc).__name__}: {exc}"

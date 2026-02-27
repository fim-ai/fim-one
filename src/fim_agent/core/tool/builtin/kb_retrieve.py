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
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to find relevant documents.",
                },
                "kb_id": {
                    "type": "string",
                    "description": "The knowledge base ID to search in.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default: 5).",
                    "default": 5,
                },
            },
            "required": ["query", "kb_id"],
        }

    async def run(self, **kwargs: Any) -> str:
        query: str = kwargs.get("query", "")
        kb_id: str = kwargs.get("kb_id", "")
        top_k: int = int(kwargs.get("top_k", 5))

        if not query:
            return "[Error] query is required"
        if not kb_id:
            return "[Error] kb_id is required"

        # Get user context from environment (set by the chat endpoint)
        user_id = os.environ.get("_TOOL_USER_ID", "default")

        try:
            from fim_agent.web.deps import get_kb_manager

            manager = get_kb_manager()
            documents = await manager.retrieve(
                query, kb_id=kb_id, user_id=user_id, top_k=top_k
            )

            if not documents:
                return "No relevant documents found."

            parts: list[str] = []
            for i, doc in enumerate(documents, start=1):
                score = f"{doc.score:.3f}" if doc.score is not None else "N/A"
                parts.append(f"[{i}] (score: {score})\n{doc.content}")

            return "\n\n---\n\n".join(parts)

        except Exception as exc:
            return f"[Error] {type(exc).__name__}: {exc}"

"""Built-in tool to list knowledge bases accessible to the current user."""

from __future__ import annotations

import os
from typing import Any

from fim_agent.core.tool.base import BaseTool


class KBListTool(BaseTool):
    """List all knowledge bases available to the current user.

    Returns each KB's id, name, description, document count, and status.
    Agents can use this to discover which knowledge bases exist before
    calling ``kb_retrieve`` or ``grounded_retrieve``.
    """

    def __init__(self, user_id: str | None = None) -> None:
        self._user_id = user_id

    @property
    def name(self) -> str:
        return "kb_list"

    @property
    def description(self) -> str:
        return (
            "List all knowledge bases available to you. "
            "Returns each KB's id, name, description, document count, and status. "
            "Use this to discover which knowledge bases you can search with kb_retrieve."
        )

    @property
    def category(self) -> str:
        return "knowledge"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def run(self, **kwargs: Any) -> str:
        user_id = self._user_id or os.environ.get("_TOOL_USER_ID", "default")

        try:
            from sqlalchemy import select

            from fim_agent.db import create_session
            from fim_agent.web.models.knowledge_base import KnowledgeBase

            async with create_session() as session:
                result = await session.execute(
                    select(KnowledgeBase)
                    .where(KnowledgeBase.user_id == user_id)
                    .where(KnowledgeBase.status == "active")
                    .order_by(KnowledgeBase.created_at.desc())
                )
                kbs = result.scalars().all()

            if not kbs:
                return "No knowledge bases found."

            lines: list[str] = [f"Found {len(kbs)} knowledge base(s):\n"]
            for kb in kbs:
                desc = f" — {kb.description}" if kb.description else ""
                lines.append(
                    f"- **{kb.name}** (id: `{kb.id}`){desc}\n"
                    f"  {kb.document_count} document(s), {kb.total_chunks} chunks"
                )
            return "\n".join(lines)

        except Exception as exc:
            return f"[Error] {type(exc).__name__}: {exc}"

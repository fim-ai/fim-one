"""Full-text search (sparse) retriever using LanceDB native FTS."""

from __future__ import annotations

from fim_agent.rag.base import BaseRetriever, Document
from fim_agent.rag.store.lancedb import LanceDBVectorStore


class FTSRetriever(BaseRetriever):
    """Retrieve documents via full-text search.

    Args:
        store: The vector store with FTS capability.
        kb_id: Knowledge base identifier.
        user_id: User identifier for data isolation.
    """

    def __init__(
        self,
        store: LanceDBVectorStore,
        *,
        kb_id: str,
        user_id: str,
    ) -> None:
        self._store = store
        self._kb_id = kb_id
        self._user_id = user_id

    async def retrieve(self, query: str, *, top_k: int = 5) -> list[Document]:
        return await self._store.fts_search(
            query,
            kb_id=self._kb_id,
            user_id=self._user_id,
            top_k=top_k,
        )

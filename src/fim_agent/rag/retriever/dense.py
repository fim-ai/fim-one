"""Dense (vector) retriever."""

from __future__ import annotations

from fim_agent.core.embedding.base import BaseEmbedding
from fim_agent.rag.base import BaseRetriever, Document
from fim_agent.rag.store.lancedb import LanceDBVectorStore


class DenseRetriever(BaseRetriever):
    """Retrieve documents via vector similarity search.

    Args:
        store: The vector store to search.
        embedding: Embedding model for query vectorisation.
        kb_id: Knowledge base identifier.
        user_id: User identifier for data isolation.
    """

    def __init__(
        self,
        store: LanceDBVectorStore,
        embedding: BaseEmbedding,
        *,
        kb_id: str,
        user_id: str,
    ) -> None:
        self._store = store
        self._embedding = embedding
        self._kb_id = kb_id
        self._user_id = user_id

    async def retrieve(self, query: str, *, top_k: int = 5) -> list[Document]:
        query_vector = await self._embedding.embed_query(query)
        return await self._store.vector_search(
            query_vector,
            kb_id=self._kb_id,
            user_id=self._user_id,
            top_k=top_k,
        )

"""Knowledge Base pipeline manager — orchestrates load → chunk → embed → store."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from fim_agent.core.embedding.base import BaseEmbedding
from fim_agent.core.reranker.base import BaseReranker
from fim_agent.rag.base import Document
from fim_agent.rag.chunking import MAX_CHUNK_SIZE, get_chunker
from fim_agent.rag.loaders import loader_for_extension
from fim_agent.rag.retriever.dense import DenseRetriever
from fim_agent.rag.retriever.hybrid import HybridRetriever
from fim_agent.rag.retriever.sparse import FTSRetriever
from fim_agent.rag.store.lancedb import LanceDBVectorStore

logger = logging.getLogger(__name__)


class KnowledgeBaseManager:
    """Orchestrate the RAG pipeline for a knowledge base.

    Args:
        store: Vector store instance.
        embedding: Embedding model.
        reranker: Optional reranker for hybrid retrieval.
    """

    def __init__(
        self,
        store: LanceDBVectorStore,
        embedding: BaseEmbedding,
        reranker: BaseReranker | None = None,
    ) -> None:
        self._store = store
        self._embedding = embedding
        self._reranker = reranker

    async def ingest_file(
        self,
        file_path: Path,
        *,
        kb_id: str,
        user_id: str,
        document_id: str,
        chunk_strategy: str = "recursive",
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
    ) -> tuple[int, str]:
        """Load, chunk, embed, and store a file.

        Args:
            file_path: Path to the source file.
            kb_id: Knowledge base ID.
            user_id: User ID.
            document_id: Document ID for tracking.
            chunk_strategy: Chunking strategy name.
            chunk_size: Chunk size in characters.
            chunk_overlap: Overlap between chunks.

        Returns:
            Tuple of (chunk_count, content_hash).
        """
        # 0. Clamp chunk_size to hard upper limit
        if chunk_size > MAX_CHUNK_SIZE:
            logger.warning(
                "chunk_size %d exceeds MAX_CHUNK_SIZE (%d), clamping",
                chunk_size,
                MAX_CHUNK_SIZE,
            )
            chunk_size = MAX_CHUNK_SIZE

        # 1. Load
        ext = file_path.suffix.lower()
        loader = loader_for_extension(ext)
        loaded_docs = await loader.load(file_path)
        if not loaded_docs:
            return 0, ""

        # 2. Compute content hash for dedup
        full_text = "\n\n".join(doc.content for doc in loaded_docs)
        content_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()

        # 3. Chunk
        chunker = get_chunker(
            chunk_strategy,
            chunk_size=chunk_size,
            overlap=chunk_overlap,
        ) if chunk_strategy != "semantic" else get_chunker(
            chunk_strategy,
            embedding=self._embedding,
        )

        all_chunks: list[tuple[str, dict[str, Any]]] = []
        for doc in loaded_docs:
            chunks = await chunker.chunk(doc.content, metadata=doc.metadata)
            for chunk in chunks:
                all_chunks.append((chunk.text, {**chunk.metadata, "chunk_index": chunk.index}))

        if not all_chunks:
            return 0, content_hash

        # 4. Embed
        texts = [t for t, _ in all_chunks]
        vectors = await self._embedding.embed_texts(texts)

        # 5. Store
        metadatas = [m for _, m in all_chunks]
        added = await self._store.add_documents(
            texts, vectors, metadatas,
            kb_id=kb_id, user_id=user_id, document_id=document_id,
        )

        logger.info(
            "Ingested %s: %d chunks (%d new), hash=%s",
            file_path.name, len(all_chunks), added, content_hash[:12],
        )
        return len(all_chunks), content_hash

    async def retrieve(
        self,
        query: str,
        *,
        kb_id: str,
        user_id: str,
        top_k: int = 5,
        mode: str = "hybrid",
    ) -> list[Document]:
        """Retrieve documents from a knowledge base.

        Args:
            query: Search query.
            kb_id: Knowledge base ID.
            user_id: User ID.
            top_k: Number of results.
            mode: Retrieval mode ("hybrid", "dense", or "fts").

        Returns:
            List of relevant Document objects.
        """
        if mode == "dense":
            retriever = DenseRetriever(
                self._store, self._embedding, kb_id=kb_id, user_id=user_id
            )
        elif mode == "fts":
            retriever = FTSRetriever(
                self._store, kb_id=kb_id, user_id=user_id
            )
        else:
            dense = DenseRetriever(
                self._store, self._embedding, kb_id=kb_id, user_id=user_id
            )
            sparse = FTSRetriever(
                self._store, kb_id=kb_id, user_id=user_id
            )
            retriever = HybridRetriever(
                dense, sparse, reranker=self._reranker
            )

        return await retriever.retrieve(query, top_k=top_k)

    async def delete_document(
        self, *, kb_id: str, user_id: str, document_id: str
    ) -> int:
        """Delete all chunks for a document."""
        return await self._store.delete_by_document(
            kb_id=kb_id, user_id=user_id, document_id=document_id
        )

    async def delete_kb(self, *, kb_id: str, user_id: str) -> None:
        """Delete all data for a knowledge base."""
        await self._store.delete_kb(kb_id=kb_id, user_id=user_id)

    # ------------------------------------------------------------------
    # Chunk management
    # ------------------------------------------------------------------

    async def get_chunks_by_document(
        self,
        *,
        kb_id: str,
        user_id: str,
        document_id: str,
        page: int = 1,
        size: int = 20,
        query: str = "",
    ) -> tuple[list[dict[str, Any]], int]:
        """Return paginated chunks for a document.

        Converts page/size to offset/limit and delegates to the store.

        Args:
            kb_id: Knowledge base ID.
            user_id: User ID.
            document_id: Document ID.
            page: 1-based page number.
            size: Items per page.
            query: Optional text filter (case-insensitive substring match).

        Returns:
            Tuple of (list of chunk dicts, total_count).
        """
        offset = (page - 1) * size
        return await self._store.get_chunks_by_document(
            kb_id=kb_id, user_id=user_id, document_id=document_id,
            offset=offset, limit=size, query=query,
        )

    async def get_chunk(
        self, *, kb_id: str, user_id: str, chunk_id: str
    ) -> dict[str, Any] | None:
        """Return a single chunk or None."""
        return await self._store.get_chunk(
            kb_id=kb_id, user_id=user_id, chunk_id=chunk_id,
        )

    async def update_chunk_text(
        self,
        *,
        kb_id: str,
        user_id: str,
        chunk_id: str,
        new_text: str,
    ) -> bool:
        """Update a chunk's text, re-embed, and update content hash.

        Args:
            kb_id: Knowledge base ID.
            user_id: User ID.
            chunk_id: Chunk ID to update.
            new_text: New chunk text content.

        Returns:
            True if the chunk was found and updated, False otherwise.
        """
        # Compute new content hash
        content_hash = hashlib.sha256(new_text.encode("utf-8")).hexdigest()

        # Re-embed the new text
        vectors = await self._embedding.embed_texts([new_text])
        new_vector = vectors[0]

        return await self._store.update_chunk(
            kb_id=kb_id, user_id=user_id, chunk_id=chunk_id,
            new_text=new_text, new_vector=new_vector,
            new_content_hash=content_hash,
        )

    async def delete_chunk(
        self, *, kb_id: str, user_id: str, chunk_id: str
    ) -> bool:
        """Delete a single chunk by ID.

        Returns:
            True if the chunk was found and deleted, False otherwise.
        """
        return await self._store.delete_chunk(
            kb_id=kb_id, user_id=user_id, chunk_id=chunk_id,
        )

"""LanceDB vector store with native FTS support."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import uuid
from pathlib import Path
from typing import Any

import lancedb  # type: ignore[import-untyped]
import pyarrow as pa  # type: ignore[import-untyped]

from fim_one.rag.base import Document

logger = logging.getLogger(__name__)

_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _validate_id(value: str, name: str = "id") -> str:
    """Validate that an ID contains only safe characters to prevent filter injection."""
    if not _SAFE_ID_RE.match(value):
        raise ValueError(f"Invalid {name}: contains unsafe characters")
    return value

_TABLE_NAME = "chunks"


def _list_table_names(db: lancedb.DBConnection) -> list[str]:
    """Return table name strings, compatible with newer LanceDB API."""
    resp = db.list_tables()
    # Newer LanceDB returns ListTablesResponse with .tables attribute
    if hasattr(resp, "tables"):
        return list(resp.tables)
    return list(resp)


def _content_hash(text: str) -> str:
    """Compute SHA256 hash for content dedup."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class LanceDBVectorStore:
    """Vector store backed by LanceDB with native FTS.

    Data isolation: ``{base_dir}/user_{uid}/kb_{kid}/`` paths.
    Content dedup via SHA-256 hash check before insert.

    Three-tier index strategy:
      - ``< 50,000 rows``  -- no index (brute force is fast enough).
      - ``50,000 - 10,000,000 rows`` -- default vector index (HNSW or
        IVF_PQ depending on LanceDB version).
      - ``>= 10,000,000 rows`` -- explicit IVF_PQ with tuned partitions
        and sub-vectors for memory efficiency at large scale.

    Smart reindex trigger: tracks cumulative inserts per table and
    rebuilds the index when the unindexed ratio exceeds a threshold
    (default: every 10,000 cumulative inserts).

    Args:
        base_dir: Root directory for vector data.
        embedding_dim: Dimension of embedding vectors.
    """

    _REINDEX_BATCH_THRESHOLD = 10_000  # Reindex after this many cumulative inserts

    def __init__(self, base_dir: str | Path, embedding_dim: int = 1024) -> None:
        self._base_dir = Path(base_dir)
        self._embedding_dim = embedding_dim
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._insert_counts: dict[str, int] = {}

    def _db_path(self, user_id: str, kb_id: str) -> Path:
        """Get the database path for a specific user + KB."""
        return self._base_dir / f"user_{user_id}" / f"kb_{kb_id}"

    def _get_db(self, user_id: str, kb_id: str) -> lancedb.DBConnection:
        """Open or create a LanceDB database."""
        db_path = self._db_path(user_id, kb_id)
        db_path.mkdir(parents=True, exist_ok=True)
        return lancedb.connect(str(db_path))

    def _expected_schema(self) -> pa.Schema:
        """Return the canonical table schema."""
        return pa.schema([
            pa.field("id", pa.string()),
            pa.field("text", pa.string()),
            pa.field("content_hash", pa.string()),
            pa.field("document_id", pa.string()),
            pa.field("kb_id", pa.string()),
            pa.field("user_id", pa.string()),
            pa.field("chunk_index", pa.int32()),
            pa.field("metadata_json", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), self._embedding_dim)),
        ])

    def _get_or_create_table(
        self, db: lancedb.DBConnection
    ) -> lancedb.table.Table:
        """Get existing table or create a new one.

        If an existing table is missing columns that were added in newer
        schema versions (e.g. ``chunk_index``), they are automatically
        back-filled with null values so that subsequent inserts succeed.
        """
        schema = self._expected_schema()
        if _TABLE_NAME in _list_table_names(db):
            table = db.open_table(_TABLE_NAME)
            self._migrate_schema(table, schema)
            return table
        return db.create_table(_TABLE_NAME, schema=schema)

    def _migrate_schema(
        self, table: lancedb.table.Table, expected: pa.Schema
    ) -> None:
        """Add any columns present in *expected* but missing from *table*."""
        existing_names = {f.name for f in table.schema}
        missing_fields = [
            f for f in expected
            if f.name not in existing_names and f.name != "vector"
        ]
        if not missing_fields:
            return
        try:
            table.add_columns(missing_fields)
            names = [f.name for f in missing_fields]
            logger.info("Migrated table schema: added columns %s", names)
        except Exception:
            logger.warning(
                "Schema migration failed for missing columns",
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Add
    # ------------------------------------------------------------------

    async def add_documents(
        self,
        texts: list[str],
        vectors: list[list[float]],
        metadatas: list[dict[str, Any]],
        *,
        kb_id: str,
        user_id: str,
        document_id: str,
    ) -> int:
        """Add document chunks to the store.

        Args:
            texts: List of chunk texts.
            vectors: List of embedding vectors.
            metadatas: List of metadata dicts.
            kb_id: Knowledge base ID.
            user_id: User ID for data isolation.
            document_id: Source document ID.

        Returns:
            Number of new chunks added (deduped).
        """
        return await asyncio.to_thread(
            self._add_documents_sync,
            texts, vectors, metadatas,
            kb_id=kb_id, user_id=user_id, document_id=document_id,
        )

    def _add_documents_sync(
        self,
        texts: list[str],
        vectors: list[list[float]],
        metadatas: list[dict[str, Any]],
        *,
        kb_id: str,
        user_id: str,
        document_id: str,
    ) -> int:
        import json

        db = self._get_db(user_id, kb_id)
        table = self._get_or_create_table(db)

        # Get existing hashes for dedup
        existing_hashes: set[str] = set()
        try:
            existing = (
                table.search()
                .where(f"document_id = '{_validate_id(document_id, 'document_id')}'")
                .select(["content_hash"])
                .limit(100_000)
                .to_list()
            )
            existing_hashes = {row["content_hash"] for row in existing}
        except Exception:
            pass  # Table might be empty

        # Build records, skipping duplicates
        records: list[dict[str, Any]] = []
        for text, vector, meta in zip(texts, vectors, metadatas):
            ch = _content_hash(text)
            if ch in existing_hashes:
                continue
            existing_hashes.add(ch)
            records.append({
                "id": str(uuid.uuid4()),
                "text": text,
                "content_hash": ch,
                "document_id": document_id,
                "kb_id": kb_id,
                "user_id": user_id,
                "chunk_index": meta.get("chunk_index", 0),
                "metadata_json": json.dumps(meta, ensure_ascii=False),
                "vector": vector,
            })

        if records:
            table.add(records)
            self._maybe_reindex(table, len(records), user_id, kb_id)

        return len(records)

    def _maybe_reindex(
        self,
        table: lancedb.table.Table,
        num_added: int,
        user_id: str,
        kb_id: str,
    ) -> None:
        """Create or rebuild vector index using tiered strategy + smart triggers.

        Tier decision:
          - ``< 50,000 rows`` -- skip (brute force).
          - ``50,000 - 10,000,000 rows`` -- default vector index.
          - ``>= 10,000,000 rows`` -- IVF_PQ with tuned parameters.

        Reindex is triggered when cumulative inserts since the last index
        build reach ``_REINDEX_BATCH_THRESHOLD``, or on the very first
        qualifying insert batch (i.e. the table just crossed 50k rows).
        """
        try:
            count = table.count_rows()
            if count < 50_000:
                return  # Brute force is fine

            # Track cumulative inserts per table
            table_key = f"{user_id}/{kb_id}"
            self._insert_counts[table_key] = (
                self._insert_counts.get(table_key, 0) + num_added
            )

            # Decide whether we should (re)index now
            cumulative = self._insert_counts[table_key]
            # First time the table qualifies OR threshold reached
            needs_index = (
                cumulative >= self._REINDEX_BATCH_THRESHOLD
                or cumulative == num_added
            )

            if not needs_index:
                return

            # Tier decision
            if count >= 10_000_000:
                # Large scale: explicit IVF_PQ with tuned partitions
                table.create_index(
                    metric="cosine",
                    num_partitions=min(count // 5000, 256),
                    num_sub_vectors=min(self._embedding_dim // 16, 96),
                    replace=True,
                )
                logger.info("Created IVF_PQ index for %d rows", count)
            else:
                # Medium scale: default index (HNSW or IVF_PQ per LanceDB version)
                table.create_index(metric="cosine", replace=True)
                logger.info("Created vector index for %d rows", count)

            # Reset counter after successful index build
            self._insert_counts[table_key] = 0
        except Exception:
            logger.debug("Index creation skipped or failed", exc_info=True)

    # ------------------------------------------------------------------
    # Vector search
    # ------------------------------------------------------------------

    async def vector_search(
        self,
        query_vector: list[float],
        *,
        kb_id: str,
        user_id: str,
        top_k: int = 20,
    ) -> list[Document]:
        """Search by vector similarity.

        Args:
            query_vector: The query embedding vector.
            kb_id: Knowledge base ID.
            user_id: User ID.
            top_k: Number of results.

        Returns:
            List of Document objects with scores.
        """
        return await asyncio.to_thread(
            self._vector_search_sync,
            query_vector, kb_id=kb_id, user_id=user_id, top_k=top_k,
        )

    def _vector_search_sync(
        self,
        query_vector: list[float],
        *,
        kb_id: str,
        user_id: str,
        top_k: int = 20,
    ) -> list[Document]:
        import json

        db = self._get_db(user_id, kb_id)
        if _TABLE_NAME not in _list_table_names(db):
            return []

        table = db.open_table(_TABLE_NAME)
        try:
            results = (
                table.search(query_vector)
                .where(f"user_id = '{_validate_id(user_id, 'user_id')}' AND kb_id = '{_validate_id(kb_id, 'kb_id')}'")
                .limit(top_k)
                .to_list()
            )
        except Exception:
            logger.warning("Vector search failed, returning empty", exc_info=True)
            return []

        docs: list[Document] = []
        for row in results:
            meta = json.loads(row.get("metadata_json", "{}"))
            meta["document_id"] = row.get("document_id", "")
            meta["chunk_id"] = row.get("id", "")
            # LanceDB returns _distance (lower is better), convert to similarity
            distance = row.get("_distance", 0.0)
            score = 1.0 / (1.0 + distance)
            docs.append(Document(content=row["text"], metadata=meta, score=score))
        return docs

    # ------------------------------------------------------------------
    # Full-text search
    # ------------------------------------------------------------------

    async def fts_search(
        self,
        query_text: str,
        *,
        kb_id: str,
        user_id: str,
        top_k: int = 20,
    ) -> list[Document]:
        """Full-text search using LanceDB native FTS.

        Args:
            query_text: The search query text.
            kb_id: Knowledge base ID.
            user_id: User ID.
            top_k: Number of results.

        Returns:
            List of Document objects with normalized scores.
        """
        return await asyncio.to_thread(
            self._fts_search_sync,
            query_text, kb_id=kb_id, user_id=user_id, top_k=top_k,
        )

    def _fts_search_sync(
        self,
        query_text: str,
        *,
        kb_id: str,
        user_id: str,
        top_k: int = 20,
    ) -> list[Document]:
        import json

        db = self._get_db(user_id, kb_id)
        if _TABLE_NAME not in _list_table_names(db):
            return []

        table = db.open_table(_TABLE_NAME)

        # Ensure FTS index exists
        try:
            table.create_fts_index("text", replace=False)
        except Exception:
            pass  # Index may already exist

        try:
            results = (
                table.search(query_text, query_type="fts")
                .where(f"user_id = '{_validate_id(user_id, 'user_id')}' AND kb_id = '{_validate_id(kb_id, 'kb_id')}'")
                .limit(top_k)
                .to_list()
            )
        except Exception:
            logger.warning("FTS search failed, returning empty", exc_info=True)
            return []

        docs: list[Document] = []
        for row in results:
            meta = json.loads(row.get("metadata_json", "{}"))
            meta["document_id"] = row.get("document_id", "")
            meta["chunk_id"] = row.get("id", "")
            # Normalize FTS score to [0, 1)
            raw_score = row.get("_score", 0.0)
            score = raw_score / (1.0 + raw_score) if raw_score > 0 else 0.0
            docs.append(Document(content=row["text"], metadata=meta, score=score))
        return docs

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete_by_document(
        self,
        *,
        kb_id: str,
        user_id: str,
        document_id: str,
    ) -> int:
        """Delete all chunks for a specific document.

        Returns:
            Number of chunks deleted.
        """
        return await asyncio.to_thread(
            self._delete_by_document_sync,
            kb_id=kb_id, user_id=user_id, document_id=document_id,
        )

    def _delete_by_document_sync(
        self, *, kb_id: str, user_id: str, document_id: str
    ) -> int:
        db = self._get_db(user_id, kb_id)
        if _TABLE_NAME not in _list_table_names(db):
            return 0

        table = db.open_table(_TABLE_NAME)
        try:
            before = table.count_rows()
            table.delete(f"document_id = '{document_id}'")
            after = table.count_rows()
            return int(before - after)
        except Exception:
            logger.warning("Delete by document failed", exc_info=True)
            return 0

    async def delete_kb(self, *, kb_id: str, user_id: str) -> None:
        """Delete all data for a knowledge base."""
        await asyncio.to_thread(
            self._delete_kb_sync, kb_id=kb_id, user_id=user_id
        )

    def _delete_kb_sync(self, *, kb_id: str, user_id: str) -> None:
        import shutil

        db_path = self._db_path(user_id, kb_id)
        if db_path.exists():
            shutil.rmtree(db_path, ignore_errors=True)

    # ------------------------------------------------------------------
    # Count
    # ------------------------------------------------------------------

    async def count(self, *, kb_id: str, user_id: str) -> int:
        """Count total chunks in a knowledge base."""
        return await asyncio.to_thread(
            self._count_sync, kb_id=kb_id, user_id=user_id
        )

    def _count_sync(self, *, kb_id: str, user_id: str) -> int:
        db = self._get_db(user_id, kb_id)
        if _TABLE_NAME not in _list_table_names(db):
            return 0
        table = db.open_table(_TABLE_NAME)
        return int(table.count_rows())

    # ------------------------------------------------------------------
    # Chunk CRUD
    # ------------------------------------------------------------------

    async def get_chunks_by_document(
        self,
        *,
        kb_id: str,
        user_id: str,
        document_id: str,
        offset: int = 0,
        limit: int = 20,
        query: str = "",
    ) -> tuple[list[dict[str, Any]], int]:
        """Return paginated chunks for a document, sorted by chunk_index.

        Args:
            kb_id: Knowledge base ID.
            user_id: User ID for data isolation.
            document_id: Document ID to filter by.
            offset: Number of chunks to skip.
            limit: Max number of chunks to return.
            query: Optional text filter (case-insensitive substring match).

        Returns:
            Tuple of (list of chunk dicts, total_count).
        """
        return await asyncio.to_thread(
            self._get_chunks_by_document_sync,
            kb_id=kb_id, user_id=user_id, document_id=document_id,
            offset=offset, limit=limit, query=query,
        )

    def _get_chunks_by_document_sync(
        self,
        *,
        kb_id: str,
        user_id: str,
        document_id: str,
        offset: int = 0,
        limit: int = 20,
        query: str = "",
    ) -> tuple[list[dict[str, Any]], int]:
        import json

        db = self._get_db(user_id, kb_id)
        if _TABLE_NAME not in _list_table_names(db):
            return [], 0

        table = db.open_table(_TABLE_NAME)
        where_clause = f"document_id = '{document_id}'"

        # When query is non-empty we must filter in Python, so skip count_rows shortcut
        if not query:
            # Optimised path: count without loading data
            try:
                total_count = table.count_rows(where_clause)
            except Exception:
                logger.warning("count_rows failed for document chunks", exc_info=True)
                return [], 0

            if total_count == 0:
                return [], 0

            fetch_limit = total_count
        else:
            # Need all rows for text filtering; use a generous upper bound
            fetch_limit = 1_000_000

        # Fetch rows. chunk_index lives inside metadata_json,
        # so we must load all matching rows, parse index, sort, then slice.
        try:
            all_rows = (
                table.search()
                .where(where_clause)
                .select(["id", "text", "content_hash", "metadata_json"])
                .limit(fetch_limit)
                .to_list()
            )
        except Exception:
            logger.warning("get_chunks_by_document failed", exc_info=True)
            return [], 0

        # Parse chunk_index for sorting, optionally filter by query text
        query_lower = query.lower() if query else ""
        indexed: list[tuple[int, dict[str, Any]]] = []
        for row in all_rows:
            # Filter by text content when query is provided
            if query_lower and query_lower not in row.get("text", "").lower():
                continue

            raw_meta = row.get("metadata_json", "{}")
            # Fast extraction: only parse chunk_index for sorting
            try:
                meta = json.loads(raw_meta)
                idx = meta.get("chunk_index", 0)
            except Exception:
                idx = 0
            indexed.append((idx, row))

        # When filtering, total_count comes from filtered list
        if query:
            total_count = len(indexed)

        indexed.sort(key=lambda t: t[0])
        page_slice = indexed[offset : offset + limit]

        parsed: list[dict[str, Any]] = []
        for idx, row in page_slice:
            meta = json.loads(row.get("metadata_json", "{}"))
            parsed.append({
                "id": row["id"],
                "text": row["text"],
                "chunk_index": idx,
                "metadata": meta,
                "content_hash": row.get("content_hash", ""),
            })

        return parsed, total_count

    async def get_chunk(
        self, *, kb_id: str, user_id: str, chunk_id: str
    ) -> dict[str, Any] | None:
        """Return a single chunk dict or None.

        Args:
            kb_id: Knowledge base ID.
            user_id: User ID for data isolation.
            chunk_id: Chunk ID.

        Returns:
            Chunk dict with id, text, chunk_index, metadata, content_hash, or None.
        """
        return await asyncio.to_thread(
            self._get_chunk_sync,
            kb_id=kb_id, user_id=user_id, chunk_id=chunk_id,
        )

    def _get_chunk_sync(
        self, *, kb_id: str, user_id: str, chunk_id: str
    ) -> dict[str, Any] | None:
        import json

        db = self._get_db(user_id, kb_id)
        if _TABLE_NAME not in _list_table_names(db):
            return None

        table = db.open_table(_TABLE_NAME)
        try:
            rows = (
                table.search()
                .where(f"id = '{_validate_id(chunk_id, 'chunk_id')}'")
                .select(["id", "text", "content_hash", "metadata_json", "document_id"])
                .limit(1)
                .to_list()
            )
        except Exception:
            logger.warning("get_chunk failed for %s", chunk_id, exc_info=True)
            return None

        if not rows:
            return None

        row = rows[0]
        meta = json.loads(row.get("metadata_json", "{}"))
        return {
            "id": row["id"],
            "text": row["text"],
            "chunk_index": meta.get("chunk_index", 0),
            "metadata": meta,
            "content_hash": row.get("content_hash", ""),
            "document_id": row.get("document_id", ""),
        }

    async def update_chunk(
        self,
        *,
        kb_id: str,
        user_id: str,
        chunk_id: str,
        new_text: str,
        new_vector: list[float],
        new_content_hash: str,
    ) -> bool:
        """In-place update a chunk's text, vector, and content hash.

        Args:
            kb_id: Knowledge base ID.
            user_id: User ID for data isolation.
            chunk_id: Chunk ID to update.
            new_text: New chunk text.
            new_vector: New embedding vector.
            new_content_hash: New SHA256 content hash.

        Returns:
            True if the chunk was found and updated, False otherwise.
        """
        return await asyncio.to_thread(
            self._update_chunk_sync,
            kb_id=kb_id, user_id=user_id, chunk_id=chunk_id,
            new_text=new_text, new_vector=new_vector,
            new_content_hash=new_content_hash,
        )

    def _update_chunk_sync(
        self,
        *,
        kb_id: str,
        user_id: str,
        chunk_id: str,
        new_text: str,
        new_vector: list[float],
        new_content_hash: str,
    ) -> bool:
        db = self._get_db(user_id, kb_id)
        if _TABLE_NAME not in _list_table_names(db):
            return False

        table = db.open_table(_TABLE_NAME)
        try:
            # Verify chunk exists first
            rows = (
                table.search()
                .where(f"id = '{_validate_id(chunk_id, 'chunk_id')}'")
                .select(["id"])
                .limit(1)
                .to_list()
            )
            if not rows:
                return False

            table.update(
                where=f"id = '{chunk_id}'",
                values={
                    "text": new_text,
                    "vector": new_vector,
                    "content_hash": new_content_hash,
                },
            )

            # Rebuild FTS index after text update
            try:
                table.create_fts_index("text", replace=True)
            except Exception:
                logger.debug("FTS index rebuild skipped after chunk update", exc_info=True)

            return True
        except Exception:
            logger.warning("update_chunk failed for %s", chunk_id, exc_info=True)
            return False

    async def delete_chunk(
        self, *, kb_id: str, user_id: str, chunk_id: str
    ) -> bool:
        """Delete a single chunk by ID.

        Args:
            kb_id: Knowledge base ID.
            user_id: User ID for data isolation.
            chunk_id: Chunk ID to delete.

        Returns:
            True if the chunk was found and deleted, False otherwise.
        """
        return await asyncio.to_thread(
            self._delete_chunk_sync,
            kb_id=kb_id, user_id=user_id, chunk_id=chunk_id,
        )

    def _delete_chunk_sync(
        self, *, kb_id: str, user_id: str, chunk_id: str
    ) -> bool:
        db = self._get_db(user_id, kb_id)
        if _TABLE_NAME not in _list_table_names(db):
            return False

        table = db.open_table(_TABLE_NAME)
        try:
            before = table.count_rows()
            table.delete(f"id = '{chunk_id}'")
            after = table.count_rows()
            return bool(before > after)
        except Exception:
            logger.warning("delete_chunk failed for %s", chunk_id, exc_info=True)
            return False

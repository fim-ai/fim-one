"""Hybrid retriever: Dense + Sparse → RRF / Linear fusion → optional Rerank."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from fim_agent.rag.base import BaseRetriever, Document

if TYPE_CHECKING:
    from fim_agent.core.reranker.base import BaseReranker

logger = logging.getLogger(__name__)

# Reciprocal Rank Fusion constant
_RRF_K = 60

_VALID_FUSION_MODES = ("rrf", "linear")


class HybridRetriever(BaseRetriever):
    """Combine dense and sparse retrieval with fusion and optional reranking.

    Pipeline:
        1. Parallel ``dense(top_k=coarse_k)`` + ``sparse(top_k=coarse_k)``
        2. Fusion (RRF or Linear) to merge the two ranked lists
        3. (optional) ``reranker.rerank(query, top_texts, top_k)`` for precision

    Degradation chain (borrowed from XAgent):
        - FTS failure -> dense-only + warning
        - Rerank failure -> return fused results directly

    Args:
        dense: Dense (vector) retriever.
        sparse: Sparse (FTS) retriever.
        reranker: Optional reranker for final precision pass.
        dense_weight: Weight for dense results (default 1.0).
        sparse_weight: Weight for sparse results (default 1.0).
        coarse_k: Number of candidates to retrieve before fusion.
        fusion_mode: ``"rrf"`` (Reciprocal Rank Fusion) or ``"linear"``
            (min-max normalised weighted sum). Default ``"rrf"``.
    """

    def __init__(
        self,
        dense: BaseRetriever,
        sparse: BaseRetriever,
        *,
        reranker: BaseReranker | None = None,
        dense_weight: float = 1.0,
        sparse_weight: float = 1.0,
        coarse_k: int = 20,
        fusion_mode: str = "rrf",
    ) -> None:
        if fusion_mode not in _VALID_FUSION_MODES:
            raise ValueError(
                f"fusion_mode must be one of {_VALID_FUSION_MODES}, got {fusion_mode!r}"
            )
        self._dense = dense
        self._sparse = sparse
        self._reranker = reranker
        self._dense_weight = dense_weight
        self._sparse_weight = sparse_weight
        self._coarse_k = coarse_k
        self._fusion_mode = fusion_mode

    async def retrieve(self, query: str, *, top_k: int = 5) -> list[Document]:
        # Step 1: parallel coarse retrieval
        dense_task = asyncio.create_task(
            self._dense.retrieve(query, top_k=self._coarse_k)
        )
        sparse_task = asyncio.create_task(
            self._safe_sparse(query, self._coarse_k)
        )

        dense_results, sparse_results = await asyncio.gather(
            dense_task, sparse_task
        )

        # Step 2: fusion
        if self._fusion_mode == "linear":
            fused = self._linear_fuse(dense_results, sparse_results)
        else:
            fused = self._rrf_fuse(dense_results, sparse_results)

        # Step 3: optional rerank
        if self._reranker and fused:
            try:
                texts = [doc.content for doc in fused[:self._coarse_k]]
                reranked = await self._reranker.rerank(query, texts, top_k=top_k)
                # Rebuild Document list preserving metadata and score tracing
                doc_map = {doc.content: doc for doc in fused}
                result: list[Document] = []
                for rr in reranked:
                    if rr.text in doc_map:
                        src = doc_map[rr.text]
                        result.append(
                            Document(
                                content=src.content,
                                metadata=src.metadata,
                                score=rr.score,
                                vector_score=src.vector_score,
                                fts_score=src.fts_score,
                                vector_rank=src.vector_rank,
                                fts_rank=src.fts_rank,
                            )
                        )
                return result[:top_k]
            except Exception:
                logger.warning(
                    "Reranker failed, falling back to fused results", exc_info=True
                )

        return fused[:top_k]

    async def _safe_sparse(self, query: str, top_k: int) -> list[Document]:
        """Run sparse retrieval with graceful degradation."""
        try:
            return await self._sparse.retrieve(query, top_k=top_k)
        except Exception:
            logger.warning("FTS search failed, using dense-only", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Fusion strategies
    # ------------------------------------------------------------------

    def _rrf_fuse(
        self,
        dense_results: list[Document],
        sparse_results: list[Document],
    ) -> list[Document]:
        """Merge two ranked lists using Reciprocal Rank Fusion."""
        scores: dict[str, float] = {}
        doc_map: dict[str, Document] = {}
        vector_scores: dict[str, float] = {}
        fts_scores: dict[str, float] = {}
        vector_ranks: dict[str, int] = {}
        fts_ranks: dict[str, int] = {}

        for rank, doc in enumerate(dense_results):
            key = doc.content
            scores[key] = scores.get(key, 0.0) + self._dense_weight / (_RRF_K + rank + 1)
            doc_map[key] = doc
            vector_scores[key] = doc.score if doc.score is not None else 0.0
            vector_ranks[key] = rank

        for rank, doc in enumerate(sparse_results):
            key = doc.content
            scores[key] = scores.get(key, 0.0) + self._sparse_weight / (_RRF_K + rank + 1)
            if key not in doc_map:
                doc_map[key] = doc
            fts_scores[key] = doc.score if doc.score is not None else 0.0
            fts_ranks[key] = rank

        # Sort by fused score descending
        sorted_keys = sorted(scores, key=lambda k: scores[k], reverse=True)
        return [
            Document(
                content=doc_map[k].content,
                metadata=doc_map[k].metadata,
                score=scores[k],
                vector_score=vector_scores.get(k),
                fts_score=fts_scores.get(k),
                vector_rank=vector_ranks.get(k),
                fts_rank=fts_ranks.get(k),
            )
            for k in sorted_keys
        ]

    def _linear_fuse(
        self,
        dense_results: list[Document],
        sparse_results: list[Document],
    ) -> list[Document]:
        """Merge two ranked lists using min-max normalised linear weighting."""
        doc_map: dict[str, Document] = {}
        raw_dense: dict[str, float] = {}
        raw_sparse: dict[str, float] = {}
        vector_ranks: dict[str, int] = {}
        fts_ranks: dict[str, int] = {}

        for rank, doc in enumerate(dense_results):
            key = doc.content
            raw_dense[key] = doc.score if doc.score is not None else 0.0
            doc_map[key] = doc
            vector_ranks[key] = rank

        for rank, doc in enumerate(sparse_results):
            key = doc.content
            raw_sparse[key] = doc.score if doc.score is not None else 0.0
            if key not in doc_map:
                doc_map[key] = doc
            fts_ranks[key] = rank

        # Min-max normalisation helper
        def _min_max_norm(values: dict[str, float]) -> dict[str, float]:
            if not values:
                return {}
            min_v = min(values.values())
            max_v = max(values.values())
            span = max_v - min_v
            if span == 0:
                # All scores identical -> normalise to 1.0
                return {k: 1.0 for k in values}
            return {k: (v - min_v) / span for k, v in values.items()}

        norm_dense = _min_max_norm(raw_dense)
        norm_sparse = _min_max_norm(raw_sparse)

        # Combine
        all_keys = set(raw_dense) | set(raw_sparse)
        scores: dict[str, float] = {}
        for k in all_keys:
            d = norm_dense.get(k, 0.0) * self._dense_weight
            s = norm_sparse.get(k, 0.0) * self._sparse_weight
            scores[k] = d + s

        sorted_keys = sorted(scores, key=lambda k: scores[k], reverse=True)
        return [
            Document(
                content=doc_map[k].content,
                metadata=doc_map[k].metadata,
                score=scores[k],
                vector_score=raw_dense.get(k),
                fts_score=raw_sparse.get(k),
                vector_rank=vector_ranks.get(k),
                fts_rank=fts_ranks.get(k),
            )
            for k in sorted_keys
        ]

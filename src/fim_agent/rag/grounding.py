"""Grounding Pipeline — evidence-anchored RAG with citation extraction."""

from __future__ import annotations

import asyncio
import logging
import math

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fim_agent.core.embedding.base import BaseEmbedding
from fim_agent.core.model.base import BaseLLM
from fim_agent.core.model.types import ChatMessage
from fim_agent.core.utils import extract_json
from fim_agent.rag.base import Document
from fim_agent.rag.manager import KnowledgeBaseManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Citation:
    """An exact verbatim quote from a chunk that supports a query."""

    text: str
    document_id: str
    kb_id: str
    chunk_id: str
    source_name: str
    page_number: int | None = None
    char_offset: int | None = None
    char_end: int | None = None
    relevance_score: float = 0.0


@dataclass
class EvidenceUnit:
    """A retrieved chunk with extracted citations."""

    chunk: Document
    citations: list[Citation] = field(default_factory=list)
    kb_id: str = ""
    rank: int = 0


@dataclass
class Conflict:
    """Two citations from different documents that may contradict each other."""

    claim_a: Citation
    claim_b: Citation
    similarity: float = 0.0


@dataclass
class GroundedResult:
    """Final output of the grounding pipeline."""

    evidence: list[EvidenceUnit] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    confidence: float = 0.0
    total_sources: int = 0
    kb_ids: list[str] = field(default_factory=list)
    query: str = ""


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG: dict[str, Any] = {
    "top_k": 10,
    "min_score": 0.3,
    "conflict_threshold": 0.85,
    "citation_mode": "grounding",
}

_CITATION_SYSTEM_PROMPT = (
    "Given a query and numbered document chunks, extract exact quotes from "
    "each chunk that answer the query.\n"
    'Return JSON object: {"0": [{"text": "exact quote", "char_offset": N}], "1": [...], ...}\n'
    "Rules: text MUST be a verbatim substring of its chunk. "
    "Extract 1-3 quotes per chunk max. Use [] if nothing relevant in a chunk."
)


class GroundingPipeline:
    """Evidence-anchored RAG pipeline.

    Retrieves chunks from multiple knowledge bases, extracts verbatim
    citations via a single batched LLM call, optionally detects cross-source
    conflicts (multi-KB only), and computes an overall confidence score.
    """

    def __init__(
        self,
        kb_manager: KnowledgeBaseManager,
        embedding: BaseEmbedding,
        llm: BaseLLM | None = None,
        config: dict[str, Any] | None = None,
        on_evidence: Callable[[EvidenceUnit], None] | None = None,
    ) -> None:
        self._kb_manager = kb_manager
        self._embedding = embedding
        self._llm = llm
        self._config = {**_DEFAULT_CONFIG, **(config or {})}
        self._on_evidence = on_evidence

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def ground(
        self, query: str, kb_ids: list[str], user_id: str
    ) -> GroundedResult:
        """Run the grounding pipeline.

        Stages:
          1. Multi-KB retrieval
          2. Citation extraction (single batched LLM call)
          3. Cross-source conflict detection (multi-KB only)
          4. Confidence scoring
        """
        # Stage 1 — multi-KB retrieval
        evidence = await self._multi_kb_retrieve(query, kb_ids, user_id)

        # Stage 2 — citation extraction (batched)
        await self._extract_citations(query, evidence)

        # Stage 3 — cross-source conflict detection (multi-KB only)
        conflicts: list[Conflict] = []
        if len(kb_ids) > 1:
            conflicts = await self._detect_conflicts(evidence)

        # Stage 4 — confidence scoring
        confidence = self._compute_confidence(evidence)

        return GroundedResult(
            evidence=evidence,
            conflicts=conflicts,
            confidence=confidence,
            total_sources=len(evidence),
            kb_ids=kb_ids,
            query=query,
        )

    # ------------------------------------------------------------------
    # Stage 1: Multi-KB Retrieve
    # ------------------------------------------------------------------

    async def _multi_kb_retrieve(
        self, query: str, kb_ids: list[str], user_id: str
    ) -> list[EvidenceUnit]:
        top_k = self._config["top_k"]
        min_score = self._config["min_score"]

        tasks = [
            self._kb_manager.retrieve(
                query, kb_id=kid, user_id=user_id, top_k=top_k
            )
            for kid in kb_ids
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        merged: list[tuple[Document, str]] = []
        for kid, result in zip(kb_ids, results):
            if isinstance(result, BaseException):
                logger.warning("Retrieval failed for kb=%s: %s", kid, result)
                continue
            for doc in result:
                if doc.score is not None and doc.score >= min_score:
                    merged.append((doc, kid))

        # Sort by score descending
        merged.sort(key=lambda pair: pair[0].score or 0.0, reverse=True)

        evidence: list[EvidenceUnit] = []
        for i, (doc, kid) in enumerate(merged):
            unit = EvidenceUnit(
                chunk=doc,
                kb_id=kid,
                rank=i,
            )
            evidence.append(unit)
            if self._on_evidence is not None:
                self._on_evidence(unit)

        logger.debug(
            "Stage 1 complete: %d evidence units from %d KBs",
            len(evidence),
            len(kb_ids),
        )
        return evidence

    # ------------------------------------------------------------------
    # Stage 2: Citation Extraction (batched)
    # ------------------------------------------------------------------

    async def _extract_citations(
        self, query: str, evidence: list[EvidenceUnit]
    ) -> None:
        if not evidence:
            return

        use_llm = (
            self._llm is not None
            and self._config["citation_mode"] == "grounding"
        )

        if use_llm:
            await self._batched_llm_extract(query, evidence)
        else:
            await asyncio.gather(*[
                self._fallback_extract_one(query, unit) for unit in evidence
            ])

    async def _batched_llm_extract(
        self, query: str, evidence: list[EvidenceUnit]
    ) -> None:
        """Extract citations from all chunks in a single LLM call."""
        assert self._llm is not None

        chunks_parts = []
        for i, unit in enumerate(evidence):
            chunks_parts.append(f"[Chunk {i}]\n{unit.chunk.content}")

        user_prompt = f"Query: {query}\n\n" + "\n\n".join(chunks_parts)

        result = await self._llm.chat([
            ChatMessage(role="system", content=_CITATION_SYSTEM_PROMPT),
            ChatMessage(role="user", content=user_prompt),
        ])

        raw = result.message.content or ""

        parsed = extract_json(raw)
        if parsed is None:
            logger.warning(
                "Failed to parse batched citation JSON: %s", raw[:200]
            )
            return

        for i, unit in enumerate(evidence):
            items = parsed.get(str(i), [])
            if not isinstance(items, list):
                continue

            content = unit.chunk.content
            chunk_id = unit.chunk.metadata.get(
                "chunk_id", f"{unit.kb_id}_{unit.rank}"
            )
            document_id = unit.chunk.metadata.get("document_id", "")
            source_name = unit.chunk.metadata.get(
                "source", unit.chunk.metadata.get("filename", "unknown")
            )
            page_number = unit.chunk.metadata.get("page_number")

            citations: list[Citation] = []
            for item in items:
                text = item.get("text", "")
                if not text or text not in content:
                    continue
                offset = item.get("char_offset")
                if offset is None:
                    offset = content.find(text)
                citations.append(
                    Citation(
                        text=text,
                        document_id=document_id,
                        kb_id=unit.kb_id,
                        chunk_id=chunk_id,
                        source_name=source_name,
                        page_number=page_number,
                        char_offset=offset,
                        char_end=offset + len(text) if offset is not None else None,
                    )
                )
            unit.citations = citations[:3]

    async def _fallback_extract_one(
        self, query: str, unit: EvidenceUnit
    ) -> None:
        """Fallback: sentence-level extraction for a single unit."""
        doc = unit.chunk
        unit.citations = await self._fallback_extract(
            query,
            doc.content,
            document_id=doc.metadata.get("document_id", ""),
            kb_id=unit.kb_id,
            chunk_id=doc.metadata.get("chunk_id", f"{unit.kb_id}_{unit.rank}"),
            source_name=doc.metadata.get(
                "source", doc.metadata.get("filename", "unknown")
            ),
            page_number=doc.metadata.get("page_number"),
        )

    async def _fallback_extract(
        self,
        query: str,
        content: str,
        *,
        document_id: str,
        kb_id: str,
        chunk_id: str,
        source_name: str,
        page_number: int | None,
    ) -> list[Citation]:
        """Sentence-level extraction using embedding similarity."""
        sentences = re.split(r"(?<=[.?!])\s+", content)
        sentences = [s.strip() for s in sentences if s.strip()]
        if not sentences:
            return []

        query_vec = await self._embedding.embed_query(query)
        sent_vecs = await self._embedding.embed_texts(sentences)

        scored = [
            (self._cosine_similarity(query_vec, sv), i)
            for i, sv in enumerate(sent_vecs)
        ]
        scored.sort(reverse=True)

        citations: list[Citation] = []
        for score, idx in scored[:3]:
            text = sentences[idx]
            offset = content.find(text)
            citations.append(
                Citation(
                    text=text,
                    document_id=document_id,
                    kb_id=kb_id,
                    chunk_id=chunk_id,
                    source_name=source_name,
                    page_number=page_number,
                    char_offset=offset if offset >= 0 else None,
                    char_end=(offset + len(text)) if offset >= 0 else None,
                    relevance_score=score,
                )
            )

        return citations

    # ------------------------------------------------------------------
    # Stage 3: Conflict Detection (multi-KB only)
    # ------------------------------------------------------------------

    async def _detect_conflicts(
        self, evidence: list[EvidenceUnit]
    ) -> list[Conflict]:
        threshold = self._config["conflict_threshold"]

        all_citations: list[Citation] = []
        for unit in evidence:
            all_citations.extend(unit.citations)

        if len(all_citations) < 2:
            return []

        # Build pairs from different documents
        pairs: list[tuple[Citation, Citation]] = []
        for i in range(len(all_citations)):
            for j in range(i + 1, len(all_citations)):
                if all_citations[i].document_id != all_citations[j].document_id:
                    pairs.append((all_citations[i], all_citations[j]))

        if not pairs:
            return []

        # Embed all unique citation texts
        unique_texts = list({c.text for c in all_citations})
        text_to_vec: dict[str, list[float]] = {}
        if unique_texts:
            vecs = await self._embedding.embed_texts(unique_texts)
            for t, v in zip(unique_texts, vecs):
                text_to_vec[t] = v

        conflicts: list[Conflict] = []
        for a, b in pairs:
            vec_a = text_to_vec.get(a.text)
            vec_b = text_to_vec.get(b.text)
            if vec_a is None or vec_b is None:
                continue
            sim = self._cosine_similarity(vec_a, vec_b)
            if sim >= threshold and self._text_overlap(a.text, b.text) < 0.9:
                conflicts.append(Conflict(claim_a=a, claim_b=b, similarity=sim))

        return conflicts

    # ------------------------------------------------------------------
    # Stage 4: Confidence Scoring
    # ------------------------------------------------------------------

    def _compute_confidence(self, evidence: list[EvidenceUnit]) -> float:
        if not evidence:
            return 0.0

        top3 = evidence[:3]
        avg_retrieval = sum(e.chunk.score or 0.0 for e in top3) / len(top3)
        coverage = min(len(evidence) / self._config["top_k"], 1.0)

        return (avg_retrieval * 0.75) + (coverage * 0.25)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    @staticmethod
    def _text_overlap(a: str, b: str) -> float:
        """Jaccard similarity on word level."""
        words_a = set(a.lower().split())
        words_b = set(b.lower().split())
        if not words_a and not words_b:
            return 1.0
        union = words_a | words_b
        if not union:
            return 0.0
        return len(words_a & words_b) / len(union)

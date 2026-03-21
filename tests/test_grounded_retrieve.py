"""Tests for the GroundedRetrieveTool."""

from __future__ import annotations

import asyncio
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.core.tool.builtin.grounded_retrieve import GroundedRetrieveTool


# ---------------------------------------------------------------------------
# Tests: Parameter schema
# ---------------------------------------------------------------------------


def test_tool_with_bound_kbs():
    """When kb_ids are bound, parameters_schema should NOT require kb_ids."""
    tool = GroundedRetrieveTool(kb_ids=["kb1", "kb2"])
    schema = tool.parameters_schema

    assert "kb_ids" not in schema["properties"]
    assert "kb_ids" not in schema["required"]
    assert "query" in schema["required"]
    assert tool.name == "grounded_retrieve"
    assert tool.category == "knowledge"


def test_tool_without_bound_kbs():
    """When no kb_ids are bound, parameters_schema SHOULD require kb_ids."""
    tool = GroundedRetrieveTool()
    schema = tool.parameters_schema

    assert "kb_ids" in schema["properties"]
    assert "kb_ids" in schema["required"]
    assert "query" in schema["required"]


# ---------------------------------------------------------------------------
# Tests: Tool output format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_output_format():
    """Mock the grounding pipeline and verify output format."""
    from fim_one.rag.base import Document
    from fim_one.rag.grounding import Citation, EvidenceUnit, GroundedResult

    mock_result = GroundedResult(
        evidence=[
            EvidenceUnit(
                chunk=Document(
                    content="Test content about AI.",
                    metadata={"source": "ai_paper.pdf"},
                    score=0.85,
                ),
                citations=[
                    Citation(
                        text="AI is transforming industries",
                        document_id="d1",
                        kb_id="kb1",
                        chunk_id="c1",
                        source_name="ai_paper.pdf",
                    )
                ],
                kb_id="kb1",
                rank=0,
            ),
        ],
        confidence=0.75,
        total_sources=1,
        kb_ids=["kb1"],
        query="What is AI?",
    )

    mock_pipeline_cls = MagicMock()
    mock_pipeline_instance = AsyncMock()
    mock_pipeline_instance.ground = AsyncMock(return_value=mock_result)
    mock_pipeline_cls.return_value = mock_pipeline_instance

    tool = GroundedRetrieveTool(kb_ids=["kb1"])

    with (
        patch(
            "fim_one.web.deps.get_kb_manager",
        ),
        patch(
            "fim_one.web.deps.get_embedding",
        ),
        patch(
            "fim_one.web.deps.get_fast_llm",
        ),
        patch(
            "fim_one.rag.grounding.GroundingPipeline",
            mock_pipeline_cls,
        ),
    ):
        output = await tool.run(query="What is AI?")

    assert "**Evidence**" in output
    assert "confidence: 75%" in output
    assert "1 sources" in output
    assert "[1]" in output
    assert "ai_paper.pdf" in output
    assert "AI is transforming industries" in output
    assert "alignment" not in output


@pytest.mark.asyncio
async def test_tool_missing_query():
    tool = GroundedRetrieveTool(kb_ids=["kb1"])
    result = await tool.run(query="")
    assert "[Error]" in result


@pytest.mark.asyncio
async def test_tool_missing_kb_ids():
    tool = GroundedRetrieveTool()  # No bound kb_ids
    result = await tool.run(query="test")
    assert "[Error]" in result
    assert "kb_ids" in result


# ---------------------------------------------------------------------------
# Helpers for cumulative numbering tests
# ---------------------------------------------------------------------------


def _make_mock_result(num_evidence: int, kb_id: str = "kb1"):
    """Build a GroundedResult with *num_evidence* evidence units."""
    from fim_one.rag.base import Document
    from fim_one.rag.grounding import Citation, EvidenceUnit, GroundedResult

    evidence = []
    for idx in range(num_evidence):
        evidence.append(
            EvidenceUnit(
                chunk=Document(
                    content=f"Content for chunk {idx}.",
                    metadata={"source": f"doc_{idx}.pdf"},
                    score=0.9 - idx * 0.05,
                ),
                citations=[
                    Citation(
                        text=f"Citation text {idx}",
                        document_id=f"d{idx}",
                        kb_id=kb_id,
                        chunk_id=f"c{idx}",
                        source_name=f"doc_{idx}.pdf",
                    )
                ],
                kb_id=kb_id,
                rank=idx,
            )
        )
    return GroundedResult(
        evidence=evidence,
        confidence=0.80,
        total_sources=num_evidence,
        kb_ids=[kb_id],
        query="test query",
    )


def _patch_pipeline(mock_result):
    """Return context managers that patch deps + pipeline to return *mock_result*."""
    mock_pipeline_cls = MagicMock()
    mock_pipeline_instance = AsyncMock()
    mock_pipeline_instance.ground = AsyncMock(return_value=mock_result)
    mock_pipeline_cls.return_value = mock_pipeline_instance
    return (
        patch("fim_one.web.deps.get_kb_manager"),
        patch("fim_one.web.deps.get_embedding"),
        patch("fim_one.web.deps.get_fast_llm"),
        patch("fim_one.rag.grounding.GroundingPipeline", mock_pipeline_cls),
    )


def _extract_source_numbers(output: str) -> list[int]:
    """Extract all ``[N]`` source numbers from formatted output."""
    return [int(m) for m in re.findall(r"\[(\d+)\] Source:", output)]


# ---------------------------------------------------------------------------
# Tests: Cumulative source numbering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cumulative_numbering_sequential():
    """Two sequential run() calls on the same tool instance.

    First call returns 3 sources numbered [1]-[3].
    Second call returns 3 sources numbered [4]-[6].
    """
    tool = GroundedRetrieveTool(kb_ids=["kb1"])

    # --- First call: 3 evidence units ---
    result1 = _make_mock_result(3)
    with (
        _patch_pipeline(result1)[0],
        _patch_pipeline(result1)[1],
        _patch_pipeline(result1)[2],
        _patch_pipeline(result1)[3],
    ):
        output1 = await tool.run(query="first query", top_k=5)

    nums1 = _extract_source_numbers(output1)
    assert nums1 == [1, 2, 3], f"Expected [1,2,3], got {nums1}"

    # --- Second call: 3 evidence units ---
    result2 = _make_mock_result(3)
    with (
        _patch_pipeline(result2)[0],
        _patch_pipeline(result2)[1],
        _patch_pipeline(result2)[2],
        _patch_pipeline(result2)[3],
    ):
        output2 = await tool.run(query="second query", top_k=5)

    nums2 = _extract_source_numbers(output2)
    assert nums2 == [4, 5, 6], f"Expected [4,5,6], got {nums2}"

    # Verify offset is correct
    assert tool._source_offset == 6


@pytest.mark.asyncio
async def test_cumulative_numbering_error_no_increment():
    """An error call should NOT advance the offset."""
    tool = GroundedRetrieveTool(kb_ids=["kb1"])

    # --- First call succeeds with 2 sources ---
    result1 = _make_mock_result(2)
    patches1 = _patch_pipeline(result1)
    with patches1[0], patches1[1], patches1[2], patches1[3]:
        output1 = await tool.run(query="ok query", top_k=5)

    assert _extract_source_numbers(output1) == [1, 2]
    assert tool._source_offset == 2

    # --- Second call: pipeline raises an exception ---
    mock_pipeline_cls = MagicMock()
    mock_pipeline_instance = AsyncMock()
    mock_pipeline_instance.ground = AsyncMock(side_effect=RuntimeError("boom"))
    mock_pipeline_cls.return_value = mock_pipeline_instance

    with (
        patch("fim_one.web.deps.get_kb_manager"),
        patch("fim_one.web.deps.get_embedding"),
        patch("fim_one.web.deps.get_fast_llm"),
        patch("fim_one.rag.grounding.GroundingPipeline", mock_pipeline_cls),
    ):
        output_err = await tool.run(query="bad query", top_k=5)

    assert "[Error]" in output_err
    # Offset should NOT have advanced
    assert tool._source_offset == 2

    # --- Third call succeeds with 2 sources, should still start at [3] ---
    result3 = _make_mock_result(2)
    patches3 = _patch_pipeline(result3)
    with patches3[0], patches3[1], patches3[2], patches3[3]:
        output3 = await tool.run(query="recovery query", top_k=5)

    assert _extract_source_numbers(output3) == [3, 4]
    assert tool._source_offset == 4


@pytest.mark.asyncio
async def test_cumulative_numbering_below_threshold_no_increment():
    """Below-threshold result should NOT advance the offset."""
    tool = GroundedRetrieveTool(kb_ids=["kb1"], confidence_threshold=0.9)

    # Result with confidence=0.80 is below threshold of 0.90
    result = _make_mock_result(3)  # confidence=0.80
    patches = _patch_pipeline(result)
    with patches[0], patches[1], patches[2], patches[3]:
        output = await tool.run(query="low confidence query", top_k=5)

    assert "[Evidence insufficient]" in output
    assert tool._source_offset == 0  # should not have advanced


@pytest.mark.asyncio
async def test_parallel_safety_non_overlapping():
    """Two concurrent run() calls on the same tool via asyncio.gather().

    Both should get non-overlapping, dense (no gaps) number ranges.
    The asyncio.Lock ensures offsets are assigned atomically after each
    pipeline.ground() returns, so we never pre-reserve more slots than
    needed.
    """
    tool = GroundedRetrieveTool(kb_ids=["kb1"])

    # Both calls return 3 evidence units each, with top_k=5
    result_a = _make_mock_result(3)
    result_b = _make_mock_result(3)

    call_count = 0

    async def mock_ground(query, kb_ids, user_id, **kwargs):
        nonlocal call_count
        call_count += 1
        # Simulate some async delay to interleave
        await asyncio.sleep(0.01)
        if call_count <= 1:
            return result_a
        return result_b

    mock_pipeline_cls = MagicMock()
    mock_pipeline_instance = AsyncMock()
    mock_pipeline_instance.ground = mock_ground
    mock_pipeline_cls.return_value = mock_pipeline_instance

    with (
        patch("fim_one.web.deps.get_kb_manager"),
        patch("fim_one.web.deps.get_embedding"),
        patch("fim_one.web.deps.get_fast_llm"),
        patch("fim_one.rag.grounding.GroundingPipeline", mock_pipeline_cls),
    ):
        output_a, output_b = await asyncio.gather(
            tool.run(query="query A", top_k=5),
            tool.run(query="query B", top_k=5),
        )

    nums_a = _extract_source_numbers(output_a)
    nums_b = _extract_source_numbers(output_b)

    # Both should have 3 numbers each
    assert len(nums_a) == 3, f"Expected 3 numbers, got {nums_a}"
    assert len(nums_b) == 3, f"Expected 3 numbers, got {nums_b}"

    # Ranges should not overlap
    set_a = set(nums_a)
    set_b = set(nums_b)
    assert set_a.isdisjoint(set_b), f"Source numbers overlap: A={nums_a}, B={nums_b}"

    # Combined numbers must be dense (no gaps): exactly {1,2,3,4,5,6}
    all_nums = sorted(nums_a + nums_b)
    assert all_nums == list(
        range(1, 7)
    ), f"Citation numbers have gaps — expected [1..6], got {all_nums}"

    # Final offset should equal total evidence count (no over-reservation)
    assert (
        tool._source_offset == 6
    ), f"Expected final offset 6, got {tool._source_offset}"

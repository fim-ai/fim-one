"""Tests for the GroundedRetrieveTool."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_agent.core.tool.builtin.grounded_retrieve import GroundedRetrieveTool


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
    from fim_agent.rag.base import Document
    from fim_agent.rag.grounding import Citation, EvidenceUnit, GroundedResult

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
                query_alignment=0.9,
                kb_id="kb1",
                rank=0,
            ),
        ],
        conflicts=[],
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
            "fim_agent.web.deps.get_kb_manager",
        ),
        patch(
            "fim_agent.web.deps.get_embedding",
        ),
        patch(
            "fim_agent.web.deps.get_fast_llm",
        ),
        patch(
            "fim_agent.rag.grounding.GroundingPipeline",
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

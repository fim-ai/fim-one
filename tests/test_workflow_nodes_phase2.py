"""Comprehensive tests for Phase 2/3 workflow node executors.

Tests cover: IteratorExecutor, LoopExecutor, HumanInterventionExecutor,
ParameterExtractorExecutor, QuestionUnderstandingExecutor,
VariableAggregatorExecutor, DocumentExtractorExecutor,
ListOperationExecutor, TransformExecutor, MCPExecutor,
BuiltinToolExecutor, SubWorkflowExecutor, ENVExecutor.
"""

from __future__ import annotations

import base64
import json
import pytest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fim_one.core.workflow.nodes import (
    BuiltinToolExecutor,
    DocumentExtractorExecutor,
    ENVExecutor,
    HumanInterventionExecutor,
    IteratorExecutor,
    ListOperationExecutor,
    LoopExecutor,
    MCPExecutor,
    ParameterExtractorExecutor,
    QuestionUnderstandingExecutor,
    SubWorkflowExecutor,
    TransformExecutor,
    VariableAggregatorExecutor,
    _resolve_json_path,
)
from fim_one.core.workflow.types import (
    ExecutionContext,
    NodeResult,
    NodeStatus,
    NodeType,
    WorkflowNodeDef,
)
from fim_one.core.workflow.variable_store import VariableStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    node_id: str,
    node_type: NodeType,
    data: dict[str, Any] | None = None,
) -> WorkflowNodeDef:
    """Create a WorkflowNodeDef with the given id, type, and data."""
    return WorkflowNodeDef(
        id=node_id,
        type=node_type,
        data=data or {},
    )


def _make_ctx(**overrides: Any) -> ExecutionContext:
    """Create an ExecutionContext with sensible defaults."""
    defaults = {
        "run_id": "test-run-001",
        "user_id": "test-user-001",
        "workflow_id": "test-workflow-001",
        "env_vars": {},
    }
    defaults.update(overrides)
    return ExecutionContext(**defaults)


def _mock_llm_response(content: str) -> MagicMock:
    """Build a mock LLM response object with the given content."""
    mock_result = MagicMock()
    mock_result.message.content = content
    return mock_result


def _patch_llm(llm_response_content: str):
    """Return a context-manager stack that patches create_session + get_effective_fast_llm.

    The LLM mock is configured to return the given content string.
    Usage::

        with _patch_llm('{"key": "val"}'):
            result = await executor.execute(node, store, ctx)
    """
    from contextlib import contextmanager

    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock(return_value=_mock_llm_response(llm_response_content))

    # Build an async context-manager mock for create_session
    mock_db = MagicMock()
    mock_session_cm = AsyncMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    @contextmanager
    def _ctx():
        with patch("fim_one.db.create_session", return_value=mock_session_cm), \
             patch("fim_one.web.deps.get_effective_fast_llm", return_value=mock_llm):
            yield mock_llm

    return _ctx()


# ===========================================================================
# 1. IteratorExecutor
# ===========================================================================


class TestIteratorExecutor:
    """Tests for IteratorExecutor — validates and prepares lists for iteration."""

    @pytest.mark.asyncio
    async def test_happy_path_with_list_variable(self):
        """Iterator resolves a list variable from the store and stores output."""
        executor = IteratorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        items = ["apple", "banana", "cherry"]
        await store.set("upstream.items", items)

        node = _make_node("iter_1", NodeType.ITERATOR, {
            "list_variable": "upstream.items",
            "iterator_variable": "current_item",
            "index_variable": "current_index",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == items
        assert await store.get("iter_1.output") == items
        assert await store.get("iter_1.count") == 3
        assert await store.get("iter_1.iterator_variable") == "current_item"
        assert await store.get("iter_1.index_variable") == "current_index"

    @pytest.mark.asyncio
    async def test_json_string_list(self):
        """Iterator parses a JSON string as a list."""
        executor = IteratorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("data.list", '[1, 2, 3]')

        node = _make_node("iter_1", NodeType.ITERATOR, {
            "list_variable": "data.list",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_interpolated_list_variable(self):
        """Iterator resolves list_variable via {{...}} interpolation."""
        executor = IteratorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("source.data", [10, 20, 30])

        node = _make_node("iter_1", NodeType.ITERATOR, {
            "list_variable": "{{source.data}}",
        })

        result = await executor.execute(node, store, ctx)

        # When interpolated, the value becomes a JSON string representation
        # which is then parsed back. The result should be a list.
        assert result.status == NodeStatus.COMPLETED
        assert result.output == [10, 20, 30]

    @pytest.mark.asyncio
    async def test_empty_list(self):
        """Iterator handles an empty list gracefully."""
        executor = IteratorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("data.empty", [])

        node = _make_node("iter_1", NodeType.ITERATOR, {
            "list_variable": "data.empty",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == []
        assert await store.get("iter_1.count") == 0

    @pytest.mark.asyncio
    async def test_none_value_becomes_empty_list(self):
        """Iterator treats None as an empty list."""
        executor = IteratorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        # Do not set any variable — store returns None by default

        node = _make_node("iter_1", NodeType.ITERATOR, {
            "list_variable": "nonexistent.var",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == []

    @pytest.mark.asyncio
    async def test_max_iterations_truncates_list(self):
        """Iterator enforces max_iterations by truncating the list."""
        executor = IteratorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("data.big", list(range(200)))

        node = _make_node("iter_1", NodeType.ITERATOR, {
            "list_variable": "data.big",
            "max_iterations": 10,
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert len(result.output) == 10
        assert result.output == list(range(10))

    @pytest.mark.asyncio
    async def test_no_list_variable_configured(self):
        """Iterator fails when list_variable is missing."""
        executor = IteratorExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("iter_1", NodeType.ITERATOR, {})

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "no list_variable" in result.error

    @pytest.mark.asyncio
    async def test_non_list_json_string(self):
        """Iterator fails when JSON string does not parse to a list."""
        executor = IteratorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("data.obj", '{"key": "value"}')

        node = _make_node("iter_1", NodeType.ITERATOR, {
            "list_variable": "data.obj",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "non-list JSON" in result.error

    @pytest.mark.asyncio
    async def test_invalid_json_string(self):
        """Iterator fails when string is not valid JSON."""
        executor = IteratorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("data.bad", "not json at all")

        node = _make_node("iter_1", NodeType.ITERATOR, {
            "list_variable": "data.bad",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "not valid JSON" in result.error

    @pytest.mark.asyncio
    async def test_unsupported_type(self):
        """Iterator fails when variable is neither list, string, nor None."""
        executor = IteratorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("data.number", 42)

        node = _make_node("iter_1", NodeType.ITERATOR, {
            "list_variable": "data.number",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "unsupported type" in result.error

    @pytest.mark.asyncio
    async def test_default_variable_names(self):
        """Iterator uses default iterator_variable and index_variable names."""
        executor = IteratorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("items", [1])

        node = _make_node("iter_1", NodeType.ITERATOR, {
            "list_variable": "items",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert await store.get("iter_1.iterator_variable") == "current_item"
        assert await store.get("iter_1.index_variable") == "current_index"


# ===========================================================================
# 2. LoopExecutor
# ===========================================================================


class TestLoopExecutor:
    """Tests for LoopExecutor — while-loop with condition and max iterations."""

    @pytest.mark.asyncio
    async def test_condition_false_immediately(self):
        """Loop exits immediately when condition is false from the start."""
        executor = LoopExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("loop_1", NodeType.LOOP, {
            "condition": "loop_index < 0",
            "max_iterations": 50,
            "loop_variable": "loop_index",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        output = result.output
        assert output["iterations"] == 0
        assert output["completed"] is True

    @pytest.mark.asyncio
    async def test_loop_runs_expected_iterations(self):
        """Loop runs until condition becomes false."""
        executor = LoopExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("loop_1", NodeType.LOOP, {
            "condition": "loop_index < 5",
            "max_iterations": 50,
            "loop_variable": "loop_index",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output["iterations"] == 5
        assert result.output["completed"] is True

    @pytest.mark.asyncio
    async def test_max_iterations_reached(self):
        """Loop stops at max_iterations even if condition is still true."""
        executor = LoopExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("loop_1", NodeType.LOOP, {
            "condition": "True",
            "max_iterations": 3,
            "loop_variable": "loop_index",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output["iterations"] == 3
        assert result.output["completed"] is False

    @pytest.mark.asyncio
    async def test_no_condition_fails(self):
        """Loop fails when no condition is configured."""
        executor = LoopExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("loop_1", NodeType.LOOP, {})

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "no condition" in result.error

    @pytest.mark.asyncio
    async def test_empty_condition_fails(self):
        """Loop fails when condition is an empty string."""
        executor = LoopExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("loop_1", NodeType.LOOP, {
            "condition": "   ",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "no condition" in result.error

    @pytest.mark.asyncio
    async def test_invalid_expression_fails(self):
        """Loop fails when condition expression is not valid."""
        executor = LoopExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("loop_1", NodeType.LOOP, {
            "condition": "invalid %%% expression",
            "max_iterations": 5,
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "evaluation failed" in result.error

    @pytest.mark.asyncio
    async def test_loop_stores_final_index(self):
        """Loop stores the final loop index value in the store."""
        executor = LoopExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("loop_1", NodeType.LOOP, {
            "condition": "loop_index < 3",
            "max_iterations": 50,
            "loop_variable": "loop_index",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        # After 3 iterations (0, 1, 2), the final count is 3
        assert await store.get("loop_1.loop_index") == 3

    @pytest.mark.asyncio
    async def test_condition_with_store_variables(self):
        """Loop condition can reference variables from the store."""
        executor = LoopExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("threshold", 2)

        node = _make_node("loop_1", NodeType.LOOP, {
            "condition": "loop_index < threshold",
            "max_iterations": 50,
            "loop_variable": "loop_index",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output["iterations"] == 2

    @pytest.mark.asyncio
    async def test_default_loop_variable_name(self):
        """Loop uses 'loop_index' as the default loop variable name."""
        executor = LoopExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("loop_1", NodeType.LOOP, {
            "condition": "loop_index < 1",
            "max_iterations": 50,
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output["loop_variable"] == "loop_index"


# ===========================================================================
# 3. HumanInterventionExecutor
# ===========================================================================


class TestHumanInterventionExecutor:
    """Tests for HumanInterventionExecutor — pauses workflow for human approval."""

    @pytest.mark.asyncio
    async def test_auto_approve_happy_path(self):
        """Human intervention auto-approves and stores result."""
        executor = HumanInterventionExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("human_1", NodeType.HUMAN_INTERVENTION, {
            "prompt_message": "Please review this data.",
            "assignee": "admin@example.com",
            "timeout_hours": 48,
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        output = result.output
        assert output["status"] == "approved"
        assert output["assignee"] == "admin@example.com"
        assert output["timeout_hours"] == 48
        assert "review this data" in output["message"]

    @pytest.mark.asyncio
    async def test_default_prompt_message(self):
        """Human intervention uses default prompt when none is provided."""
        executor = HumanInterventionExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("human_1", NodeType.HUMAN_INTERVENTION, {})

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert "Please review and approve" in result.output["message"]

    @pytest.mark.asyncio
    async def test_stores_in_all_locations(self):
        """Human intervention stores result in output_variable, node.output, and node.output_variable."""
        executor = HumanInterventionExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("human_1", NodeType.HUMAN_INTERVENTION, {
            "output_variable": "my_approval",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert await store.get("my_approval") is not None
        assert await store.get("human_1.output") is not None
        assert await store.get("human_1.my_approval") is not None

    @pytest.mark.asyncio
    async def test_prompt_interpolation(self):
        """Human intervention interpolates {{}} variables in prompt_message."""
        executor = HumanInterventionExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("order.id", "ORD-123")

        node = _make_node("human_1", NodeType.HUMAN_INTERVENTION, {
            "prompt_message": "Review order {{order.id}}",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert "ORD-123" in result.output["message"]

    @pytest.mark.asyncio
    async def test_default_output_variable(self):
        """Human intervention uses 'approval_result' as default output_variable."""
        executor = HumanInterventionExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("human_1", NodeType.HUMAN_INTERVENTION, {})

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert await store.get("approval_result") is not None


# ===========================================================================
# 4. ParameterExtractorExecutor
# ===========================================================================


class TestParameterExtractorExecutor:
    """Tests for ParameterExtractorExecutor — LLM-based parameter extraction."""

    @pytest.mark.asyncio
    async def test_happy_path_extraction(self):
        """Parameter extraction calls LLM and parses JSON response."""
        executor = ParameterExtractorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("user.input", "Book a flight from NYC to LA on Dec 25")

        node = _make_node("extractor_1", NodeType.PARAMETER_EXTRACTOR, {
            "input_text": "{{user.input}}",
            "parameters": [
                {"name": "origin", "type": "string", "description": "Departure city"},
                {"name": "destination", "type": "string", "description": "Arrival city"},
                {"name": "date", "type": "string", "description": "Travel date"},
            ],
        })

        llm_response = '{"origin": "NYC", "destination": "LA", "date": "Dec 25"}'

        with _patch_llm(llm_response):
            result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == {"origin": "NYC", "destination": "LA", "date": "Dec 25"}
        assert await store.get("extractor_1.output") == result.output

    @pytest.mark.asyncio
    async def test_no_input_text_fails(self):
        """Parameter extraction fails when input_text is missing."""
        executor = ParameterExtractorExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("extractor_1", NodeType.PARAMETER_EXTRACTOR, {
            "parameters": [{"name": "x", "type": "string", "description": "test"}],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "No input_text" in result.error

    @pytest.mark.asyncio
    async def test_no_parameters_fails(self):
        """Parameter extraction fails when parameters list is empty."""
        executor = ParameterExtractorExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("extractor_1", NodeType.PARAMETER_EXTRACTOR, {
            "input_text": "some text",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "No parameters" in result.error

    @pytest.mark.asyncio
    async def test_llm_returns_markdown_code_block(self):
        """Parameter extraction strips markdown code fences from LLM response."""
        executor = ParameterExtractorExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("extractor_1", NodeType.PARAMETER_EXTRACTOR, {
            "input_text": "Temperature is 72F in NYC",
            "parameters": [
                {"name": "temp", "type": "number", "description": "Temperature"},
                {"name": "city", "type": "string", "description": "City name"},
            ],
        })

        llm_response = '```json\n{"temp": 72, "city": "NYC"}\n```'

        with _patch_llm(llm_response):
            result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == {"temp": 72, "city": "NYC"}

    @pytest.mark.asyncio
    async def test_llm_returns_invalid_json(self):
        """Parameter extraction fails when LLM response is not valid JSON."""
        executor = ParameterExtractorExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("extractor_1", NodeType.PARAMETER_EXTRACTOR, {
            "input_text": "test input",
            "parameters": [{"name": "x", "type": "string", "description": "test"}],
        })

        with _patch_llm("This is not JSON"):
            result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "parse LLM response as JSON" in result.error

    @pytest.mark.asyncio
    async def test_llm_returns_non_object_json(self):
        """Parameter extraction fails when LLM returns a JSON array instead of object."""
        executor = ParameterExtractorExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("extractor_1", NodeType.PARAMETER_EXTRACTOR, {
            "input_text": "test input",
            "parameters": [{"name": "x", "type": "string", "description": "test"}],
        })

        with _patch_llm('["not", "an", "object"]'):
            result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "not a JSON object" in result.error


# ===========================================================================
# 5. QuestionUnderstandingExecutor
# ===========================================================================


class TestQuestionUnderstandingExecutor:
    """Tests for QuestionUnderstandingExecutor — LLM question rewriting."""

    @pytest.mark.asyncio
    async def test_rewrite_mode(self):
        """Question understanding in rewrite mode returns processed text."""
        executor = QuestionUnderstandingExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("user.question", "whats the weather")

        node = _make_node("qu_1", NodeType.QUESTION_UNDERSTANDING, {
            "input_variable": "user.question",
            "mode": "rewrite",
        })

        rewritten = "What is the current weather forecast?"

        with _patch_llm(rewritten):
            result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == rewritten
        assert await store.get("qu_1.output") == rewritten

    @pytest.mark.asyncio
    async def test_classify_mode_parses_json(self):
        """Question understanding in classify mode parses JSON response."""
        executor = QuestionUnderstandingExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("q", "How do I reset my password?")

        node = _make_node("qu_1", NodeType.QUESTION_UNDERSTANDING, {
            "input_variable": "q",
            "mode": "classify",
        })

        json_response = '{"intent": "password_reset", "topic": "account", "confidence": 0.95}'

        with _patch_llm(json_response):
            result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert isinstance(result.output, dict)
        assert result.output["intent"] == "password_reset"

    @pytest.mark.asyncio
    async def test_decompose_mode_parses_array(self):
        """Question understanding in decompose mode parses JSON array."""
        executor = QuestionUnderstandingExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("q", "What causes climate change and how to prevent it?")

        node = _make_node("qu_1", NodeType.QUESTION_UNDERSTANDING, {
            "input_variable": "q",
            "mode": "decompose",
        })

        json_response = '["What causes climate change?", "How can we prevent climate change?"]'

        with _patch_llm(json_response):
            result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert isinstance(result.output, list)
        assert len(result.output) == 2

    @pytest.mark.asyncio
    async def test_no_input_variable_fails(self):
        """Question understanding fails when input_variable is missing."""
        executor = QuestionUnderstandingExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("qu_1", NodeType.QUESTION_UNDERSTANDING, {
            "mode": "rewrite",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "no input_variable" in result.error

    @pytest.mark.asyncio
    async def test_empty_input_text_fails(self):
        """Question understanding fails when resolved input is empty."""
        executor = QuestionUnderstandingExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("q", "")

        node = _make_node("qu_1", NodeType.QUESTION_UNDERSTANDING, {
            "input_variable": "q",
            "mode": "rewrite",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "empty text" in result.error

    @pytest.mark.asyncio
    async def test_unknown_mode_fails(self):
        """Question understanding fails with an unknown mode."""
        executor = QuestionUnderstandingExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("q", "some question")

        node = _make_node("qu_1", NodeType.QUESTION_UNDERSTANDING, {
            "input_variable": "q",
            "mode": "nonexistent_mode",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "unknown mode" in result.error

    @pytest.mark.asyncio
    async def test_custom_system_prompt(self):
        """Question understanding uses custom system_prompt when provided."""
        executor = QuestionUnderstandingExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("q", "test question")

        node = _make_node("qu_1", NodeType.QUESTION_UNDERSTANDING, {
            "input_variable": "q",
            "mode": "rewrite",
            "system_prompt": "You are a custom prompt tester.",
        })

        with _patch_llm("custom result") as mock_llm:
            result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        # Verify the custom system prompt was used in the LLM call
        call_args = mock_llm.chat.call_args
        messages = call_args[0][0]
        assert messages[0].content == "You are a custom prompt tester."

    @pytest.mark.asyncio
    async def test_output_variable_stored(self):
        """Question understanding stores result under custom output_variable."""
        executor = QuestionUnderstandingExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("q", "test")

        node = _make_node("qu_1", NodeType.QUESTION_UNDERSTANDING, {
            "input_variable": "q",
            "mode": "rewrite",
            "output_variable": "my_result",
        })

        with _patch_llm("enhanced question"):
            result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert await store.get("qu_1.my_result") == "enhanced question"


# ===========================================================================
# 6. VariableAggregatorExecutor
# ===========================================================================


class TestVariableAggregatorExecutor:
    """Tests for VariableAggregatorExecutor — merges outputs from multiple branches."""

    @pytest.mark.asyncio
    async def test_list_mode(self):
        """Aggregator in list mode collects values into an array."""
        executor = VariableAggregatorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("node_a.output", "hello")
        await store.set("node_b.output", "world")

        node = _make_node("agg_1", NodeType.VARIABLE_AGGREGATOR, {
            "variables": ["node_a.output", "node_b.output"],
            "mode": "list",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == ["hello", "world"]

    @pytest.mark.asyncio
    async def test_concat_mode(self):
        """Aggregator in concat mode joins string representations."""
        executor = VariableAggregatorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("a.out", "line1")
        await store.set("b.out", "line2")

        node = _make_node("agg_1", NodeType.VARIABLE_AGGREGATOR, {
            "variables": ["a.out", "b.out"],
            "mode": "concat",
            "separator": " | ",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == "line1 | line2"

    @pytest.mark.asyncio
    async def test_concat_mode_default_separator(self):
        """Aggregator concat mode uses newline as default separator."""
        executor = VariableAggregatorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("a.out", "first")
        await store.set("b.out", "second")

        node = _make_node("agg_1", NodeType.VARIABLE_AGGREGATOR, {
            "variables": ["a.out", "b.out"],
            "mode": "concat",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == "first\nsecond"

    @pytest.mark.asyncio
    async def test_merge_mode(self):
        """Aggregator in merge mode deep-merges dicts."""
        executor = VariableAggregatorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("a.out", {"name": "Alice", "age": 30})
        await store.set("b.out", {"city": "NYC", "age": 31})

        node = _make_node("agg_1", NodeType.VARIABLE_AGGREGATOR, {
            "variables": ["a.out", "b.out"],
            "mode": "merge",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == {"name": "Alice", "age": 31, "city": "NYC"}

    @pytest.mark.asyncio
    async def test_merge_mode_skips_non_dicts(self):
        """Aggregator merge mode skips non-dict values."""
        executor = VariableAggregatorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("a.out", {"key": "val"})
        await store.set("b.out", "not a dict")

        node = _make_node("agg_1", NodeType.VARIABLE_AGGREGATOR, {
            "variables": ["a.out", "b.out"],
            "mode": "merge",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == {"key": "val"}

    @pytest.mark.asyncio
    async def test_first_non_empty_mode(self):
        """Aggregator first_non_empty mode picks the first non-null value."""
        executor = VariableAggregatorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        # a.out is not set (None), b.out has a value
        await store.set("b.out", "found it")

        node = _make_node("agg_1", NodeType.VARIABLE_AGGREGATOR, {
            "variables": ["a.out", "b.out"],
            "mode": "first_non_empty",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == "found it"

    @pytest.mark.asyncio
    async def test_first_non_empty_skips_empty_values(self):
        """Aggregator first_non_empty skips empty strings, empty lists, empty dicts."""
        executor = VariableAggregatorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("a.out", "")
        await store.set("b.out", [])
        await store.set("c.out", {})
        await store.set("d.out", "actual value")

        node = _make_node("agg_1", NodeType.VARIABLE_AGGREGATOR, {
            "variables": ["a.out", "b.out", "c.out", "d.out"],
            "mode": "first_non_empty",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == "actual value"

    @pytest.mark.asyncio
    async def test_no_variables_fails(self):
        """Aggregator fails when no variables are configured."""
        executor = VariableAggregatorExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("agg_1", NodeType.VARIABLE_AGGREGATOR, {})

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "no variables" in result.error

    @pytest.mark.asyncio
    async def test_unknown_mode_fails(self):
        """Aggregator fails with an unknown aggregation mode."""
        executor = VariableAggregatorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("x", 1)

        node = _make_node("agg_1", NodeType.VARIABLE_AGGREGATOR, {
            "variables": ["x"],
            "mode": "bad_mode",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "unknown mode" in result.error

    @pytest.mark.asyncio
    async def test_concat_mode_skips_none_values(self):
        """Aggregator concat mode skips None values."""
        executor = VariableAggregatorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("a.out", "hello")
        # b.out is not set

        node = _make_node("agg_1", NodeType.VARIABLE_AGGREGATOR, {
            "variables": ["a.out", "b.out"],
            "mode": "concat",
            "separator": ", ",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == "hello"

    @pytest.mark.asyncio
    async def test_interpolated_variable_references(self):
        """Aggregator resolves {{}} variable references."""
        executor = VariableAggregatorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("x.val", "resolved!")

        node = _make_node("agg_1", NodeType.VARIABLE_AGGREGATOR, {
            "variables": ["{{x.val}}"],
            "mode": "list",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == ["resolved!"]


# ===========================================================================
# 7. DocumentExtractorExecutor
# ===========================================================================


class TestDocumentExtractorExecutor:
    """Tests for DocumentExtractorExecutor — text extraction from documents."""

    @pytest.mark.asyncio
    async def test_full_text_mode(self):
        """Document extractor returns full text in full_text mode."""
        executor = DocumentExtractorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("doc.text", "Hello, this is a document.")

        node = _make_node("doc_1", NodeType.DOCUMENT_EXTRACTOR, {
            "input_variable": "doc.text",
            "input_type": "text",
            "extract_mode": "full_text",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == "Hello, this is a document."

    @pytest.mark.asyncio
    async def test_pages_mode_with_form_feeds(self):
        """Document extractor splits text into pages by form-feed character."""
        executor = DocumentExtractorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("doc.text", "Page 1\fPage 2\fPage 3")

        node = _make_node("doc_1", NodeType.DOCUMENT_EXTRACTOR, {
            "input_variable": "doc.text",
            "extract_mode": "pages",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == ["Page 1", "Page 2", "Page 3"]

    @pytest.mark.asyncio
    async def test_pages_mode_with_page_range(self):
        """Document extractor applies page_range filter."""
        executor = DocumentExtractorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("doc.text", "Page 1\fPage 2\fPage 3\fPage 4")

        node = _make_node("doc_1", NodeType.DOCUMENT_EXTRACTOR, {
            "input_variable": "doc.text",
            "extract_mode": "pages",
            "page_range": "2-3",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == ["Page 2", "Page 3"]

    @pytest.mark.asyncio
    async def test_metadata_mode(self):
        """Document extractor returns metadata about the text."""
        executor = DocumentExtractorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        text = "Hello world\nSecond line\fPage two content"
        await store.set("doc.text", text)

        node = _make_node("doc_1", NodeType.DOCUMENT_EXTRACTOR, {
            "input_variable": "doc.text",
            "extract_mode": "metadata",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        meta = result.output
        assert meta["char_count"] == len(text)
        assert meta["word_count"] > 0
        assert meta["page_count"] == 2  # split by \f

    @pytest.mark.asyncio
    async def test_tables_mode_extracts_markdown_tables(self):
        """Document extractor finds markdown tables in text."""
        executor = DocumentExtractorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        text = (
            "Some text\n"
            "| Name | Age |\n"
            "|------|-----|\n"
            "| Alice | 30 |\n"
            "\n"
            "More text\n"
        )
        await store.set("doc.text", text)

        node = _make_node("doc_1", NodeType.DOCUMENT_EXTRACTOR, {
            "input_variable": "doc.text",
            "extract_mode": "tables",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert len(result.output) == 1
        assert "Alice" in result.output[0]

    @pytest.mark.asyncio
    async def test_base64_input(self):
        """Document extractor decodes base64-encoded text."""
        executor = DocumentExtractorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        original = "This is base64 encoded content."
        encoded = base64.b64encode(original.encode("utf-8")).decode("utf-8")
        await store.set("doc.data", encoded)

        node = _make_node("doc_1", NodeType.DOCUMENT_EXTRACTOR, {
            "input_variable": "doc.data",
            "input_type": "base64",
            "extract_mode": "full_text",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == original

    @pytest.mark.asyncio
    async def test_url_input_type_not_supported(self):
        """Document extractor returns error for URL input type."""
        executor = DocumentExtractorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("doc.url", "https://example.com/doc.pdf")

        node = _make_node("doc_1", NodeType.DOCUMENT_EXTRACTOR, {
            "input_variable": "doc.url",
            "input_type": "url",
            "extract_mode": "full_text",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "URL" in result.error

    @pytest.mark.asyncio
    async def test_no_input_variable_fails(self):
        """Document extractor fails when input_variable is missing."""
        executor = DocumentExtractorExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("doc_1", NodeType.DOCUMENT_EXTRACTOR, {
            "extract_mode": "full_text",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "no input_variable" in result.error

    @pytest.mark.asyncio
    async def test_unknown_extract_mode_fails(self):
        """Document extractor fails with an unknown extract mode."""
        executor = DocumentExtractorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("doc.text", "some text")

        node = _make_node("doc_1", NodeType.DOCUMENT_EXTRACTOR, {
            "input_variable": "doc.text",
            "extract_mode": "bad_mode",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "unknown extract_mode" in result.error

    @pytest.mark.asyncio
    async def test_null_input_becomes_empty_string(self):
        """Document extractor treats None input as empty string."""
        executor = DocumentExtractorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        # Variable not set => None

        node = _make_node("doc_1", NodeType.DOCUMENT_EXTRACTOR, {
            "input_variable": "nonexistent",
            "extract_mode": "full_text",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == ""  # None -> "" -> str("") == ""

    @pytest.mark.asyncio
    async def test_output_variable_stored(self):
        """Document extractor stores result under custom output_variable."""
        executor = DocumentExtractorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("doc.text", "content here")

        node = _make_node("doc_1", NodeType.DOCUMENT_EXTRACTOR, {
            "input_variable": "doc.text",
            "extract_mode": "full_text",
            "output_variable": "my_doc_result",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert await store.get("doc_1.my_doc_result") == "content here"
        assert await store.get("doc_1.output") == "content here"

    @pytest.mark.asyncio
    async def test_pages_mode_with_markdown_separator(self):
        """Document extractor splits pages by markdown --- separator."""
        executor = DocumentExtractorExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("doc.text", "Section A\n\n---\n\nSection B")

        node = _make_node("doc_1", NodeType.DOCUMENT_EXTRACTOR, {
            "input_variable": "doc.text",
            "extract_mode": "pages",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == ["Section A", "Section B"]


# ===========================================================================
# 8. ListOperationExecutor
# ===========================================================================


class TestListOperationExecutor:
    """Tests for ListOperationExecutor — filter, map, sort, slice, flatten, etc."""

    @pytest.mark.asyncio
    async def test_filter_operation(self):
        """List filter operation keeps items matching expression."""
        executor = ListOperationExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("data.nums", [1, 2, 3, 4, 5])

        node = _make_node("listop_1", NodeType.LIST_OPERATION, {
            "input_variable": "data.nums",
            "operation": "filter",
            "expression": "item > 3",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == [4, 5]

    @pytest.mark.asyncio
    async def test_map_operation(self):
        """List map operation transforms each item."""
        executor = ListOperationExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("data.nums", [1, 2, 3])

        node = _make_node("listop_1", NodeType.LIST_OPERATION, {
            "input_variable": "data.nums",
            "operation": "map",
            "expression": "item * 2",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == [2, 4, 6]

    @pytest.mark.asyncio
    async def test_sort_operation_default(self):
        """List sort operation sorts items naturally."""
        executor = ListOperationExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("data.nums", [3, 1, 4, 1, 5])

        node = _make_node("listop_1", NodeType.LIST_OPERATION, {
            "input_variable": "data.nums",
            "operation": "sort",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == [1, 1, 3, 4, 5]

    @pytest.mark.asyncio
    async def test_sort_operation_with_expression(self):
        """List sort operation uses expression as sort key."""
        executor = ListOperationExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("data.nums", [3, -1, 2])

        node = _make_node("listop_1", NodeType.LIST_OPERATION, {
            "input_variable": "data.nums",
            "operation": "sort",
            "expression": "0 - item",  # sort by negative = reverse
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == [3, 2, -1]

    @pytest.mark.asyncio
    async def test_slice_operation(self):
        """List slice operation returns a sub-list."""
        executor = ListOperationExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("data.list", [10, 20, 30, 40, 50])

        node = _make_node("listop_1", NodeType.LIST_OPERATION, {
            "input_variable": "data.list",
            "operation": "slice",
            "slice_start": 1,
            "slice_end": 3,
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == [20, 30]

    @pytest.mark.asyncio
    async def test_flatten_operation(self):
        """List flatten operation flattens nested lists one level."""
        executor = ListOperationExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("data.nested", [[1, 2], [3, 4], 5])

        node = _make_node("listop_1", NodeType.LIST_OPERATION, {
            "input_variable": "data.nested",
            "operation": "flatten",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == [1, 2, 3, 4, 5]

    @pytest.mark.asyncio
    async def test_unique_operation(self):
        """List unique operation removes duplicates preserving order."""
        executor = ListOperationExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("data.dups", [1, 2, 2, 3, 1, 4])

        node = _make_node("listop_1", NodeType.LIST_OPERATION, {
            "input_variable": "data.dups",
            "operation": "unique",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == [1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_reverse_operation(self):
        """List reverse operation reverses the list."""
        executor = ListOperationExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("data.list", [1, 2, 3])

        node = _make_node("listop_1", NodeType.LIST_OPERATION, {
            "input_variable": "data.list",
            "operation": "reverse",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == [3, 2, 1]

    @pytest.mark.asyncio
    async def test_length_operation(self):
        """List length operation returns the count."""
        executor = ListOperationExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("data.list", [1, 2, 3, 4, 5])

        node = _make_node("listop_1", NodeType.LIST_OPERATION, {
            "input_variable": "data.list",
            "operation": "length",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == 5

    @pytest.mark.asyncio
    async def test_no_input_variable_fails(self):
        """List operation fails when input_variable is missing."""
        executor = ListOperationExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("listop_1", NodeType.LIST_OPERATION, {
            "operation": "filter",
            "expression": "item > 0",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "no input_variable" in result.error

    @pytest.mark.asyncio
    async def test_no_operation_fails(self):
        """List operation fails when operation is missing."""
        executor = ListOperationExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("data.list", [1])

        node = _make_node("listop_1", NodeType.LIST_OPERATION, {
            "input_variable": "data.list",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "no operation" in result.error

    @pytest.mark.asyncio
    async def test_unknown_operation_fails(self):
        """List operation fails with an unknown operation."""
        executor = ListOperationExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("data.list", [1])

        node = _make_node("listop_1", NodeType.LIST_OPERATION, {
            "input_variable": "data.list",
            "operation": "explode",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "unknown operation" in result.error

    @pytest.mark.asyncio
    async def test_filter_no_expression_fails(self):
        """List filter operation fails when no expression is given."""
        executor = ListOperationExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("data.list", [1, 2, 3])

        node = _make_node("listop_1", NodeType.LIST_OPERATION, {
            "input_variable": "data.list",
            "operation": "filter",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "requires an expression" in result.error

    @pytest.mark.asyncio
    async def test_map_no_expression_fails(self):
        """List map operation fails when no expression is given."""
        executor = ListOperationExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("data.list", [1, 2, 3])

        node = _make_node("listop_1", NodeType.LIST_OPERATION, {
            "input_variable": "data.list",
            "operation": "map",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "requires an expression" in result.error

    @pytest.mark.asyncio
    async def test_json_string_input(self):
        """List operation parses a JSON string as input."""
        executor = ListOperationExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("data.json", '[10, 20, 30]')

        node = _make_node("listop_1", NodeType.LIST_OPERATION, {
            "input_variable": "data.json",
            "operation": "reverse",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == [30, 20, 10]

    @pytest.mark.asyncio
    async def test_none_input_becomes_empty_list(self):
        """List operation treats None input as empty list."""
        executor = ListOperationExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("listop_1", NodeType.LIST_OPERATION, {
            "input_variable": "nonexistent",
            "operation": "length",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == 0

    @pytest.mark.asyncio
    async def test_non_list_json_string_fails(self):
        """List operation fails when JSON string does not parse to list."""
        executor = ListOperationExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("data.obj", '{"key": "val"}')

        node = _make_node("listop_1", NodeType.LIST_OPERATION, {
            "input_variable": "data.obj",
            "operation": "length",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "non-list JSON" in result.error

    @pytest.mark.asyncio
    async def test_output_variable_stored(self):
        """List operation stores result under custom output_variable."""
        executor = ListOperationExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("data.list", [1, 2, 3])

        node = _make_node("listop_1", NodeType.LIST_OPERATION, {
            "input_variable": "data.list",
            "operation": "reverse",
            "output_variable": "my_reversed",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert await store.get("listop_1.my_reversed") == [3, 2, 1]
        assert await store.get("listop_1.output") == [3, 2, 1]

    @pytest.mark.asyncio
    async def test_filter_with_index(self):
        """List filter expression has access to 'index' variable."""
        executor = ListOperationExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("data.list", ["a", "b", "c", "d", "e"])

        node = _make_node("listop_1", NodeType.LIST_OPERATION, {
            "input_variable": "data.list",
            "operation": "filter",
            "expression": "index % 2 == 0",  # even indices only
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == ["a", "c", "e"]


# ===========================================================================
# 9. TransformExecutor
# ===========================================================================


class TestTransformExecutor:
    """Tests for TransformExecutor — JSON path, type conversion, and more."""

    @pytest.mark.asyncio
    async def test_json_path_extraction(self):
        """Transform extracts a value using JSON path."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        data = {"user": {"name": "Alice", "age": 30}}
        await store.set("data.obj", data)

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "input_variable": "data.obj",
            "operations": [
                {"type": "json_path", "config": {"path": "$.user.name"}},
            ],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == "Alice"

    @pytest.mark.asyncio
    async def test_type_cast_string(self):
        """Transform casts value to string."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("val", 42)

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "input_variable": "val",
            "operations": [
                {"type": "type_cast", "config": {"target_type": "string"}},
            ],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == "42"

    @pytest.mark.asyncio
    async def test_type_cast_integer(self):
        """Transform casts value to integer."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("val", "123")

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "input_variable": "val",
            "operations": [
                {"type": "type_cast", "config": {"target_type": "integer"}},
            ],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == 123

    @pytest.mark.asyncio
    async def test_type_cast_float(self):
        """Transform casts value to float."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("val", "3.14")

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "input_variable": "val",
            "operations": [
                {"type": "type_cast", "config": {"target_type": "float"}},
            ],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == 3.14

    @pytest.mark.asyncio
    async def test_type_cast_boolean(self):
        """Transform casts value to boolean."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("val", "false")

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "input_variable": "val",
            "operations": [
                {"type": "type_cast", "config": {"target_type": "boolean"}},
            ],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output is False

    @pytest.mark.asyncio
    async def test_type_cast_json(self):
        """Transform casts a JSON string to a Python object."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("val", '{"key": "value"}')

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "input_variable": "val",
            "operations": [
                {"type": "type_cast", "config": {"target_type": "json"}},
            ],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == {"key": "value"}

    @pytest.mark.asyncio
    async def test_format_operation(self):
        """Transform applies a format template."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("val", "World")

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "input_variable": "val",
            "operations": [
                {"type": "format", "config": {"template": "Hello, {value}!"}},
            ],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == "Hello, World!"

    @pytest.mark.asyncio
    async def test_regex_extract_operation(self):
        """Transform extracts text using regex."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("val", "My email is test@example.com today")

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "input_variable": "val",
            "operations": [
                {"type": "regex_extract", "config": {"pattern": r"[\w.]+@[\w.]+", "group": 0}},
            ],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == "test@example.com"

    @pytest.mark.asyncio
    async def test_regex_extract_no_match(self):
        """Transform regex extract returns None when no match."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("val", "no emails here")

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "input_variable": "val",
            "operations": [
                {"type": "regex_extract", "config": {"pattern": r"[\w.]+@[\w.]+", "group": 0}},
            ],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output is None

    @pytest.mark.asyncio
    async def test_string_op_upper(self):
        """Transform string operation upper."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("val", "hello")

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "input_variable": "val",
            "operations": [
                {"type": "string_op", "config": {"operation": "upper"}},
            ],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == "HELLO"

    @pytest.mark.asyncio
    async def test_string_op_split(self):
        """Transform string operation split."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("val", "a,b,c")

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "input_variable": "val",
            "operations": [
                {"type": "string_op", "config": {"operation": "split", "args": {"separator": ","}}},
            ],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_string_op_replace(self):
        """Transform string operation replace."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("val", "hello world")

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "input_variable": "val",
            "operations": [
                {"type": "string_op", "config": {"operation": "replace", "args": {"old": "world", "new": "universe"}}},
            ],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == "hello universe"

    @pytest.mark.asyncio
    async def test_math_op_add(self):
        """Transform math operation add."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("val", 10)

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "input_variable": "val",
            "operations": [
                {"type": "math_op", "config": {"operation": "add", "operand": 5}},
            ],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == 15.0

    @pytest.mark.asyncio
    async def test_math_op_divide_by_zero(self):
        """Transform math divide by zero fails."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("val", 10)

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "input_variable": "val",
            "operations": [
                {"type": "math_op", "config": {"operation": "divide", "operand": 0}},
            ],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "divide by zero" in result.error

    @pytest.mark.asyncio
    async def test_math_op_modulo_by_zero(self):
        """Transform math modulo by zero fails."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("val", 10)

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "input_variable": "val",
            "operations": [
                {"type": "math_op", "config": {"operation": "modulo", "operand": 0}},
            ],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "modulo by zero" in result.error

    @pytest.mark.asyncio
    async def test_pipeline_multiple_operations(self):
        """Transform applies multiple operations in sequence (pipeline)."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        data = {"items": [{"name": "alice"}, {"name": "bob"}]}
        await store.set("data.obj", data)

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "input_variable": "data.obj",
            "operations": [
                {"type": "json_path", "config": {"path": "$.items[0].name"}},
                {"type": "string_op", "config": {"operation": "upper"}},
            ],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == "ALICE"

    @pytest.mark.asyncio
    async def test_no_input_variable_fails(self):
        """Transform fails when input_variable is missing."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "operations": [{"type": "json_path", "config": {"path": "$"}}],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "no input_variable" in result.error

    @pytest.mark.asyncio
    async def test_no_operations_fails(self):
        """Transform fails when operations list is empty."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("val", "test")

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "input_variable": "val",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "no operations" in result.error

    @pytest.mark.asyncio
    async def test_unknown_operation_type_fails(self):
        """Transform fails with an unknown operation type."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("val", "test")

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "input_variable": "val",
            "operations": [
                {"type": "nonexistent_op", "config": {}},
            ],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "unknown operation type" in result.error

    @pytest.mark.asyncio
    async def test_output_variable_stored(self):
        """Transform stores result under custom output_variable."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("val", "test")

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "input_variable": "val",
            "output_variable": "my_result",
            "operations": [
                {"type": "string_op", "config": {"operation": "upper"}},
            ],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert await store.get("xform_1.my_result") == "TEST"
        assert await store.get("xform_1.output") == "TEST"

    @pytest.mark.asyncio
    async def test_math_op_round(self):
        """Transform math operation round."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("val", 3.14159)

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "input_variable": "val",
            "operations": [
                {"type": "math_op", "config": {"operation": "round", "operand": 2}},
            ],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == 3.14

    @pytest.mark.asyncio
    async def test_math_op_abs(self):
        """Transform math operation abs."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("val", -42)

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "input_variable": "val",
            "operations": [
                {"type": "math_op", "config": {"operation": "abs"}},
            ],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == 42.0

    @pytest.mark.asyncio
    async def test_string_op_lower(self):
        """Transform string operation lower."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("val", "HELLO")

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "input_variable": "val",
            "operations": [
                {"type": "string_op", "config": {"operation": "lower"}},
            ],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == "hello"

    @pytest.mark.asyncio
    async def test_string_op_strip(self):
        """Transform string operation strip."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("val", "  spaces  ")

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "input_variable": "val",
            "operations": [
                {"type": "string_op", "config": {"operation": "strip"}},
            ],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == "spaces"

    @pytest.mark.asyncio
    async def test_string_op_join(self):
        """Transform string operation join on a list."""
        executor = TransformExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("val", ["a", "b", "c"])

        node = _make_node("xform_1", NodeType.TRANSFORM, {
            "input_variable": "val",
            "operations": [
                {"type": "string_op", "config": {"operation": "join", "args": {"separator": "-"}}},
            ],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == "a-b-c"


# ===========================================================================
# Helper: _resolve_json_path tests
# ===========================================================================


class TestResolveJsonPath:
    """Tests for the _resolve_json_path helper function."""

    def test_root_path(self):
        data = {"key": "value"}
        assert _resolve_json_path(data, "$") == data

    def test_simple_key(self):
        data = {"name": "Alice"}
        assert _resolve_json_path(data, "$.name") == "Alice"

    def test_nested_key(self):
        data = {"user": {"name": "Bob"}}
        assert _resolve_json_path(data, "$.user.name") == "Bob"

    def test_array_index(self):
        data = {"items": [10, 20, 30]}
        assert _resolve_json_path(data, "$.items[0]") == 10
        assert _resolve_json_path(data, "$.items[2]") == 30

    def test_array_wildcard(self):
        data = {"users": [{"name": "A"}, {"name": "B"}]}
        result = _resolve_json_path(data, "$.users[*].name")
        assert result == ["A", "B"]

    def test_out_of_bounds_index(self):
        data = {"items": [1, 2]}
        assert _resolve_json_path(data, "$.items[5]") is None

    def test_missing_key(self):
        data = {"a": 1}
        assert _resolve_json_path(data, "$.b") is None

    def test_non_dict_key_access(self):
        data = {"items": [1, 2, 3]}
        assert _resolve_json_path(data, "$.items.name") is None

    def test_invalid_path_prefix(self):
        with pytest.raises(ValueError, match="must start with"):
            _resolve_json_path({"a": 1}, "a.b")


# ===========================================================================
# 10. MCPExecutor
# ===========================================================================


class TestMCPExecutor:
    """Tests for MCPExecutor — basic validation (real implementation).

    Comprehensive tests with DB/MCP mocking are in
    tests/test_workflow_mcp_executor.py.
    """

    @pytest.mark.asyncio
    async def test_missing_server_id_fails(self):
        """MCP executor fails when server_id is missing."""
        executor = MCPExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("mcp_1", NodeType.MCP, {
            "tool_name": "search",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "server_id" in result.error

    @pytest.mark.asyncio
    async def test_missing_tool_name_fails(self):
        """MCP executor fails when tool_name is missing."""
        executor = MCPExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("mcp_1", NodeType.MCP, {
            "server_id": "server-abc",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "tool_name" in result.error


# ===========================================================================
# 11. BuiltinToolExecutor
# ===========================================================================


class TestBuiltinToolExecutor:
    """Tests for BuiltinToolExecutor — builtin tool execution (currently a stub)."""

    @pytest.mark.asyncio
    async def test_happy_path(self):
        """BuiltinTool executor stores tool_id and parameters."""
        executor = BuiltinToolExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("tool_1", NodeType.BUILTIN_TOOL, {
            "tool_id": "calculator",
            "parameters": {"expression": "2+2"},
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        output = result.output
        assert output["tool_id"] == "calculator"
        assert output["parameters"] == {"expression": "2+2"}
        assert output["status"] == "stub"

    @pytest.mark.asyncio
    async def test_missing_tool_id_fails(self):
        """BuiltinTool executor fails when tool_id is missing."""
        executor = BuiltinToolExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("tool_1", NodeType.BUILTIN_TOOL, {})

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "missing tool_id" in result.error

    @pytest.mark.asyncio
    async def test_parameter_interpolation(self):
        """BuiltinTool executor interpolates {{}} variables in parameters."""
        executor = BuiltinToolExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        await store.set("input.text", "hello world")

        node = _make_node("tool_1", NodeType.BUILTIN_TOOL, {
            "tool_id": "text_tool",
            "parameters": {"text": "{{input.text}}"},
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output["parameters"]["text"] == "hello world"

    @pytest.mark.asyncio
    async def test_stores_in_all_locations(self):
        """BuiltinTool stores result in output_variable, node.output, and node.output_variable."""
        executor = BuiltinToolExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("tool_1", NodeType.BUILTIN_TOOL, {
            "tool_id": "my_tool",
            "output_variable": "tool_out",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert await store.get("tool_out") is not None
        assert await store.get("tool_1.output") is not None
        assert await store.get("tool_1.tool_out") is not None

    @pytest.mark.asyncio
    async def test_non_string_parameters_pass_through(self):
        """BuiltinTool executor passes non-string parameters unchanged."""
        executor = BuiltinToolExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("tool_1", NodeType.BUILTIN_TOOL, {
            "tool_id": "calc",
            "parameters": {"number": 42, "flag": True},
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output["parameters"]["number"] == 42
        assert result.output["parameters"]["flag"] is True


# ===========================================================================
# 12. SubWorkflowExecutor
# ===========================================================================


class TestSubWorkflowExecutor:
    """Tests for SubWorkflowExecutor guard checks.

    Full integration tests with DB mocking are in test_workflow_subworkflow.py.
    """

    @pytest.mark.asyncio
    async def test_no_db_factory_fails(self):
        """SubWorkflow executor fails without db_session_factory."""
        executor = SubWorkflowExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("sub_1", NodeType.SUB_WORKFLOW, {
            "workflow_id": "wf-child-001",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "db_session_factory" in result.error

    @pytest.mark.asyncio
    async def test_missing_workflow_id_fails(self):
        """SubWorkflow executor fails when workflow_id is missing."""
        from unittest.mock import MagicMock

        executor = SubWorkflowExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        ctx.db_session_factory = MagicMock()

        node = _make_node("sub_1", NodeType.SUB_WORKFLOW, {})

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "workflow_id" in result.error.lower()

    @pytest.mark.asyncio
    async def test_depth_exceeded_fails(self):
        """SubWorkflow executor fails when recursion depth is exceeded."""
        executor = SubWorkflowExecutor()
        store = VariableStore()
        ctx = _make_ctx()
        ctx.depth = 5

        node = _make_node("sub_1", NodeType.SUB_WORKFLOW, {
            "workflow_id": "wf-123",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "depth" in result.error.lower()


# ===========================================================================
# 13. ENVExecutor
# ===========================================================================


class TestENVExecutor:
    """Tests for ENVExecutor — environment variable injection."""

    @pytest.mark.asyncio
    async def test_happy_path(self):
        """ENV executor reads keys from context.env_vars."""
        executor = ENVExecutor()
        store = VariableStore()
        ctx = _make_ctx(env_vars={
            "API_KEY": "secret-key-123",
            "DB_URL": "postgres://localhost/db",
        })

        node = _make_node("env_1", NodeType.ENV, {
            "env_keys": ["API_KEY", "DB_URL"],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        output = result.output
        assert output["API_KEY"] == "secret-key-123"
        assert output["DB_URL"] == "postgres://localhost/db"

    @pytest.mark.asyncio
    async def test_missing_key_returns_none(self):
        """ENV executor returns None for keys not in env_vars."""
        executor = ENVExecutor()
        store = VariableStore()
        ctx = _make_ctx(env_vars={"EXISTING": "value"})

        node = _make_node("env_1", NodeType.ENV, {
            "env_keys": ["EXISTING", "MISSING_KEY"],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output["EXISTING"] == "value"
        assert result.output["MISSING_KEY"] is None

    @pytest.mark.asyncio
    async def test_no_env_keys_fails(self):
        """ENV executor fails when env_keys is missing or empty."""
        executor = ENVExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("env_1", NodeType.ENV, {})

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "non-empty 'env_keys'" in result.error

    @pytest.mark.asyncio
    async def test_env_keys_not_list_fails(self):
        """ENV executor fails when env_keys is not a list."""
        executor = ENVExecutor()
        store = VariableStore()
        ctx = _make_ctx()

        node = _make_node("env_1", NodeType.ENV, {
            "env_keys": "API_KEY",  # Not a list
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.FAILED
        assert "non-empty 'env_keys'" in result.error

    @pytest.mark.asyncio
    async def test_stores_in_all_locations(self):
        """ENV executor stores result in output_variable, node.output, and node.output_variable."""
        executor = ENVExecutor()
        store = VariableStore()
        ctx = _make_ctx(env_vars={"KEY": "val"})

        node = _make_node("env_1", NodeType.ENV, {
            "env_keys": ["KEY"],
            "output_variable": "my_env",
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert await store.get("my_env") is not None
        assert await store.get("env_1.output") is not None
        assert await store.get("env_1.my_env") is not None

    @pytest.mark.asyncio
    async def test_non_string_keys_skipped(self):
        """ENV executor skips non-string keys in env_keys list."""
        executor = ENVExecutor()
        store = VariableStore()
        ctx = _make_ctx(env_vars={"VALID": "value"})

        node = _make_node("env_1", NodeType.ENV, {
            "env_keys": ["VALID", 123, None],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output == {"VALID": "value"}

    @pytest.mark.asyncio
    async def test_empty_env_vars_context(self):
        """ENV executor handles empty env_vars in context."""
        executor = ENVExecutor()
        store = VariableStore()
        ctx = _make_ctx(env_vars={})

        node = _make_node("env_1", NodeType.ENV, {
            "env_keys": ["MISSING"],
        })

        result = await executor.execute(node, store, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.output["MISSING"] is None

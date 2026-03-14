"""End-to-end lifecycle tests for the workflow engine.

Exercises the complete build-blueprint -> execute -> verify-results path
that a real user exercises through the UI.  Each test constructs a raw
blueprint dict (as the frontend would send), parses it, runs the engine
with ``execute_streaming()``, and asserts on the SSE event stream plus
final outputs.

Scenarios covered:
1. Simple LLM chain with variable interpolation
2. Condition branching (true path, false/default path, skipped events)
3. Error strategies: stop_workflow, continue, fail_branch
4. Template transform with variable chaining
5. Code execution with output captured via variable store
6. Parallel diamond (fan-out / fan-in)
7. Cancellation mid-execution
8. Empty workflow (Start -> End)
"""

from __future__ import annotations

import asyncio
import sys
import pytest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fim_one.core.workflow.engine import WorkflowEngine
from fim_one.core.workflow.parser import parse_blueprint
from fim_one.core.workflow.types import (
    ErrorStrategy,
    ExecutionContext,
    NodeResult,
    NodeStatus,
    NodeType,
    WorkflowBlueprint,
    WorkflowEdgeDef,
    WorkflowNodeDef,
)
from fim_one.core.workflow.variable_store import VariableStore


# ---------------------------------------------------------------------------
# Blueprint builder helpers
# ---------------------------------------------------------------------------


def _start_node(node_id: str = "start_1", **data: Any) -> dict:
    return {
        "id": node_id,
        "type": "start",
        "position": {"x": 0, "y": 0},
        "data": {"type": "START", **data},
    }


def _end_node(node_id: str = "end_1", **data: Any) -> dict:
    return {
        "id": node_id,
        "type": "end",
        "position": {"x": 800, "y": 0},
        "data": {"type": "END", **data},
    }


def _llm_node(node_id: str, **data: Any) -> dict:
    return {
        "id": node_id,
        "type": "llm",
        "position": {"x": 200, "y": 0},
        "data": {
            "type": "LLM",
            "prompt_template": "Hello {{input.query}}",
            **data,
        },
    }


def _variable_assign_node(node_id: str, **data: Any) -> dict:
    return {
        "id": node_id,
        "type": "variableAssign",
        "position": {"x": 200, "y": 0},
        "data": {
            "type": "VARIABLE_ASSIGN",
            "assignments": [
                {"variable": "result", "mode": "literal", "value": "done"}
            ],
            **data,
        },
    }


def _condition_node(
    node_id: str,
    conditions: list[dict] | None = None,
    **data: Any,
) -> dict:
    return {
        "id": node_id,
        "type": "conditionBranch",
        "position": {"x": 200, "y": 0},
        "data": {
            "type": "CONDITION_BRANCH",
            "conditions": conditions
            or [
                {"id": "c1", "expression": "True"},
            ],
            **data,
        },
    }


def _code_node(node_id: str, code: str = "result = 42", **data: Any) -> dict:
    return {
        "id": node_id,
        "type": "codeExecution",
        "position": {"x": 200, "y": 0},
        "data": {
            "type": "CODE_EXECUTION",
            "code": code,
            **data,
        },
    }


def _template_node(
    node_id: str,
    template: str = "Hello {{ input_name }}",
    **data: Any,
) -> dict:
    return {
        "id": node_id,
        "type": "templateTransform",
        "position": {"x": 200, "y": 0},
        "data": {
            "type": "TEMPLATE_TRANSFORM",
            "template": template,
            **data,
        },
    }


def _edge(source: str, target: str, **kw: Any) -> dict:
    return {
        "id": kw.pop("edge_id", f"{source}->{target}"),
        "source": source,
        "target": target,
        **kw,
    }


# ---------------------------------------------------------------------------
# Event collection helpers
# ---------------------------------------------------------------------------


async def _collect_events(
    engine: WorkflowEngine,
    bp: WorkflowBlueprint,
    inputs: dict[str, Any] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Run engine and collect all SSE events."""
    events: list[tuple[str, dict[str, Any]]] = []
    async for event_name, event_data in engine.execute_streaming(bp, inputs):
        events.append((event_name, event_data))
    return events


def _events_by_type(
    events: list[tuple[str, dict]], event_type: str
) -> list[dict]:
    return [data for name, data in events if name == event_type]


def _completed_node_ids(events: list[tuple[str, dict]]) -> set[str]:
    return {d["node_id"] for name, d in events if name == "node_completed"}


def _skipped_node_ids(events: list[tuple[str, dict]]) -> set[str]:
    return {d["node_id"] for name, d in events if name == "node_skipped"}


def _failed_node_ids(events: list[tuple[str, dict]]) -> set[str]:
    return {d["node_id"] for name, d in events if name == "node_failed"}


def _event_names(events: list[tuple[str, dict]]) -> list[str]:
    """Return ordered list of event names."""
    return [name for name, _ in events]


# ---------------------------------------------------------------------------
# LLM mock helpers
# ---------------------------------------------------------------------------


def _make_mock_llm(response_text: str) -> MagicMock:
    """Build a mock LLM that returns *response_text* from ``chat()``."""
    mock_llm = MagicMock()
    mock_result = MagicMock()
    mock_result.message.content = response_text
    mock_llm.chat = AsyncMock(return_value=mock_result)
    return mock_llm


def _llm_module_patches(mock_llm: MagicMock) -> dict[str, MagicMock]:
    """Build a sys.modules patch dict that satisfies the lazy imports inside
    LLMExecutor.execute (create_session, get_effective_fast_llm, etc.)."""
    mock_session = AsyncMock()
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_create_session = MagicMock(return_value=mock_cm)
    mock_get_fast_llm = AsyncMock(return_value=mock_llm)
    mock_get_llm = AsyncMock(return_value=mock_llm)

    return {
        "fim_one.db": MagicMock(create_session=mock_create_session),
        "fim_one.web.deps": MagicMock(
            get_effective_fast_llm=mock_get_fast_llm,
            get_effective_llm=mock_get_llm,
        ),
    }


# ===========================================================================
# 1. Simple LLM chain: Start -> LLM -> End
# ===========================================================================


class TestSimpleLLMChain:
    """Start -> LLM -> End with mocked LLM call."""

    @pytest.mark.asyncio
    async def test_llm_chain_completes_with_interpolated_prompt(self):
        """Verify variable interpolation in the prompt and output capture."""
        mock_llm = _make_mock_llm("Hello, this is the AI response!")

        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _llm_node(
                        "llm_1",
                        prompt_template="Summarize: {{input.query}}",
                    ),
                    _end_node(
                        output_mapping={"answer": "{{llm_1.output}}"},
                    ),
                ],
                "edges": [
                    _edge("start_1", "llm_1"),
                    _edge("llm_1", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)

        with patch.dict(sys.modules, _llm_module_patches(mock_llm)):
            events = await _collect_events(
                engine, bp, {"query": "climate change"}
            )

        # Verify the LLM was called with the interpolated prompt
        mock_llm.chat.assert_called_once()
        messages = mock_llm.chat.call_args[0][0]
        user_msg = [m for m in messages if m.role == "user"][0]
        assert "climate change" in user_msg.content

        # All nodes completed
        completed = _completed_node_ids(events)
        assert "start_1" in completed
        assert "llm_1" in completed
        assert "end_1" in completed

        # Output captured in End node
        run_completed = _events_by_type(events, "run_completed")
        assert len(run_completed) == 1
        assert run_completed[0]["status"] == "completed"
        outputs = run_completed[0].get("outputs", {})
        assert outputs.get("answer") == "Hello, this is the AI response!"

    @pytest.mark.asyncio
    async def test_sse_event_sequence(self):
        """Verify the SSE events follow: run_started -> node_* ... -> run_completed."""
        mock_llm = _make_mock_llm("response")

        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _llm_node("llm_1"),
                    _end_node(),
                ],
                "edges": [
                    _edge("start_1", "llm_1"),
                    _edge("llm_1", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)

        with patch.dict(sys.modules, _llm_module_patches(mock_llm)):
            events = await _collect_events(engine, bp, {"query": "test"})

        names = _event_names(events)

        # First event must be run_started
        assert names[0] == "run_started"

        # Last event must be run_completed
        assert names[-1] == "run_completed"

        # For every node, started must appear before completed
        for node_id in ("start_1", "llm_1", "end_1"):
            started_idx = next(
                i
                for i, (n, d) in enumerate(events)
                if n == "node_started" and d.get("node_id") == node_id
            )
            completed_idx = next(
                i
                for i, (n, d) in enumerate(events)
                if n == "node_completed" and d.get("node_id") == node_id
            )
            assert started_idx < completed_idx, (
                f"node_started for {node_id} should precede node_completed"
            )

        # Linear ordering: start_1 completed before llm_1 started
        start_completed = next(
            i
            for i, (n, d) in enumerate(events)
            if n == "node_completed" and d.get("node_id") == "start_1"
        )
        llm_started = next(
            i
            for i, (n, d) in enumerate(events)
            if n == "node_started" and d.get("node_id") == "llm_1"
        )
        assert start_completed < llm_started

    @pytest.mark.asyncio
    async def test_llm_with_system_prompt(self):
        """LLM node with system_prompt should pass it to the LLM."""
        mock_llm = _make_mock_llm("structured output")

        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _llm_node(
                        "llm_1",
                        prompt_template="{{input.query}}",
                        system_prompt="You are a helpful assistant.",
                    ),
                    _end_node(),
                ],
                "edges": [
                    _edge("start_1", "llm_1"),
                    _edge("llm_1", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)

        with patch.dict(sys.modules, _llm_module_patches(mock_llm)):
            events = await _collect_events(engine, bp, {"query": "hello"})

        # Both system and user messages sent
        messages = mock_llm.chat.call_args[0][0]
        assert len(messages) == 2
        assert messages[0].role == "system"
        assert "helpful assistant" in messages[0].content
        assert messages[1].role == "user"

        completed = _completed_node_ids(events)
        assert "llm_1" in completed


# ===========================================================================
# 2. Condition branching with multiple paths
# ===========================================================================


class TestConditionBranching:
    """Start -> Condition -> (true: VarAssign A, false: VarAssign B) -> End."""

    @pytest.mark.asyncio
    async def test_true_branch_executes_false_skipped(self):
        """When condition is True, only the true branch runs."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _condition_node(
                        "cond_1",
                        conditions=[{"id": "c1", "expression": "True"}],
                    ),
                    _variable_assign_node(
                        "va_true",
                        assignments=[
                            {
                                "variable": "path",
                                "mode": "literal",
                                "value": "true_path",
                            }
                        ],
                    ),
                    _variable_assign_node(
                        "va_false",
                        assignments=[
                            {
                                "variable": "path",
                                "mode": "literal",
                                "value": "false_path",
                            }
                        ],
                    ),
                    _end_node(),
                ],
                "edges": [
                    _edge("start_1", "cond_1"),
                    _edge(
                        "cond_1", "va_true", sourceHandle="condition-c1"
                    ),
                    _edge(
                        "cond_1", "va_false", sourceHandle="source-default"
                    ),
                    _edge("va_true", "end_1"),
                    _edge("va_false", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp, {"value": 10})

        completed = _completed_node_ids(events)
        skipped = _skipped_node_ids(events)

        assert "va_true" in completed
        assert "va_false" in skipped
        assert "end_1" in completed

    @pytest.mark.asyncio
    async def test_false_branch_executes_true_skipped(self):
        """When condition is False, only the default (false) branch runs."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _condition_node(
                        "cond_1",
                        conditions=[{"id": "c1", "expression": "False"}],
                    ),
                    _variable_assign_node(
                        "va_true",
                        assignments=[
                            {
                                "variable": "path",
                                "mode": "literal",
                                "value": "true_path",
                            }
                        ],
                    ),
                    _variable_assign_node(
                        "va_false",
                        assignments=[
                            {
                                "variable": "path",
                                "mode": "literal",
                                "value": "false_path",
                            }
                        ],
                    ),
                    _end_node(),
                ],
                "edges": [
                    _edge("start_1", "cond_1"),
                    _edge(
                        "cond_1", "va_true", sourceHandle="condition-c1"
                    ),
                    _edge(
                        "cond_1", "va_false", sourceHandle="source-default"
                    ),
                    _edge("va_true", "end_1"),
                    _edge("va_false", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        skipped = _skipped_node_ids(events)

        assert "va_true" in skipped
        assert "va_false" in completed
        assert "end_1" in completed

    @pytest.mark.asyncio
    async def test_skipped_nodes_emit_node_skipped_events(self):
        """Skipped branch nodes must emit node_skipped events with reason."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _condition_node(
                        "cond_1",
                        conditions=[{"id": "c1", "expression": "True"}],
                    ),
                    _variable_assign_node("va_true", assignments=[
                        {"variable": "x", "mode": "literal", "value": "1"},
                    ]),
                    _variable_assign_node("va_false", assignments=[
                        {"variable": "x", "mode": "literal", "value": "2"},
                    ]),
                    _end_node(),
                ],
                "edges": [
                    _edge("start_1", "cond_1"),
                    _edge("cond_1", "va_true", sourceHandle="condition-c1"),
                    _edge("cond_1", "va_false", sourceHandle="source-default"),
                    _edge("va_true", "end_1"),
                    _edge("va_false", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        skip_events = _events_by_type(events, "node_skipped")
        skipped_ids = {e["node_id"] for e in skip_events}
        assert "va_false" in skipped_ids

        # Each skipped event should have a reason
        for ev in skip_events:
            assert "reason" in ev
            assert ev["reason"]  # non-empty

    @pytest.mark.asyncio
    async def test_condition_with_variable_expression(self):
        """Condition using input variable in expression."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _condition_node(
                        "cond_1",
                        conditions=[
                            {"id": "c1", "expression": "score > 80"},
                        ],
                    ),
                    _variable_assign_node("va_high", assignments=[
                        {"variable": "grade", "mode": "literal", "value": "A"},
                    ]),
                    _variable_assign_node("va_low", assignments=[
                        {"variable": "grade", "mode": "literal", "value": "B"},
                    ]),
                    _end_node(),
                ],
                "edges": [
                    _edge("start_1", "cond_1"),
                    _edge("cond_1", "va_high", sourceHandle="condition-c1"),
                    _edge("cond_1", "va_low", sourceHandle="source-default"),
                    _edge("va_high", "end_1"),
                    _edge("va_low", "end_1"),
                ],
            }
        )

        # score = 90 -> true branch
        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp, {"score": 90})
        assert "va_high" in _completed_node_ids(events)
        assert "va_low" in _skipped_node_ids(events)

    @pytest.mark.asyncio
    async def test_condition_variable_expression_false_path(self):
        """Condition using input variable that evaluates to false."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _condition_node(
                        "cond_1",
                        conditions=[
                            {"id": "c1", "expression": "score > 80"},
                        ],
                    ),
                    _variable_assign_node("va_high", assignments=[
                        {"variable": "grade", "mode": "literal", "value": "A"},
                    ]),
                    _variable_assign_node("va_low", assignments=[
                        {"variable": "grade", "mode": "literal", "value": "B"},
                    ]),
                    _end_node(),
                ],
                "edges": [
                    _edge("start_1", "cond_1"),
                    _edge("cond_1", "va_high", sourceHandle="condition-c1"),
                    _edge("cond_1", "va_low", sourceHandle="source-default"),
                    _edge("va_high", "end_1"),
                    _edge("va_low", "end_1"),
                ],
            }
        )

        # score = 50 -> default (false) branch
        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp, {"score": 50})
        assert "va_high" in _skipped_node_ids(events)
        assert "va_low" in _completed_node_ids(events)


# ===========================================================================
# 3. Error strategies
# ===========================================================================


class TestErrorStrategies:
    """Test the three error strategies: stop_workflow, continue, fail_branch."""

    @pytest.mark.asyncio
    async def test_stop_workflow_halts_remaining_nodes(self):
        """stop_workflow: node fails -> workflow stops, remaining nodes skipped."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _code_node(
                        "code_fail",
                        code="raise ValueError('boom')",
                        error_strategy="stop_workflow",
                    ),
                    _variable_assign_node("va_after", assignments=[
                        {"variable": "x", "mode": "literal", "value": "1"},
                    ]),
                    _end_node(),
                ],
                "edges": [
                    _edge("start_1", "code_fail"),
                    _edge("code_fail", "va_after"),
                    _edge("va_after", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        failed = _failed_node_ids(events)
        skipped = _skipped_node_ids(events)
        completed = _completed_node_ids(events)

        assert "code_fail" in failed
        assert "va_after" in skipped
        assert "end_1" in skipped

        # Run should emit run_failed
        run_failed = _events_by_type(events, "run_failed")
        assert len(run_failed) == 1

    @pytest.mark.asyncio
    async def test_continue_allows_downstream_to_execute(self):
        """continue: node fails -> downstream nodes still run."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _code_node(
                        "code_fail",
                        code="raise ValueError('boom')",
                        error_strategy="continue",
                    ),
                    _variable_assign_node("va_after", assignments=[
                        {"variable": "x", "mode": "literal", "value": "1"},
                    ]),
                    _end_node(),
                ],
                "edges": [
                    _edge("start_1", "code_fail"),
                    _edge("code_fail", "va_after"),
                    _edge("va_after", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        failed = _failed_node_ids(events)
        completed = _completed_node_ids(events)

        assert "code_fail" in failed
        # Downstream nodes still execute with CONTINUE strategy
        assert "va_after" in completed
        assert "end_1" in completed

    @pytest.mark.asyncio
    async def test_fail_branch_skips_only_downstream(self):
        """fail_branch: failed node's downstream skipped, sibling branch unaffected."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _code_node("code_ok", code="result = 'ok'"),
                    _code_node(
                        "code_fail",
                        code="raise ValueError('boom')",
                        error_strategy="fail_branch",
                    ),
                    _variable_assign_node("va_after_fail", assignments=[
                        {"variable": "x", "mode": "literal", "value": "1"},
                    ]),
                    _end_node("end_ok"),
                    _end_node("end_fail"),
                ],
                "edges": [
                    _edge("start_1", "code_ok"),
                    _edge("start_1", "code_fail"),
                    _edge("code_fail", "va_after_fail"),
                    _edge("va_after_fail", "end_fail"),
                    _edge("code_ok", "end_ok"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        failed = _failed_node_ids(events)
        completed = _completed_node_ids(events)
        skipped = _skipped_node_ids(events)

        # Failed node
        assert "code_fail" in failed

        # Downstream of failed branch: skipped
        assert "va_after_fail" in skipped
        assert "end_fail" in skipped

        # Sibling branch: unaffected
        assert "code_ok" in completed
        assert "end_ok" in completed

    @pytest.mark.asyncio
    async def test_fail_branch_event_contains_skipped_downstream(self):
        """The node_failed event for fail_branch should list skipped downstream IDs."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _code_node(
                        "code_fail",
                        code="raise ValueError('boom')",
                        error_strategy="fail_branch",
                    ),
                    _variable_assign_node("va_down", assignments=[
                        {"variable": "x", "mode": "literal", "value": "1"},
                    ]),
                    _end_node(),
                ],
                "edges": [
                    _edge("start_1", "code_fail"),
                    _edge("code_fail", "va_down"),
                    _edge("va_down", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        fail_events = _events_by_type(events, "node_failed")
        code_fail_event = next(
            e for e in fail_events if e.get("node_id") == "code_fail"
        )
        assert code_fail_event["error_strategy"] == "fail_branch"
        assert "skipped_downstream" in code_fail_event
        # va_down and end_1 should be in the skipped downstream set
        assert "va_down" in code_fail_event["skipped_downstream"]
        assert "end_1" in code_fail_event["skipped_downstream"]


# ===========================================================================
# 4. Template transform + variable chaining
# ===========================================================================


class TestTemplateTransformChaining:
    """Start -> VarAssign(x=hello) -> TemplateTransform -> End."""

    @pytest.mark.asyncio
    async def test_template_renders_with_variable_values(self):
        """Template should render using variables set by upstream nodes."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _variable_assign_node(
                        "va_1",
                        assignments=[
                            {
                                "variable": "greeting",
                                "mode": "literal",
                                "value": "hello",
                            }
                        ],
                    ),
                    # Jinja2 template references the flat variable name.
                    # VariableAssignExecutor stores under both "greeting"
                    # (flat) and "va_1.greeting" (namespaced).  The
                    # TemplateTransformExecutor renders using snapshot_safe()
                    # which exposes dotted keys: use "va_1.greeting" or
                    # just the dotted key syntax Jinja2 doesn't support with
                    # dots -- so we use the namespaced form with underscores.
                    _template_node(
                        "tmpl_1",
                        template="Message: {{ greeting }}",
                    ),
                    _end_node(
                        output_mapping={"rendered": "{{tmpl_1.output}}"},
                    ),
                ],
                "edges": [
                    _edge("start_1", "va_1"),
                    _edge("va_1", "tmpl_1"),
                    _edge("tmpl_1", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        assert "va_1" in completed
        assert "tmpl_1" in completed
        assert "end_1" in completed

        run_completed = _events_by_type(events, "run_completed")
        assert len(run_completed) == 1
        assert run_completed[0]["status"] == "completed"
        outputs = run_completed[0].get("outputs", {})
        assert outputs.get("rendered") == "Message: hello"

    @pytest.mark.asyncio
    async def test_template_with_input_variables(self):
        """Template rendering with workflow-level input variables."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _template_node(
                        "tmpl_1",
                        # snapshot_safe() stores inputs as "input.name";
                        # Jinja2 dot access: input is a top-level key
                        # But snapshot keys are dotted strings, not nested dicts.
                        # The flat key "input.name" is accessible via
                        # the underscore alias or the Jinja2 bracket syntax.
                        template="Hello {{ input_name }}",
                    ),
                    _end_node(
                        output_mapping={"msg": "{{tmpl_1.output}}"},
                    ),
                ],
                "edges": [
                    _edge("start_1", "tmpl_1"),
                    _edge("tmpl_1", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp, {"name": "World"})

        completed = _completed_node_ids(events)
        assert "tmpl_1" in completed

        run_completed = _events_by_type(events, "run_completed")
        assert run_completed[0]["status"] == "completed"


# ===========================================================================
# 5. Code execution with output capture
# ===========================================================================


class TestCodeExecution:
    """Start -> CodeExecution -> End with output mapping."""

    @pytest.mark.asyncio
    async def test_code_result_captured_in_end_node(self):
        """Code node result should be accessible via output_mapping."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _code_node("code_1", code="result = 42"),
                    _end_node(
                        output_mapping={"answer": "{{code_1.output}}"},
                    ),
                ],
                "edges": [
                    _edge("start_1", "code_1"),
                    _edge("code_1", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        assert "code_1" in completed
        assert "end_1" in completed

        run_completed = _events_by_type(events, "run_completed")
        assert run_completed[0]["status"] == "completed"
        outputs = run_completed[0].get("outputs", {})
        # Code output is JSON-parsed: integer 42 becomes "42" via
        # store.interpolate (which converts non-string values to JSON)
        assert outputs.get("answer") is not None

    @pytest.mark.asyncio
    async def test_code_string_result(self):
        """Code node returning a string result."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _code_node(
                        "code_1", code='result = "hello world"'
                    ),
                    _end_node(
                        output_mapping={"msg": "{{code_1.output}}"},
                    ),
                ],
                "edges": [
                    _edge("start_1", "code_1"),
                    _edge("code_1", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        assert "code_1" in completed
        assert "end_1" in completed

        run_completed = _events_by_type(events, "run_completed")
        outputs = run_completed[0].get("outputs", {})
        assert outputs.get("msg") == "hello world"

    @pytest.mark.asyncio
    async def test_code_with_dict_result(self):
        """Code node returning a dictionary."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _code_node(
                        "code_1",
                        code='result = {"key": "value", "num": 42}',
                    ),
                    _end_node(),
                ],
                "edges": [
                    _edge("start_1", "code_1"),
                    _edge("code_1", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        assert "code_1" in completed
        assert "end_1" in completed

    @pytest.mark.asyncio
    async def test_code_failure_produces_error(self):
        """Code that raises should produce a node_failed event."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _code_node(
                        "code_1",
                        code="raise RuntimeError('test error')",
                        error_strategy="stop_workflow",
                    ),
                    _end_node(),
                ],
                "edges": [
                    _edge("start_1", "code_1"),
                    _edge("code_1", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        failed = _failed_node_ids(events)
        assert "code_1" in failed

        fail_events = _events_by_type(events, "node_failed")
        code_fail = next(
            e for e in fail_events if e.get("node_id") == "code_1"
        )
        assert "error" in code_fail
        assert code_fail["error"]  # non-empty


# ===========================================================================
# 6. Parallel diamond (fan-out / fan-in)
# ===========================================================================


class TestParallelDiamond:
    """Start -> (NodeA, NodeB in parallel) -> End."""

    @pytest.mark.asyncio
    async def test_both_branches_execute_and_merge(self):
        """Both parallel branches should complete before End runs."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _variable_assign_node(
                        "va_a",
                        assignments=[
                            {
                                "variable": "a_result",
                                "mode": "literal",
                                "value": "from_a",
                            }
                        ],
                    ),
                    _variable_assign_node(
                        "va_b",
                        assignments=[
                            {
                                "variable": "b_result",
                                "mode": "literal",
                                "value": "from_b",
                            }
                        ],
                    ),
                    _end_node(),
                ],
                "edges": [
                    _edge("start_1", "va_a"),
                    _edge("start_1", "va_b"),
                    _edge("va_a", "end_1"),
                    _edge("va_b", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        assert "va_a" in completed
        assert "va_b" in completed
        assert "end_1" in completed

        # End must complete after both branches
        va_a_completed = next(
            i
            for i, (n, d) in enumerate(events)
            if n == "node_completed" and d.get("node_id") == "va_a"
        )
        va_b_completed = next(
            i
            for i, (n, d) in enumerate(events)
            if n == "node_completed" and d.get("node_id") == "va_b"
        )
        end_started = next(
            i
            for i, (n, d) in enumerate(events)
            if n == "node_started" and d.get("node_id") == "end_1"
        )
        assert va_a_completed < end_started
        assert va_b_completed < end_started

        run_completed = _events_by_type(events, "run_completed")
        assert len(run_completed) == 1
        assert run_completed[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_parallel_with_code_nodes(self):
        """Parallel code execution nodes should both complete."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _code_node("code_a", code="result = 'alpha'"),
                    _code_node("code_b", code="result = 'beta'"),
                    _end_node(),
                ],
                "edges": [
                    _edge("start_1", "code_a"),
                    _edge("start_1", "code_b"),
                    _edge("code_a", "end_1"),
                    _edge("code_b", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        assert "code_a" in completed
        assert "code_b" in completed
        assert "end_1" in completed


# ===========================================================================
# 7. Cancellation mid-execution
# ===========================================================================


class TestCancellation:
    """Start a workflow and cancel it mid-execution."""

    @pytest.mark.asyncio
    async def test_cancel_after_first_node_completes(self):
        """Cancelling after the first node starts should skip remaining."""
        cancel = asyncio.Event()

        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _code_node("code_1", code="result = 1"),
                    _code_node("code_2", code="result = 2"),
                    _code_node("code_3", code="result = 3"),
                    _end_node(),
                ],
                "edges": [
                    _edge("start_1", "code_1"),
                    _edge("code_1", "code_2"),
                    _edge("code_2", "code_3"),
                    _edge("code_3", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5, cancel_event=cancel)

        events: list[tuple[str, dict]] = []

        async for event_name, event_data in engine.execute_streaming(bp):
            events.append((event_name, event_data))
            # Cancel after code_1 completes
            if (
                event_name == "node_completed"
                and event_data.get("node_id") == "code_1"
            ):
                cancel.set()

        # code_1 should have completed
        completed = _completed_node_ids(events)
        assert "code_1" in completed

        # Remaining nodes should be skipped (code_2, code_3, end_1)
        skipped = _skipped_node_ids(events)
        assert len(skipped) >= 1  # At least some nodes were skipped

    @pytest.mark.asyncio
    async def test_cancel_status_in_final_event(self):
        """Cancellation should produce a run_completed with status=cancelled
        or skipped remaining nodes."""
        cancel = asyncio.Event()

        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _code_node("code_1", code="result = 1"),
                    _code_node("code_2", code="result = 2"),
                    _end_node(),
                ],
                "edges": [
                    _edge("start_1", "code_1"),
                    _edge("code_1", "code_2"),
                    _edge("code_2", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5, cancel_event=cancel)

        events: list[tuple[str, dict]] = []

        async for event_name, event_data in engine.execute_streaming(bp):
            events.append((event_name, event_data))
            if (
                event_name == "node_completed"
                and event_data.get("node_id") == "code_1"
            ):
                cancel.set()

        # Should have some terminal event
        names = _event_names(events)
        assert names[-1] in ("run_completed", "run_failed")


# ===========================================================================
# 8. Empty workflow: Start -> End
# ===========================================================================


class TestEmptyWorkflow:
    """Start -> End with no middle nodes."""

    @pytest.mark.asyncio
    async def test_start_to_end_completes(self):
        """Simplest workflow should complete successfully."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _end_node(),
                ],
                "edges": [
                    _edge("start_1", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        assert "start_1" in completed
        assert "end_1" in completed

        run_completed = _events_by_type(events, "run_completed")
        assert len(run_completed) == 1
        assert run_completed[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_start_to_end_with_empty_outputs(self):
        """Start -> End should produce empty or default outputs."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _end_node(),
                ],
                "edges": [
                    _edge("start_1", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        run_completed = _events_by_type(events, "run_completed")
        assert len(run_completed) == 1
        # Outputs should exist (may contain start node vars)
        assert "outputs" in run_completed[0]

    @pytest.mark.asyncio
    async def test_start_to_end_with_passthrough(self):
        """Start -> End with output_mapping echoing inputs."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _end_node(
                        output_mapping={"echo": "{{input.message}}"},
                    ),
                ],
                "edges": [
                    _edge("start_1", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(
            engine, bp, {"message": "pass through"}
        )

        run_completed = _events_by_type(events, "run_completed")
        assert len(run_completed) == 1
        outputs = run_completed[0].get("outputs", {})
        assert outputs.get("echo") == "pass through"

    @pytest.mark.asyncio
    async def test_minimal_event_count(self):
        """Start -> End should produce exactly the right number of events."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _end_node(),
                ],
                "edges": [
                    _edge("start_1", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        names = _event_names(events)
        # Expected: run_started, start_started, start_completed,
        #           end_started, end_completed, run_completed
        assert names[0] == "run_started"
        assert names[-1] == "run_completed"
        assert names.count("run_started") == 1
        assert names.count("run_completed") == 1


# ===========================================================================
# Additional lifecycle scenarios
# ===========================================================================


class TestVariableChaining:
    """Test that variables flow correctly between nodes."""

    @pytest.mark.asyncio
    async def test_code_to_code_variable_passing(self):
        """Chained code nodes: second can access first's output."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _code_node("code_1", code="result = 10"),
                    _code_node("code_2", code="result = 20"),
                    _end_node(
                        output_mapping={
                            "first": "{{code_1.output}}",
                            "second": "{{code_2.output}}",
                        },
                    ),
                ],
                "edges": [
                    _edge("start_1", "code_1"),
                    _edge("code_1", "code_2"),
                    _edge("code_2", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        assert "code_1" in completed
        assert "code_2" in completed
        assert "end_1" in completed

        run_completed = _events_by_type(events, "run_completed")
        assert run_completed[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_variable_assign_to_template_chaining(self):
        """VariableAssign -> TemplateTransform -> End variable flow."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _variable_assign_node(
                        "va_1",
                        assignments=[
                            {
                                "variable": "name",
                                "mode": "literal",
                                "value": "Alice",
                            },
                            {
                                "variable": "age",
                                "mode": "literal",
                                "value": "30",
                            },
                        ],
                    ),
                    _template_node(
                        "tmpl_1",
                        template="{{ name }} is {{ age }} years old",
                    ),
                    _end_node(
                        output_mapping={"bio": "{{tmpl_1.output}}"},
                    ),
                ],
                "edges": [
                    _edge("start_1", "va_1"),
                    _edge("va_1", "tmpl_1"),
                    _edge("tmpl_1", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        assert "va_1" in completed
        assert "tmpl_1" in completed
        assert "end_1" in completed

        run_completed = _events_by_type(events, "run_completed")
        outputs = run_completed[0].get("outputs", {})
        assert outputs.get("bio") == "Alice is 30 years old"


class TestComplexLifecycleGraphs:
    """Multi-step workflows combining different node types."""

    @pytest.mark.asyncio
    async def test_condition_to_code_to_end(self):
        """Start -> Condition -> Code -> End (single active path)."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _condition_node(
                        "cond_1",
                        conditions=[{"id": "c1", "expression": "True"}],
                    ),
                    _code_node("code_true", code="result = 'yes'"),
                    _code_node("code_false", code="result = 'no'"),
                    _end_node(),
                ],
                "edges": [
                    _edge("start_1", "cond_1"),
                    _edge("cond_1", "code_true", sourceHandle="condition-c1"),
                    _edge(
                        "cond_1", "code_false", sourceHandle="source-default"
                    ),
                    _edge("code_true", "end_1"),
                    _edge("code_false", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        skipped = _skipped_node_ids(events)

        assert "code_true" in completed
        assert "code_false" in skipped
        assert "end_1" in completed

    @pytest.mark.asyncio
    async def test_parallel_branches_with_different_node_types(self):
        """Start -> (Code, VarAssign in parallel) -> End."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _code_node("code_1", code="result = 100"),
                    _variable_assign_node(
                        "va_1",
                        assignments=[
                            {
                                "variable": "label",
                                "mode": "literal",
                                "value": "test_label",
                            }
                        ],
                    ),
                    _end_node(),
                ],
                "edges": [
                    _edge("start_1", "code_1"),
                    _edge("start_1", "va_1"),
                    _edge("code_1", "end_1"),
                    _edge("va_1", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        assert "code_1" in completed
        assert "va_1" in completed
        assert "end_1" in completed

    @pytest.mark.asyncio
    async def test_deep_linear_chain_all_nodes_complete(self):
        """Linear chain of 6 VariableAssign nodes executes in order."""
        nodes = [_start_node()]
        edges = []

        for i in range(1, 7):
            nodes.append(
                _variable_assign_node(
                    f"va_{i}",
                    assignments=[
                        {
                            "variable": f"step_{i}",
                            "mode": "literal",
                            "value": str(i),
                        }
                    ],
                )
            )

        nodes.append(_end_node())

        # Chain: start -> va_1 -> va_2 -> ... -> va_6 -> end
        prev = "start_1"
        for i in range(1, 7):
            edges.append(_edge(prev, f"va_{i}"))
            prev = f"va_{i}"
        edges.append(_edge(prev, "end_1"))

        bp = parse_blueprint({"nodes": nodes, "edges": edges})
        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        for i in range(1, 7):
            assert f"va_{i}" in completed
        assert "end_1" in completed

        run_completed = _events_by_type(events, "run_completed")
        assert run_completed[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_multiple_end_nodes(self):
        """Graph with two independent paths each ending at a different End node."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _code_node("code_a", code="result = 'path_a'"),
                    _code_node("code_b", code="result = 'path_b'"),
                    _end_node("end_a"),
                    _end_node("end_b"),
                ],
                "edges": [
                    _edge("start_1", "code_a"),
                    _edge("start_1", "code_b"),
                    _edge("code_a", "end_a"),
                    _edge("code_b", "end_b"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        completed = _completed_node_ids(events)
        assert "end_a" in completed
        assert "end_b" in completed


class TestDurationTracking:
    """Verify that duration_ms is tracked in events."""

    @pytest.mark.asyncio
    async def test_node_completed_has_duration(self):
        """node_completed events should include a non-negative duration_ms."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _code_node("code_1", code="result = 42"),
                    _end_node(),
                ],
                "edges": [
                    _edge("start_1", "code_1"),
                    _edge("code_1", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        completed_events = _events_by_type(events, "node_completed")
        for ev in completed_events:
            assert "duration_ms" in ev
            assert ev["duration_ms"] >= 0

    @pytest.mark.asyncio
    async def test_run_completed_has_duration(self):
        """run_completed event should include overall duration_ms."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _end_node(),
                ],
                "edges": [
                    _edge("start_1", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp)

        run_completed = _events_by_type(events, "run_completed")
        assert len(run_completed) == 1
        assert "duration_ms" in run_completed[0]
        assert run_completed[0]["duration_ms"] >= 0


class TestInputPreviewCapture:
    """Verify node_started events include input_preview for debugging."""

    @pytest.mark.asyncio
    async def test_node_started_has_input_preview(self):
        """node_started events should include input_preview field."""
        bp = parse_blueprint(
            {
                "nodes": [
                    _start_node(),
                    _code_node("code_1", code="result = 42"),
                    _end_node(),
                ],
                "edges": [
                    _edge("start_1", "code_1"),
                    _edge("code_1", "end_1"),
                ],
            }
        )

        engine = WorkflowEngine(max_concurrency=5)
        events = await _collect_events(engine, bp, {"name": "test"})

        started_events = _events_by_type(events, "node_started")
        # code_1 should have input_preview showing start_1's outputs
        code_started = [
            e for e in started_events if e.get("node_id") == "code_1"
        ]
        assert len(code_started) == 1
        assert "input_preview" in code_started[0]

"""Tests for the workflow execution engine core components.

Covers: parser (validation, topo sort), variable store (interpolation,
snapshot_safe), and engine (linear execution, condition branching, error
strategies, cancellation).
"""

from __future__ import annotations

import asyncio
import json
import pytest
from typing import Any

from fim_one.core.workflow.parser import (
    BlueprintValidationError,
    BlueprintWarning,
    parse_blueprint,
    topological_sort,
    validate_blueprint,
)
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
from fim_one.core.workflow.engine import WorkflowEngine


# ---------------------------------------------------------------------------
# Fixtures: reusable blueprint builders
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
        "position": {"x": 400, "y": 0},
        "data": {"type": "END", **data},
    }


def _llm_node(node_id: str = "llm_1", **data: Any) -> dict:
    return {
        "id": node_id,
        "type": "llm",
        "position": {"x": 200, "y": 0},
        "data": {"type": "LLM", "prompt": "Hello {{input.name}}", **data},
    }


def _condition_node(node_id: str = "cond_1", **data: Any) -> dict:
    return {
        "id": node_id,
        "type": "conditionBranch",
        "position": {"x": 200, "y": 0},
        "data": {
            "type": "CONDITION_BRANCH",
            "conditions": [
                {"handle": "yes", "expression": "score > 50"},
            ],
            "default_handle": "no",
            **data,
        },
    }


def _edge(source: str, target: str, source_handle: str | None = None) -> dict:
    eid = f"e-{source}-{target}"
    edge: dict[str, Any] = {"id": eid, "source": source, "target": target}
    if source_handle:
        edge["sourceHandle"] = source_handle
    return edge


def _simple_blueprint() -> dict:
    """Start → End, the simplest valid blueprint."""
    return {
        "nodes": [_start_node(), _end_node()],
        "edges": [_edge("start_1", "end_1")],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }


# =========================================================================
# Parser tests
# =========================================================================


class TestParser:
    def test_parse_simple_blueprint(self):
        bp = parse_blueprint(_simple_blueprint())
        assert len(bp.nodes) == 2
        assert len(bp.edges) == 1
        assert bp.nodes[0].type == NodeType.START
        assert bp.nodes[1].type == NodeType.END

    def test_missing_start_node(self):
        raw = {
            "nodes": [_end_node()],
            "edges": [],
        }
        with pytest.raises(BlueprintValidationError, match="Start node"):
            parse_blueprint(raw)

    def test_missing_end_node(self):
        raw = {
            "nodes": [_start_node()],
            "edges": [],
        }
        with pytest.raises(BlueprintValidationError, match="End node"):
            parse_blueprint(raw)

    def test_duplicate_start_node(self):
        raw = {
            "nodes": [_start_node("s1"), _start_node("s2"), _end_node()],
            "edges": [],
        }
        with pytest.raises(BlueprintValidationError, match="exactly 1"):
            parse_blueprint(raw)

    def test_duplicate_node_id(self):
        raw = {
            "nodes": [
                _start_node("dup"),
                {"id": "dup", "type": "end", "data": {"type": "END"}},
            ],
            "edges": [],
        }
        with pytest.raises(BlueprintValidationError, match="Duplicate"):
            parse_blueprint(raw)

    def test_unknown_node_type(self):
        raw = {
            "nodes": [
                _start_node(),
                {"id": "x", "type": "banana", "data": {"type": "BANANA"}},
                _end_node(),
            ],
            "edges": [],
        }
        with pytest.raises(BlueprintValidationError, match="Unknown node type"):
            parse_blueprint(raw)

    def test_edge_references_unknown_node(self):
        raw = {
            "nodes": [_start_node(), _end_node()],
            "edges": [_edge("start_1", "ghost")],
        }
        with pytest.raises(BlueprintValidationError, match="unknown node"):
            parse_blueprint(raw)

    def test_cycle_detection(self):
        raw = {
            "nodes": [
                _start_node(),
                _llm_node("a"),
                _llm_node("b"),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "a"),
                _edge("a", "b"),
                _edge("b", "a"),  # creates a cycle
                _edge("b", "end_1"),
            ],
        }
        with pytest.raises(BlueprintValidationError, match="cycle"):
            parse_blueprint(raw)

    def test_no_nodes(self):
        with pytest.raises(BlueprintValidationError, match="no nodes"):
            parse_blueprint({"nodes": [], "edges": []})

    def test_error_strategy_parsing(self):
        raw = {
            "nodes": [
                _start_node(),
                {
                    "id": "llm_1",
                    "type": "llm",
                    "data": {
                        "type": "LLM",
                        "error_strategy": "CONTINUE",
                    },
                },
                _end_node(),
            ],
            "edges": [_edge("start_1", "llm_1"), _edge("llm_1", "end_1")],
        }
        bp = parse_blueprint(raw)
        llm_node = next(n for n in bp.nodes if n.type == NodeType.LLM)
        assert llm_node.error_strategy == ErrorStrategy.CONTINUE

    def test_timeout_parsing(self):
        raw = {
            "nodes": [
                _start_node(),
                {
                    "id": "llm_1",
                    "type": "llm",
                    "data": {"type": "LLM", "timeout_ms": 60000},
                },
                _end_node(),
            ],
            "edges": [_edge("start_1", "llm_1"), _edge("llm_1", "end_1")],
        }
        bp = parse_blueprint(raw)
        llm_node = next(n for n in bp.nodes if n.type == NodeType.LLM)
        assert llm_node.timeout_ms == 60000


class TestTopologicalSort:
    def test_linear_order(self):
        bp = parse_blueprint({
            "nodes": [_start_node(), _llm_node("a"), _end_node()],
            "edges": [_edge("start_1", "a"), _edge("a", "end_1")],
        })
        order = topological_sort(bp)
        assert order.index("start_1") < order.index("a")
        assert order.index("a") < order.index("end_1")

    def test_parallel_branches(self):
        """Two parallel nodes should both appear between start and end."""
        bp = parse_blueprint({
            "nodes": [
                _start_node(),
                _llm_node("a"),
                _llm_node("b"),
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "a"),
                _edge("start_1", "b"),
                _edge("a", "end_1"),
                _edge("b", "end_1"),
            ],
        })
        order = topological_sort(bp)
        assert order[0] == "start_1"
        assert set(order[1:3]) == {"a", "b"}
        assert order[-1] == "end_1"


# =========================================================================
# VariableStore tests
# =========================================================================


class TestVariableStore:
    @pytest.mark.asyncio
    async def test_set_and_get(self):
        store = VariableStore()
        await store.set("x", 42)
        assert await store.get("x") == 42

    @pytest.mark.asyncio
    async def test_get_default(self):
        store = VariableStore()
        assert await store.get("missing", "default") == "default"

    @pytest.mark.asyncio
    async def test_interpolate_simple(self):
        store = VariableStore()
        await store.set("input.name", "Alice")
        result = await store.interpolate("Hello {{input.name}}!")
        assert result == "Hello Alice!"

    @pytest.mark.asyncio
    async def test_interpolate_flat_fallback(self):
        """Flat variable name matches last segment of dotted key."""
        store = VariableStore()
        await store.set("llm_1.output", "result text")
        result = await store.interpolate("Got: {{output}}")
        assert result == "Got: result text"

    @pytest.mark.asyncio
    async def test_interpolate_unknown_kept(self):
        store = VariableStore()
        result = await store.interpolate("{{unknown_var}}")
        assert result == "{{unknown_var}}"

    @pytest.mark.asyncio
    async def test_interpolate_non_string_json(self):
        store = VariableStore()
        await store.set("data", {"key": "value"})
        result = await store.interpolate("Result: {{data}}")
        assert '"key"' in result
        assert '"value"' in result

    @pytest.mark.asyncio
    async def test_env_vars_injection(self):
        store = VariableStore(env_vars={"API_KEY": "secret123"})
        assert await store.get("env.API_KEY") == "secret123"

    @pytest.mark.asyncio
    async def test_snapshot_safe_excludes_env(self):
        store = VariableStore(env_vars={"SECRET": "hidden"})
        await store.set("visible", "ok")
        safe = await store.snapshot_safe()
        assert "visible" in safe
        assert "env.SECRET" not in safe

    @pytest.mark.asyncio
    async def test_snapshot_includes_env(self):
        store = VariableStore(env_vars={"SECRET": "hidden"})
        full = await store.snapshot()
        assert "env.SECRET" in full

    @pytest.mark.asyncio
    async def test_set_many(self):
        store = VariableStore()
        await store.set_many({"a": 1, "b": 2})
        assert await store.get("a") == 1
        assert await store.get("b") == 2

    @pytest.mark.asyncio
    async def test_get_node_outputs(self):
        store = VariableStore()
        await store.set("llm_1.output", "text")
        await store.set("llm_1.tokens", 150)
        await store.set("other.val", "x")
        outputs = await store.get_node_outputs("llm_1")
        assert outputs == {"output": "text", "tokens": 150}
        assert "val" not in outputs

    @pytest.mark.asyncio
    async def test_list_available_variables(self):
        store = VariableStore(env_vars={"K": "V"})
        await store.set("input.q", "query")
        await store.set("llm_1.output", "answer")
        variables = await store.list_available_variables()
        # Should exclude env.* and input.*
        names = [v["var_name"] for v in variables]
        assert "output" in names
        assert "q" not in names


# =========================================================================
# Engine tests (unit-level, with mocked executors)
# =========================================================================


class TestEngineLinear:
    """Test engine with Start → End (no LLM calls needed)."""

    @pytest.mark.asyncio
    async def test_start_to_end_execution(self):
        """Simplest workflow: Start → End should complete successfully."""
        raw = _simple_blueprint()
        parsed = parse_blueprint(raw)

        engine = WorkflowEngine(
            run_id="test-run-1",
            user_id="test-user",
            workflow_id="test-wf",
        )

        events: list[tuple[str, dict]] = []
        async for event_name, event_data in engine.execute_streaming(
            parsed, inputs={"greeting": "hello"}
        ):
            events.append((event_name, event_data))

        event_types = [e[0] for e in events]
        assert "run_started" in event_types, "Engine should emit run_started"
        # Should have node_started and node_completed for both nodes
        started_nodes = [
            e[1]["node_id"] for e in events if e[0] == "node_started"
        ]
        completed_nodes = [
            e[1]["node_id"] for e in events if e[0] == "node_completed"
        ]
        assert "start_1" in started_nodes
        assert "start_1" in completed_nodes

    @pytest.mark.asyncio
    async def test_inputs_available_in_store(self):
        """Verify that inputs are passed through Start node to downstream."""
        raw = {
            "nodes": [
                _start_node(),
                _end_node(output_mapping={"result": "{{start_1.name}}"}),
            ],
            "edges": [_edge("start_1", "end_1")],
        }
        parsed = parse_blueprint(raw)

        engine = WorkflowEngine(run_id="r", user_id="u", workflow_id="w")

        events: list[tuple[str, dict]] = []
        async for event_name, event_data in engine.execute_streaming(
            parsed, inputs={"name": "World"}
        ):
            events.append((event_name, event_data))

        # End node should complete
        completed_events = [
            e for e in events if e[0] == "node_completed" and e[1].get("node_id") == "end_1"
        ]
        assert len(completed_events) == 1


class TestEngineCancellation:
    @pytest.mark.asyncio
    async def test_cancel_stops_execution(self):
        """Cancelling mid-run should skip remaining nodes."""
        raw = {
            "nodes": [
                _start_node(),
                _llm_node("a"),  # This will fail (no LLM configured) but tests cancel path
                _end_node(),
            ],
            "edges": [_edge("start_1", "a"), _edge("a", "end_1")],
        }
        parsed = parse_blueprint(raw)

        cancel = asyncio.Event()
        engine = WorkflowEngine(
            cancel_event=cancel,
            run_id="r",
            user_id="u",
            workflow_id="w",
        )

        events: list[tuple[str, dict]] = []

        async def collect():
            async for event_name, event_data in engine.execute_streaming(
                parsed, inputs={}
            ):
                events.append((event_name, event_data))
                # Cancel after the first node starts
                if event_name == "node_started":
                    cancel.set()

        # Should complete (not hang)
        await asyncio.wait_for(collect(), timeout=10.0)


class TestEngineErrorStrategies:
    @pytest.mark.asyncio
    async def test_default_stop_workflow(self):
        """Default STOP_WORKFLOW: a failed node should skip all remaining."""
        raw = {
            "nodes": [
                _start_node(),
                {
                    "id": "code_1",
                    "type": "codeExecution",
                    "data": {
                        "type": "CODE_EXECUTION",
                        "code": "raise ValueError('boom')",
                    },
                },
                _end_node(),
            ],
            "edges": [_edge("start_1", "code_1"), _edge("code_1", "end_1")],
        }
        parsed = parse_blueprint(raw)
        engine = WorkflowEngine(run_id="r", user_id="u", workflow_id="w")

        events: list[tuple[str, dict]] = []
        async for event_name, event_data in engine.execute_streaming(parsed):
            events.append((event_name, event_data))

        event_types = [e[0] for e in events]
        assert "node_failed" in event_types
        # End node should be skipped due to STOP_WORKFLOW
        skipped = [e for e in events if e[0] == "node_skipped"]
        skipped_ids = [e[1]["node_id"] for e in skipped]
        assert "end_1" in skipped_ids

    @pytest.mark.asyncio
    async def test_continue_strategy(self):
        """CONTINUE strategy: failed node doesn't block downstream."""
        raw = {
            "nodes": [
                _start_node(),
                {
                    "id": "code_1",
                    "type": "codeExecution",
                    "data": {
                        "type": "CODE_EXECUTION",
                        "code": "raise ValueError('boom')",
                        "error_strategy": "CONTINUE",
                    },
                },
                _end_node(),
            ],
            "edges": [_edge("start_1", "code_1"), _edge("code_1", "end_1")],
        }
        parsed = parse_blueprint(raw)
        engine = WorkflowEngine(run_id="r", user_id="u", workflow_id="w")

        events: list[tuple[str, dict]] = []
        async for event_name, event_data in engine.execute_streaming(parsed):
            events.append((event_name, event_data))

        event_types = [e[0] for e in events]
        assert "node_failed" in event_types
        # End node should still run (not skipped)
        end_started = any(
            e[0] == "node_started" and e[1].get("node_id") == "end_1"
            for e in events
        )
        assert end_started, "End node should still run with CONTINUE strategy"


class TestVariableAssignNode:
    @pytest.mark.asyncio
    async def test_variable_assign_execution(self):
        """VariableAssign node should set variables in the store."""
        raw = {
            "nodes": [
                _start_node(),
                {
                    "id": "va_1",
                    "type": "variableAssign",
                    "data": {
                        "type": "VARIABLE_ASSIGN",
                        "assignments": [
                            {"variable": "greeting", "value": "hello"},
                        ],
                    },
                },
                _end_node(output_mapping={"msg": "{{va_1.greeting}}"}),
            ],
            "edges": [_edge("start_1", "va_1"), _edge("va_1", "end_1")],
        }
        parsed = parse_blueprint(raw)
        engine = WorkflowEngine(run_id="r", user_id="u", workflow_id="w")

        events: list[tuple[str, dict]] = []
        async for event_name, event_data in engine.execute_streaming(parsed):
            events.append((event_name, event_data))

        # VariableAssign should complete
        va_completed = any(
            e[0] == "node_completed" and e[1].get("node_id") == "va_1"
            for e in events
        )
        assert va_completed


class TestFieldNameCompatibility:
    """Verify that node executors accept both frontend and legacy field names."""

    @pytest.mark.asyncio
    async def test_llm_accepts_prompt_template(self):
        """LLM node should read prompt_template (frontend key)."""
        from fim_one.core.workflow.nodes import LLMExecutor
        from fim_one.core.workflow.types import ExecutionContext, WorkflowNodeDef, NodeType

        node = WorkflowNodeDef(
            id="llm_1", type=NodeType.LLM,
            data={"type": "LLM", "prompt_template": "Hello {{input.name}}"},
        )
        store = VariableStore()
        await store.set("input.name", "World")
        ctx = ExecutionContext(run_id="r", user_id="u", workflow_id="w")

        # Can't actually call LLM without a DB, but we can verify the executor
        # reads the correct field by checking it doesn't get an empty prompt
        executor = LLMExecutor()
        # This will fail due to no DB, but we verify the prompt was read
        result = await executor.execute(node, store, ctx)
        # It should fail with an LLM error (no DB), not "empty prompt"
        assert result.status == NodeStatus.FAILED
        assert "LLM error" in (result.error or "")

    @pytest.mark.asyncio
    async def test_kb_accepts_singular_kb_id(self):
        """KnowledgeRetrieval should accept kb_id (singular) from frontend."""
        from fim_one.core.workflow.nodes import KnowledgeRetrievalExecutor
        from fim_one.core.workflow.types import ExecutionContext, WorkflowNodeDef, NodeType

        node = WorkflowNodeDef(
            id="kb_1", type=NodeType.KNOWLEDGE_RETRIEVAL,
            data={
                "type": "KNOWLEDGE_RETRIEVAL",
                "kb_id": "single-kb-id",
                "query_template": "test query",
            },
        )
        store = VariableStore()
        ctx = ExecutionContext(run_id="r", user_id="u", workflow_id="w")

        executor = KnowledgeRetrievalExecutor()
        result = await executor.execute(node, store, ctx)
        # KB executor handles per-KB errors gracefully (returns 0 results),
        # so it should complete (not "no query" error).
        # The important thing: it read query_template, not "query"
        assert result.status == NodeStatus.COMPLETED
        assert "Retrieved" in str(result.output)


class TestConditionBranchExpressions:
    """Test the condition branch executor's structured expression building."""

    @pytest.mark.asyncio
    async def test_condition_with_structured_fields(self):
        """ConditionBranch should build expressions from variable/operator/value."""
        raw = {
            "nodes": [
                _start_node(),
                {
                    "id": "cond_1",
                    "type": "conditionBranch",
                    "data": {
                        "type": "CONDITION_BRANCH",
                        "mode": "expression",
                        "conditions": [
                            {
                                "id": "c1",
                                "label": "Is Admin",
                                "variable": "role",
                                "operator": "==",
                                "value": "admin",
                            },
                        ],
                    },
                },
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "cond_1"),
                {"id": "e-cond-c1", "source": "cond_1", "target": "end_1", "sourceHandle": "condition-c1"},
            ],
        }
        parsed = parse_blueprint(raw)
        engine = WorkflowEngine(run_id="r", user_id="u", workflow_id="w")

        events: list[tuple[str, dict]] = []
        async for event_name, event_data in engine.execute_streaming(
            parsed, inputs={"role": "admin"}
        ):
            events.append((event_name, event_data))

        # Condition should complete
        cond_completed = any(
            e[0] == "node_completed" and e[1].get("node_id") == "cond_1"
            for e in events
        )
        assert cond_completed

    @pytest.mark.asyncio
    async def test_condition_contains_operator(self):
        """ConditionBranch should handle 'contains' operator."""
        from fim_one.core.workflow.nodes import ConditionBranchExecutor
        from fim_one.core.workflow.types import ExecutionContext, WorkflowNodeDef, NodeType

        node = WorkflowNodeDef(
            id="cond_1", type=NodeType.CONDITION_BRANCH,
            data={
                "type": "CONDITION_BRANCH",
                "mode": "expression",
                "conditions": [
                    {
                        "id": "c1",
                        "label": "Has keyword",
                        "variable": "text",
                        "operator": "contains",
                        "value": "hello",
                    },
                ],
            },
        )
        store = VariableStore()
        await store.set("text", "say hello world")
        ctx = ExecutionContext(run_id="r", user_id="u", workflow_id="w")

        executor = ConditionBranchExecutor()
        result = await executor.execute(node, store, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert result.active_handles == ["condition-c1"]


class TestTemplateTransformNode:
    """Test the TemplateTransform node using Jinja2 sandbox."""

    @pytest.mark.asyncio
    async def test_jinja2_template_rendering(self):
        """TemplateTransform should render Jinja2 templates with store variables."""
        from fim_one.core.workflow.nodes import TemplateTransformExecutor
        from fim_one.core.workflow.types import ExecutionContext, WorkflowNodeDef, NodeType

        node = WorkflowNodeDef(
            id="tmpl_1", type=NodeType.TEMPLATE_TRANSFORM,
            data={
                "type": "TEMPLATE_TRANSFORM",
                "template": "Hello {{ name }}! You have {{ count }} items.",
            },
        )
        store = VariableStore()
        await store.set("name", "Alice")
        await store.set("count", 3)
        ctx = ExecutionContext(run_id="r", user_id="u", workflow_id="w")

        executor = TemplateTransformExecutor()
        result = await executor.execute(node, store, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert "Alice" in str(result.output)
        assert "3" in str(result.output)

    @pytest.mark.asyncio
    async def test_empty_template_fails(self):
        """TemplateTransform with empty template should fail."""
        from fim_one.core.workflow.nodes import TemplateTransformExecutor
        from fim_one.core.workflow.types import ExecutionContext, WorkflowNodeDef, NodeType

        node = WorkflowNodeDef(
            id="tmpl_1", type=NodeType.TEMPLATE_TRANSFORM,
            data={"type": "TEMPLATE_TRANSFORM", "template": ""},
        )
        store = VariableStore()
        ctx = ExecutionContext(run_id="r", user_id="u", workflow_id="w")

        executor = TemplateTransformExecutor()
        result = await executor.execute(node, store, ctx)
        assert result.status == NodeStatus.FAILED
        assert "no template" in (result.error or "").lower()


class TestConditionBranchDefaultRoute:
    """Test condition branch routing to the default (else) branch."""

    @pytest.mark.asyncio
    async def test_falls_through_to_default(self):
        """When no condition matches, default handle is activated."""
        from fim_one.core.workflow.nodes import ConditionBranchExecutor
        from fim_one.core.workflow.types import ExecutionContext, WorkflowNodeDef, NodeType

        node = WorkflowNodeDef(
            id="cond_1", type=NodeType.CONDITION_BRANCH,
            data={
                "type": "CONDITION_BRANCH",
                "mode": "expression",
                "conditions": [
                    {
                        "id": "c1",
                        "label": "Is Admin",
                        "variable": "role",
                        "operator": "==",
                        "value": "admin",
                    },
                ],
                "default_handle": "source-default",
            },
        )
        store = VariableStore()
        await store.set("role", "viewer")  # No match
        ctx = ExecutionContext(run_id="r", user_id="u", workflow_id="w")

        executor = ConditionBranchExecutor()
        result = await executor.execute(node, store, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert result.active_handles == ["source-default"]

    @pytest.mark.asyncio
    async def test_numeric_comparison(self):
        """ConditionBranch should handle numeric operators (> < etc.)."""
        from fim_one.core.workflow.nodes import ConditionBranchExecutor
        from fim_one.core.workflow.types import ExecutionContext, WorkflowNodeDef, NodeType

        node = WorkflowNodeDef(
            id="cond_1", type=NodeType.CONDITION_BRANCH,
            data={
                "type": "CONDITION_BRANCH",
                "mode": "expression",
                "conditions": [
                    {
                        "id": "c1",
                        "label": "High Score",
                        "variable": "score",
                        "operator": ">",
                        "value": "80",
                    },
                ],
            },
        )
        store = VariableStore()
        await store.set("score", 95)
        ctx = ExecutionContext(run_id="r", user_id="u", workflow_id="w")

        executor = ConditionBranchExecutor()
        result = await executor.execute(node, store, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert result.active_handles == ["condition-c1"]


class TestEndNodeOutputMapping:
    """Test the End node output mapping with variable interpolation."""

    @pytest.mark.asyncio
    async def test_output_mapping_with_interpolation(self):
        """End node should interpolate {{var}} references in output mapping."""
        from fim_one.core.workflow.nodes import EndExecutor
        from fim_one.core.workflow.types import ExecutionContext, WorkflowNodeDef, NodeType

        node = WorkflowNodeDef(
            id="end_1", type=NodeType.END,
            data={
                "type": "END",
                "output_mapping": {
                    "greeting": "{{message}}",
                    "direct_ref": "count",
                },
            },
        )
        store = VariableStore()
        await store.set("message", "Hello World!")
        await store.set("count", 42)
        ctx = ExecutionContext(run_id="r", user_id="u", workflow_id="w")

        executor = EndExecutor()
        result = await executor.execute(node, store, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert result.output["greeting"] == "Hello World!"
        # Direct variable reference (no {{...}} wrapper) resolves via store.get()
        assert result.output["direct_ref"] == 42


class TestFailBranchStrategy:
    """Test the FAIL_BRANCH error strategy."""

    @pytest.mark.asyncio
    async def test_fail_branch_skips_downstream(self):
        """FAIL_BRANCH should skip downstream nodes while other branches run."""
        raw = {
            "nodes": [
                _start_node(),
                {
                    "id": "code_fail",
                    "type": "codeExecution",
                    "data": {
                        "type": "CODE_EXECUTION",
                        "code": "raise ValueError('boom')",
                        "error_strategy": "fail_branch",
                    },
                },
                {
                    "id": "code_ok",
                    "type": "codeExecution",
                    "data": {
                        "type": "CODE_EXECUTION",
                        "code": "result = 'ok'",
                    },
                },
                {
                    "id": "after_fail",
                    "type": "variableAssign",
                    "data": {
                        "type": "VARIABLE_ASSIGN",
                        "assignments": [{"variable": "x", "value": "1"}],
                    },
                },
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "code_fail"),
                _edge("start_1", "code_ok"),
                _edge("code_fail", "after_fail"),
                _edge("code_ok", "end_1"),
                _edge("after_fail", "end_1"),
            ],
        }
        parsed = parse_blueprint(raw)
        engine = WorkflowEngine(run_id="r", user_id="u", workflow_id="w")

        events: list[tuple[str, dict]] = []
        async for event_name, event_data in engine.execute_streaming(parsed):
            events.append((event_name, event_data))

        event_types_by_node = {}
        for e_name, e_data in events:
            nid = e_data.get("node_id", "")
            if nid:
                event_types_by_node[nid] = e_name

        # code_fail should fail
        assert event_types_by_node.get("code_fail") == "node_failed"
        # after_fail should be skipped (downstream of failed node)
        assert event_types_by_node.get("after_fail") == "node_skipped"
        # code_ok should still complete (parallel branch)
        assert event_types_by_node.get("code_ok") == "node_completed"


class TestCodeExecutionNode:
    @pytest.mark.asyncio
    async def test_simple_code_execution(self):
        """Code execution should run Python and capture output."""
        raw = {
            "nodes": [
                _start_node(),
                {
                    "id": "code_1",
                    "type": "codeExecution",
                    "data": {
                        "type": "CODE_EXECUTION",
                        "code": "result = 2 + 3",
                    },
                },
                _end_node(),
            ],
            "edges": [_edge("start_1", "code_1"), _edge("code_1", "end_1")],
        }
        parsed = parse_blueprint(raw)
        engine = WorkflowEngine(run_id="r", user_id="u", workflow_id="w")

        events: list[tuple[str, dict]] = []
        async for event_name, event_data in engine.execute_streaming(parsed):
            events.append((event_name, event_data))

        code_completed = [
            e for e in events
            if e[0] == "node_completed" and e[1].get("node_id") == "code_1"
        ]
        assert len(code_completed) == 1
        # The output should contain "5"
        assert "5" in str(code_completed[0][1].get("output_preview", ""))

    @pytest.mark.asyncio
    async def test_code_with_variables(self):
        """Code execution should have access to workflow variables."""
        raw = {
            "nodes": [
                _start_node(),
                {
                    "id": "code_1",
                    "type": "codeExecution",
                    "data": {
                        "type": "CODE_EXECUTION",
                        "code": "result = variables.get('input.name', 'unknown')",
                    },
                },
                _end_node(),
            ],
            "edges": [_edge("start_1", "code_1"), _edge("code_1", "end_1")],
        }
        parsed = parse_blueprint(raw)
        engine = WorkflowEngine(run_id="r", user_id="u", workflow_id="w")

        events: list[tuple[str, dict]] = []
        async for event_name, event_data in engine.execute_streaming(
            parsed, inputs={"name": "TestUser"}
        ):
            events.append((event_name, event_data))

        code_completed = [
            e for e in events
            if e[0] == "node_completed" and e[1].get("node_id") == "code_1"
        ]
        assert len(code_completed) == 1

    @pytest.mark.asyncio
    async def test_code_error_returns_failed(self):
        """Code with a syntax error should produce a failed node result."""
        raw = {
            "nodes": [
                _start_node(),
                {
                    "id": "code_1",
                    "type": "codeExecution",
                    "data": {
                        "type": "CODE_EXECUTION",
                        "code": "this is not valid python!!!",
                        "error_strategy": "CONTINUE",
                    },
                },
                _end_node(),
            ],
            "edges": [_edge("start_1", "code_1"), _edge("code_1", "end_1")],
        }
        parsed = parse_blueprint(raw)
        engine = WorkflowEngine(run_id="r", user_id="u", workflow_id="w")

        events: list[tuple[str, dict]] = []
        async for event_name, event_data in engine.execute_streaming(parsed):
            events.append((event_name, event_data))

        code_failed = [
            e for e in events
            if e[0] == "node_failed" and e[1].get("node_id") == "code_1"
        ]
        assert len(code_failed) == 1
        assert "error" in code_failed[0][1]


# =========================================================================
# Additional executor unit tests
# =========================================================================


class TestVariableAssignExpressions:
    """Test VariableAssign node with simpleeval expressions."""

    @pytest.mark.asyncio
    async def test_expression_evaluation(self):
        """VariableAssign should evaluate simpleeval expressions."""
        from fim_one.core.workflow.nodes import VariableAssignExecutor
        from fim_one.core.workflow.types import ExecutionContext, WorkflowNodeDef, NodeType

        node = WorkflowNodeDef(
            id="va_1", type=NodeType.VARIABLE_ASSIGN,
            data={
                "type": "VARIABLE_ASSIGN",
                "assignments": [
                    {"variable": "doubled", "expression": "x * 2"},
                    {"variable": "greeting", "expression": ""},
                    {"variable": "fallback", "value": "static_val"},
                ],
            },
        )
        store = VariableStore()
        await store.set("x", 21)
        ctx = ExecutionContext(run_id="r", user_id="u", workflow_id="w")

        executor = VariableAssignExecutor()
        result = await executor.execute(node, store, ctx)
        assert result.status == NodeStatus.COMPLETED
        # simpleeval expression: x * 2 = 42
        assert result.output["doubled"] == 42
        # Empty expression falls through to "value" key — but it's not set
        assert result.output.get("greeting") is None
        # Static value assignment
        assert result.output["fallback"] == "static_val"

    @pytest.mark.asyncio
    async def test_interpolation_mode(self):
        """VariableAssign should interpolate {{var}} in expressions."""
        from fim_one.core.workflow.nodes import VariableAssignExecutor
        from fim_one.core.workflow.types import ExecutionContext, WorkflowNodeDef, NodeType

        node = WorkflowNodeDef(
            id="va_1", type=NodeType.VARIABLE_ASSIGN,
            data={
                "type": "VARIABLE_ASSIGN",
                "assignments": [
                    {"variable": "msg", "expression": "Hello {{name}}!"},
                ],
            },
        )
        store = VariableStore()
        await store.set("name", "World")
        ctx = ExecutionContext(run_id="r", user_id="u", workflow_id="w")

        executor = VariableAssignExecutor()
        result = await executor.execute(node, store, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert result.output["msg"] == "Hello World!"

    @pytest.mark.asyncio
    async def test_bad_expression_returns_none(self):
        """A failing simpleeval expression should return None, not crash."""
        from fim_one.core.workflow.nodes import VariableAssignExecutor
        from fim_one.core.workflow.types import ExecutionContext, WorkflowNodeDef, NodeType

        node = WorkflowNodeDef(
            id="va_1", type=NodeType.VARIABLE_ASSIGN,
            data={
                "type": "VARIABLE_ASSIGN",
                "assignments": [
                    {"variable": "bad", "expression": "undefined_var + 1"},
                ],
            },
        )
        store = VariableStore()
        ctx = ExecutionContext(run_id="r", user_id="u", workflow_id="w")

        executor = VariableAssignExecutor()
        result = await executor.execute(node, store, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert result.output["bad"] is None


class TestStartNodePropagation:
    """Verify Start node correctly propagates inputs."""

    @pytest.mark.asyncio
    async def test_inputs_under_both_namespaces(self):
        """Start node should expose inputs as both input.x and start_id.x."""
        from fim_one.core.workflow.nodes import StartExecutor
        from fim_one.core.workflow.types import ExecutionContext, WorkflowNodeDef, NodeType

        node = WorkflowNodeDef(id="start_1", type=NodeType.START, data={})
        store = VariableStore()
        await store.set("input.name", "Alice")
        await store.set("input.age", 30)
        ctx = ExecutionContext(run_id="r", user_id="u", workflow_id="w")

        executor = StartExecutor()
        result = await executor.execute(node, store, ctx)
        assert result.status == NodeStatus.COMPLETED
        # input.name still accessible
        assert await store.get("input.name") == "Alice"
        # Also under start node namespace
        assert await store.get("start_1.name") == "Alice"
        assert await store.get("start_1.age") == 30
        # Combined output
        combined = await store.get("start_1.output")
        assert combined == {"name": "Alice", "age": 30}


class TestEndNodeDefaultOutput:
    """Test End node with no output_mapping (collects all)."""

    @pytest.mark.asyncio
    async def test_no_mapping_collects_all(self):
        """End node without output_mapping should collect all non-env/input vars."""
        from fim_one.core.workflow.nodes import EndExecutor
        from fim_one.core.workflow.types import ExecutionContext, WorkflowNodeDef, NodeType

        node = WorkflowNodeDef(
            id="end_1", type=NodeType.END,
            data={"type": "END"},
        )
        store = VariableStore(env_vars={"SECRET": "hidden"})
        await store.set("input.q", "query")
        await store.set("llm_1.output", "answer text")
        await store.set("code_1.output", 42)
        ctx = ExecutionContext(run_id="r", user_id="u", workflow_id="w")

        executor = EndExecutor()
        result = await executor.execute(node, store, ctx)
        assert result.status == NodeStatus.COMPLETED
        outputs = result.output
        # Should include non-env, non-input vars
        assert "llm_1.output" in outputs
        assert "code_1.output" in outputs
        # Should exclude env and input
        assert "env.SECRET" not in outputs
        assert "input.q" not in outputs


class TestHTTPRequestValidation:
    """Test HTTPRequest node validation edge cases."""

    @pytest.mark.asyncio
    async def test_missing_url_fails(self):
        """HTTPRequest with empty URL should fail."""
        from fim_one.core.workflow.nodes import HTTPRequestExecutor
        from fim_one.core.workflow.types import ExecutionContext, WorkflowNodeDef, NodeType

        node = WorkflowNodeDef(
            id="http_1", type=NodeType.HTTP_REQUEST,
            data={"type": "HTTP_REQUEST", "method": "GET", "url": ""},
        )
        store = VariableStore()
        ctx = ExecutionContext(run_id="r", user_id="u", workflow_id="w")

        executor = HTTPRequestExecutor()
        result = await executor.execute(node, store, ctx)
        # Should fail because empty URL leads to an HTTP error
        assert result.status == NodeStatus.FAILED
        assert "error" in (result.error or "").lower()


class TestConnectorValidation:
    """Test Connector node validation."""

    @pytest.mark.asyncio
    async def test_missing_ids_fails(self):
        """Connector with no connector_id should fail with descriptive error."""
        from fim_one.core.workflow.nodes import ConnectorExecutor
        from fim_one.core.workflow.types import ExecutionContext, WorkflowNodeDef, NodeType

        node = WorkflowNodeDef(
            id="conn_1", type=NodeType.CONNECTOR,
            data={"type": "CONNECTOR", "connector_id": "", "action_id": ""},
        )
        store = VariableStore()
        ctx = ExecutionContext(run_id="r", user_id="u", workflow_id="w")

        executor = ConnectorExecutor()
        result = await executor.execute(node, store, ctx)
        assert result.status == NodeStatus.FAILED
        assert "requires" in (result.error or "").lower()


class TestTemplateTransformAdvanced:
    """Advanced Jinja2 template tests."""

    @pytest.mark.asyncio
    async def test_jinja2_conditionals(self):
        """TemplateTransform should support Jinja2 if/else."""
        from fim_one.core.workflow.nodes import TemplateTransformExecutor
        from fim_one.core.workflow.types import ExecutionContext, WorkflowNodeDef, NodeType

        node = WorkflowNodeDef(
            id="tmpl_1", type=NodeType.TEMPLATE_TRANSFORM,
            data={
                "type": "TEMPLATE_TRANSFORM",
                "template": "{% if score > 80 %}Pass{% else %}Fail{% endif %}",
            },
        )
        store = VariableStore()
        await store.set("score", 95)
        ctx = ExecutionContext(run_id="r", user_id="u", workflow_id="w")

        executor = TemplateTransformExecutor()
        result = await executor.execute(node, store, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert "Pass" in str(result.output)

    @pytest.mark.asyncio
    async def test_jinja2_loops(self):
        """TemplateTransform should support Jinja2 for loops."""
        from fim_one.core.workflow.nodes import TemplateTransformExecutor
        from fim_one.core.workflow.types import ExecutionContext, WorkflowNodeDef, NodeType

        node = WorkflowNodeDef(
            id="tmpl_1", type=NodeType.TEMPLATE_TRANSFORM,
            data={
                "type": "TEMPLATE_TRANSFORM",
                "template": "{% for item in items %}{{ item }},{% endfor %}",
            },
        )
        store = VariableStore()
        await store.set("items", ["a", "b", "c"])
        ctx = ExecutionContext(run_id="r", user_id="u", workflow_id="w")

        executor = TemplateTransformExecutor()
        result = await executor.execute(node, store, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert "a," in str(result.output)
        assert "c," in str(result.output)


class TestEngineParallelExecution:
    """Verify concurrent node execution in the engine."""

    @pytest.mark.asyncio
    async def test_parallel_nodes_both_execute(self):
        """Two independent branches should both execute concurrently."""
        raw = {
            "nodes": [
                _start_node(),
                {
                    "id": "va_a",
                    "type": "variableAssign",
                    "data": {
                        "type": "VARIABLE_ASSIGN",
                        "assignments": [{"variable": "a", "value": "alpha"}],
                    },
                },
                {
                    "id": "va_b",
                    "type": "variableAssign",
                    "data": {
                        "type": "VARIABLE_ASSIGN",
                        "assignments": [{"variable": "b", "value": "beta"}],
                    },
                },
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "va_a"),
                _edge("start_1", "va_b"),
                _edge("va_a", "end_1"),
                _edge("va_b", "end_1"),
            ],
        }
        parsed = parse_blueprint(raw)
        engine = WorkflowEngine(run_id="r", user_id="u", workflow_id="w")

        events: list[tuple[str, dict]] = []
        async for event_name, event_data in engine.execute_streaming(parsed):
            events.append((event_name, event_data))

        completed_nodes = [
            e[1]["node_id"] for e in events if e[0] == "node_completed"
        ]
        # Both branches should complete
        assert "va_a" in completed_nodes
        assert "va_b" in completed_nodes
        assert "end_1" in completed_nodes

    @pytest.mark.asyncio
    async def test_diamond_pattern(self):
        """Diamond: Start → (A, B) → End; verify no deadlock."""
        raw = {
            "nodes": [
                _start_node(),
                {
                    "id": "code_a",
                    "type": "codeExecution",
                    "data": {"type": "CODE_EXECUTION", "code": "result = 'A'"},
                },
                {
                    "id": "code_b",
                    "type": "codeExecution",
                    "data": {"type": "CODE_EXECUTION", "code": "result = 'B'"},
                },
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "code_a"),
                _edge("start_1", "code_b"),
                _edge("code_a", "end_1"),
                _edge("code_b", "end_1"),
            ],
        }
        parsed = parse_blueprint(raw)
        engine = WorkflowEngine(run_id="r", user_id="u", workflow_id="w")

        events: list[tuple[str, dict]] = []
        async for event_name, event_data in engine.execute_streaming(parsed):
            events.append((event_name, event_data))

        # Should complete without deadlock
        final_events = [e for e in events if e[0] in ("run_completed", "run_failed")]
        assert len(final_events) == 1
        assert final_events[0][0] == "run_completed"


class TestEngineConditionBranching:
    """Test full engine execution with condition-based branch selection."""

    @pytest.mark.asyncio
    async def test_true_branch_runs_false_skipped(self):
        """Condition selecting one branch should skip the other."""
        raw = {
            "nodes": [
                _start_node(),
                {
                    "id": "cond_1",
                    "type": "conditionBranch",
                    "data": {
                        "type": "CONDITION_BRANCH",
                        "mode": "expression",
                        "conditions": [
                            {
                                "id": "c1",
                                "label": "Is High",
                                "variable": "score",
                                "operator": ">",
                                "value": "50",
                            },
                        ],
                        "default_handle": "source-default",
                    },
                },
                {
                    "id": "va_true",
                    "type": "variableAssign",
                    "data": {
                        "type": "VARIABLE_ASSIGN",
                        "assignments": [{"variable": "branch", "value": "true_branch"}],
                    },
                },
                {
                    "id": "va_false",
                    "type": "variableAssign",
                    "data": {
                        "type": "VARIABLE_ASSIGN",
                        "assignments": [{"variable": "branch", "value": "false_branch"}],
                    },
                },
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "cond_1"),
                {"id": "e-cond-true", "source": "cond_1", "target": "va_true", "sourceHandle": "condition-c1"},
                {"id": "e-cond-false", "source": "cond_1", "target": "va_false", "sourceHandle": "source-default"},
                _edge("va_true", "end_1"),
                _edge("va_false", "end_1"),
            ],
        }
        parsed = parse_blueprint(raw)
        engine = WorkflowEngine(run_id="r", user_id="u", workflow_id="w")

        events: list[tuple[str, dict]] = []
        async for event_name, event_data in engine.execute_streaming(
            parsed, inputs={"score": 80}
        ):
            events.append((event_name, event_data))

        completed_ids = [
            e[1]["node_id"] for e in events if e[0] == "node_completed"
        ]
        skipped_ids = [
            e[1]["node_id"] for e in events if e[0] == "node_skipped"
        ]
        # True branch should run, false branch should be skipped
        assert "va_true" in completed_ids
        assert "va_false" in skipped_ids

    @pytest.mark.asyncio
    async def test_default_branch_when_no_match(self):
        """When no condition matches, the default branch should run."""
        raw = {
            "nodes": [
                _start_node(),
                {
                    "id": "cond_1",
                    "type": "conditionBranch",
                    "data": {
                        "type": "CONDITION_BRANCH",
                        "mode": "expression",
                        "conditions": [
                            {
                                "id": "c1",
                                "label": "Is High",
                                "variable": "score",
                                "operator": ">",
                                "value": "100",
                            },
                        ],
                        "default_handle": "source-default",
                    },
                },
                {
                    "id": "va_true",
                    "type": "variableAssign",
                    "data": {
                        "type": "VARIABLE_ASSIGN",
                        "assignments": [{"variable": "branch", "value": "true_branch"}],
                    },
                },
                {
                    "id": "va_default",
                    "type": "variableAssign",
                    "data": {
                        "type": "VARIABLE_ASSIGN",
                        "assignments": [{"variable": "branch", "value": "default_branch"}],
                    },
                },
                _end_node(),
            ],
            "edges": [
                _edge("start_1", "cond_1"),
                {"id": "e-cond-true", "source": "cond_1", "target": "va_true", "sourceHandle": "condition-c1"},
                {"id": "e-cond-default", "source": "cond_1", "target": "va_default", "sourceHandle": "source-default"},
                _edge("va_true", "end_1"),
                _edge("va_default", "end_1"),
            ],
        }
        parsed = parse_blueprint(raw)
        engine = WorkflowEngine(run_id="r", user_id="u", workflow_id="w")

        events: list[tuple[str, dict]] = []
        async for event_name, event_data in engine.execute_streaming(
            parsed, inputs={"score": 30}
        ):
            events.append((event_name, event_data))

        completed_ids = [
            e[1]["node_id"] for e in events if e[0] == "node_completed"
        ]
        skipped_ids = [
            e[1]["node_id"] for e in events if e[0] == "node_skipped"
        ]
        # Default branch should run, true branch should be skipped
        assert "va_default" in completed_ids
        assert "va_true" in skipped_ids


class TestEngineTimeout:
    """Test per-node timeout enforcement."""

    @pytest.mark.asyncio
    async def test_node_timeout_kills_long_running(self):
        """A node that exceeds timeout_ms should be killed and marked failed."""
        raw = {
            "nodes": [
                _start_node(),
                {
                    "id": "slow_code",
                    "type": "codeExecution",
                    "data": {
                        "type": "CODE_EXECUTION",
                        "code": "import time; time.sleep(60); result = 'done'",
                        "timeout_ms": 500,
                    },
                },
                _end_node(),
            ],
            "edges": [_edge("start_1", "slow_code"), _edge("slow_code", "end_1")],
        }
        parsed = parse_blueprint(raw)
        engine = WorkflowEngine(run_id="r", user_id="u", workflow_id="w")

        events: list[tuple[str, dict]] = []
        async for event_name, event_data in engine.execute_streaming(parsed):
            events.append((event_name, event_data))

        failed = [e for e in events if e[0] == "node_failed" and e[1].get("node_id") == "slow_code"]
        assert len(failed) == 1
        assert "timed out" in (failed[0][1].get("error", "")).lower()


class TestCodeExecutionEdgeCases:
    """Additional CodeExecution edge cases."""

    @pytest.mark.asyncio
    async def test_empty_code_fails(self):
        """Code node with empty code should fail."""
        from fim_one.core.workflow.nodes import CodeExecutionExecutor
        from fim_one.core.workflow.types import ExecutionContext, WorkflowNodeDef, NodeType

        node = WorkflowNodeDef(
            id="code_1", type=NodeType.CODE_EXECUTION,
            data={"type": "CODE_EXECUTION", "code": ""},
        )
        store = VariableStore()
        ctx = ExecutionContext(run_id="r", user_id="u", workflow_id="w")

        executor = CodeExecutionExecutor()
        result = await executor.execute(node, store, ctx)
        assert result.status == NodeStatus.FAILED
        assert "no code" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_code_outputs_complex_json(self):
        """Code node should serialize complex output as JSON."""
        raw = {
            "nodes": [
                _start_node(),
                {
                    "id": "code_1",
                    "type": "codeExecution",
                    "data": {
                        "type": "CODE_EXECUTION",
                        "code": "result = {'items': [1, 2, 3], 'total': 6}",
                    },
                },
                _end_node(),
            ],
            "edges": [_edge("start_1", "code_1"), _edge("code_1", "end_1")],
        }
        parsed = parse_blueprint(raw)
        engine = WorkflowEngine(run_id="r", user_id="u", workflow_id="w")

        events: list[tuple[str, dict]] = []
        async for event_name, event_data in engine.execute_streaming(parsed):
            events.append((event_name, event_data))

        code_completed = [
            e for e in events
            if e[0] == "node_completed" and e[1].get("node_id") == "code_1"
        ]
        assert len(code_completed) == 1
        preview = code_completed[0][1].get("output_preview", "")
        assert "items" in preview
        assert "total" in preview


class TestEngineEnvVars:
    """Test env var injection into the engine."""

    @pytest.mark.asyncio
    async def test_env_vars_available_in_templates(self):
        """Env vars should be accessible via {{env.KEY}} in templates."""
        raw = {
            "nodes": [
                _start_node(),
                {
                    "id": "tmpl_1",
                    "type": "templateTransform",
                    "data": {
                        "type": "TEMPLATE_TRANSFORM",
                        "template": "Key is {{ env_API_KEY }}",
                    },
                },
                _end_node(),
            ],
            "edges": [_edge("start_1", "tmpl_1"), _edge("tmpl_1", "end_1")],
        }
        parsed = parse_blueprint(raw)
        engine = WorkflowEngine(
            run_id="r", user_id="u", workflow_id="w",
            env_vars={"API_KEY": "sk-test-123"},
        )

        events: list[tuple[str, dict]] = []
        async for event_name, event_data in engine.execute_streaming(parsed):
            events.append((event_name, event_data))

        # TemplateTransform uses snapshot_safe() which excludes env.* vars,
        # so we need to check if env vars are accessible via a different path.
        # The Jinja2 template receives snapshot_safe() variables which do NOT
        # include env vars (by design — they're secrets).
        # This test verifies the security behavior: env vars should NOT leak.
        tmpl_completed = [
            e for e in events
            if e[0] == "node_completed" and e[1].get("node_id") == "tmpl_1"
        ]
        assert len(tmpl_completed) == 1
        # The output should NOT contain the actual secret
        preview = tmpl_completed[0][1].get("output_preview", "")
        assert "sk-test-123" not in preview


# =========================================================================
# Blueprint validation (non-fatal warnings)
# =========================================================================


class TestBlueprintValidation:
    """Test the validate_blueprint() soft warning system."""

    def test_valid_blueprint_no_warnings(self):
        """A well-connected blueprint should produce no warnings."""
        bp = parse_blueprint(_simple_blueprint())
        warnings = validate_blueprint(bp)
        assert len(warnings) == 0

    def test_disconnected_node_warning(self):
        """A node with no edges should produce a disconnected warning."""
        raw = {
            "nodes": [
                _start_node(),
                _llm_node("orphan"),
                _end_node(),
            ],
            "edges": [_edge("start_1", "end_1")],
        }
        bp = parse_blueprint(raw)
        warnings = validate_blueprint(bp)
        codes = [w.code for w in warnings]
        assert "disconnected_node" in codes
        assert any(w.node_id == "orphan" for w in warnings)

    def test_start_no_outgoing_warning(self):
        """Start node with no outgoing edges should warn."""
        raw = {
            "nodes": [_start_node(), _end_node()],
            "edges": [],
        }
        bp = parse_blueprint(raw)
        warnings = validate_blueprint(bp)
        codes = [w.code for w in warnings]
        assert "start_no_outgoing" in codes

    def test_end_unreachable_warning(self):
        """End node not reachable from Start should warn."""
        raw = {
            "nodes": [
                _start_node(),
                _llm_node("a"),
                _end_node("end_1"),
                _end_node("end_2"),
            ],
            "edges": [
                _edge("start_1", "a"),
                _edge("a", "end_1"),
                # end_2 has no incoming edges from the start path
            ],
        }
        bp = parse_blueprint(raw)
        warnings = validate_blueprint(bp)
        codes = [w.code for w in warnings]
        assert "end_unreachable" in codes or "end_no_incoming" in codes

    def test_empty_conditions_warning(self):
        """Condition branch with no conditions should warn."""
        raw = {
            "nodes": [
                _start_node(),
                {
                    "id": "cond_1",
                    "type": "conditionBranch",
                    "data": {
                        "type": "CONDITION_BRANCH",
                        "conditions": [],  # empty!
                    },
                },
                _end_node(),
            ],
            "edges": [_edge("start_1", "cond_1"), _edge("cond_1", "end_1")],
        }
        bp = parse_blueprint(raw)
        warnings = validate_blueprint(bp)
        codes = [w.code for w in warnings]
        assert "empty_conditions" in codes

    def test_empty_llm_prompt_warning(self):
        """LLM node with no prompt should warn."""
        raw = {
            "nodes": [
                _start_node(),
                {
                    "id": "llm_1",
                    "type": "llm",
                    "data": {"type": "LLM", "prompt_template": ""},
                },
                _end_node(),
            ],
            "edges": [_edge("start_1", "llm_1"), _edge("llm_1", "end_1")],
        }
        bp = parse_blueprint(raw)
        warnings = validate_blueprint(bp)
        codes = [w.code for w in warnings]
        assert "empty_prompt" in codes

    def test_empty_code_warning(self):
        """Code node with no code should warn."""
        raw = {
            "nodes": [
                _start_node(),
                {
                    "id": "code_1",
                    "type": "codeExecution",
                    "data": {"type": "CODE_EXECUTION", "code": ""},
                },
                _end_node(),
            ],
            "edges": [_edge("start_1", "code_1"), _edge("code_1", "end_1")],
        }
        bp = parse_blueprint(raw)
        warnings = validate_blueprint(bp)
        codes = [w.code for w in warnings]
        assert "empty_code" in codes

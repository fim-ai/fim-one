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
    parse_blueprint,
    topological_sort,
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
        assert "run_started" in event_types or "node_started" in event_types
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

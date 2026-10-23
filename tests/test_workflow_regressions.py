"""Regression tests for bugs found while testing the trimmed 9-node workflow set.

Covers: editor node-type spellings, structured condition evaluation, output
variable names, error-strategy run status, per-type node timeouts, the
built-in templates, and the run endpoints' quota / rate-limit bookkeeping.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.core.workflow.engine import WorkflowEngine
from fim_one.core.workflow.nodes import (
    ConditionBranchExecutor,
    LLMExecutor,
    _store_output,
)
from fim_one.core.workflow.parser import (
    BlueprintValidationError,
    parse_blueprint,
    resolve_raw_node_type,
)
from fim_one.core.workflow.templates import WORKFLOW_TEMPLATES
from fim_one.core.workflow.types import (
    ExecutionContext,
    NodeResult,
    NodeStatus,
    NodeType,
    WorkflowNodeDef,
    default_timeout_ms,
)
from fim_one.core.workflow.variable_store import VariableStore


def _ctx() -> ExecutionContext:
    return ExecutionContext(run_id="r", user_id="u", workflow_id="w")


def _n(node_id: str, node_type: str, **data: Any) -> dict[str, Any]:
    """Node the way the backend/templates spell it (type on data.type)."""
    return {"id": node_id, "data": {"type": node_type, **data}}


def _editor_node(node_id: str, rf_type: str, **data: Any) -> dict[str, Any]:
    """Node the way the editor saves it: React Flow type, no data.type."""
    return {"id": node_id, "type": rf_type, "position": {"x": 0, "y": 0}, "data": data}


def _e(source: str, target: str, handle: str | None = None) -> dict[str, Any]:
    edge: dict[str, Any] = {"source": source, "target": target}
    if handle is not None:
        edge["sourceHandle"] = handle
    return edge


async def _run(
    raw: dict[str, Any], inputs: dict[str, Any] | None = None
) -> list[tuple[str, dict[str, Any]]]:
    engine = WorkflowEngine(run_id="r", user_id="u", workflow_id="w")
    events: list[tuple[str, dict[str, Any]]] = []
    async for name, data in engine.execute_streaming(parse_blueprint(raw), inputs):
        events.append((name, data))
    return events


def _final(events: list[tuple[str, dict[str, Any]]]) -> tuple[str, dict[str, Any]]:
    return next(e for e in reversed(events) if e[0] in ("run_completed", "run_failed"))


def _last_event_by_node(events: list[tuple[str, dict[str, Any]]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, data in events:
        if data.get("node_id"):
            out[data["node_id"]] = name
    return out


# ---------------------------------------------------------------------------
# Node types as the editor saves them
# ---------------------------------------------------------------------------


class TestEditorNodeTypes:
    @pytest.mark.parametrize(
        ("rf_type", "expected"),
        [
            ("start", NodeType.START),
            ("end", NodeType.END),
            ("llm", NodeType.LLM),
            ("conditionBranch", NodeType.CONDITION_BRANCH),
            ("agent", NodeType.AGENT),
            ("knowledgeRetrieval", NodeType.KNOWLEDGE_RETRIEVAL),
            ("connector", NodeType.CONNECTOR),
            ("humanIntervention", NodeType.HUMAN_INTERVENTION),
            ("mcp", NodeType.MCP),
        ],
    )
    def test_react_flow_type_without_data_type(self, rf_type: str, expected: NodeType) -> None:
        assert resolve_raw_node_type(_editor_node("x", rf_type)) == expected

    def test_data_type_wins_over_placeholder(self) -> None:
        node = {"id": "x", "type": "custom", "data": {"type": "KNOWLEDGE_RETRIEVAL"}}
        assert resolve_raw_node_type(node) == NodeType.KNOWLEDGE_RETRIEVAL

    def test_unknown_type_returns_none(self) -> None:
        assert resolve_raw_node_type({"id": "x", "type": "subWorkflow", "data": {}}) is None

    def test_editor_blueprint_with_branch_parses(self) -> None:
        raw = {
            "nodes": [
                _editor_node("start_1", "start"),
                _editor_node("conditionBranch_1", "conditionBranch", conditions=[]),
                _editor_node("knowledgeRetrieval_1", "knowledgeRetrieval"),
                _editor_node("humanIntervention_1", "humanIntervention"),
                _editor_node("end_1", "end"),
            ],
            "edges": [
                _e("start_1", "conditionBranch_1"),
                _e("conditionBranch_1", "knowledgeRetrieval_1", "source-default"),
                _e("knowledgeRetrieval_1", "humanIntervention_1"),
                _e("humanIntervention_1", "end_1"),
            ],
        }
        types = {n.id: n.type for n in parse_blueprint(raw).nodes}
        assert types["conditionBranch_1"] == NodeType.CONDITION_BRANCH
        assert types["knowledgeRetrieval_1"] == NodeType.KNOWLEDGE_RETRIEVAL
        assert types["humanIntervention_1"] == NodeType.HUMAN_INTERVENTION

    def test_removed_node_type_still_rejected(self) -> None:
        raw = {
            "nodes": [
                _n("s", "START"),
                {"id": "x", "type": "subWorkflow", "data": {"type": "SUB_WORKFLOW"}},
                _n("e", "END"),
            ],
            "edges": [_e("s", "x"), _e("x", "e")],
        }
        with pytest.raises(BlueprintValidationError, match="Unknown node type"):
            parse_blueprint(raw)


# ---------------------------------------------------------------------------
# Structured condition evaluation
# ---------------------------------------------------------------------------


async def _eval_condition(
    variable: str, operator: str, value: Any, store_values: dict[str, Any]
) -> str:
    store = VariableStore()
    for key, val in store_values.items():
        await store.set(key, val)
    node = WorkflowNodeDef(
        id="cond",
        type=NodeType.CONDITION_BRANCH,
        data={
            "conditions": [{"id": "a", "variable": variable, "operator": operator, "value": value}]
        },
    )
    result = await ConditionBranchExecutor().execute(node, store, _ctx())
    assert result.status == NodeStatus.COMPLETED, result.error
    return str(result.output)


_VARS: dict[str, Any] = {
    "input.score": 80,
    "input.score_text": "80",
    "input.name": "Alice",
    "input.empty": "",
    "input.tags": ["vip", "beta"],
    "llm_1.output": "yes, approved",
    "human_1.output": {"status": "approved", "decision_by": "bob"},
    "flag.output": True,
}


class TestConditionVariables:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("variable", "operator", "value"),
        [
            ("input.score", ">", "50"),
            ("{{input.score}}", ">", "50"),
            ("{{ input.score }}", ">=", "80"),
            ("score", ">", "50"),
            ("input_score", ">", "50"),
            ("input.score_text", ">", "50"),
            ("input.score_text", "==", "80"),
            ("input.score", "==", "80.0"),
            ("input.name", "==", "Alice"),
            ("input.name", "!=", "Bob"),
            ("llm_1.output", "contains", "approved"),
            ("llm_1.output", "not_contains", "rejected"),
            ("input.tags", "contains", "vip"),
            ("human_1.output.status", "==", "approved"),
            ("input.empty", "is_empty", ""),
            ("input.missing", "is_empty", ""),
            ("input.name", "is_not_empty", ""),
            ("flag.output", "==", "true"),
        ],
    )
    async def test_condition_matches(self, variable: str, operator: str, value: Any) -> None:
        assert await _eval_condition(variable, operator, value, _VARS) == "condition-a"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("variable", "operator", "value"),
        [
            ("input.score", "<", "50"),
            ("input.name", "==", "Bob"),
            ("input.name", ">", "50"),  # text vs number never matches
            ("input.tags", "contains", "vi"),  # list membership, not substring
            ("input.missing", "==", "x"),
            ("input.name", "is_empty", ""),
            ("input.score", "~=", "80"),  # unsupported operator is ignored
        ],
    )
    async def test_condition_falls_to_default(
        self, variable: str, operator: str, value: Any
    ) -> None:
        assert await _eval_condition(variable, operator, value, _VARS) == "source-default"

    @pytest.mark.asyncio
    async def test_dotted_condition_routes_branch_end_to_end(self) -> None:
        raw = {
            "nodes": [
                _n("start_1", "START"),
                _n(
                    "cond_1",
                    "CONDITION_BRANCH",
                    conditions=[
                        {"id": "high", "variable": "input.score", "operator": ">", "value": "50"}
                    ],
                ),
                _n("end_high", "END", output_mapping={"branch": "high"}),
                _n("end_low", "END", output_mapping={"branch": "low"}),
            ],
            "edges": [
                _e("start_1", "cond_1"),
                _e("cond_1", "end_high", "condition-high"),
                _e("cond_1", "end_low", "source-default"),
            ],
        }
        name, data = _final(await _run(raw, {"score": "75"}))
        assert name == "run_completed"
        assert data["outputs"] == {"branch": "high"}

        name, data = _final(await _run(raw, {"score": 10}))
        assert data["outputs"] == {"branch": "low"}

    @pytest.mark.asyncio
    async def test_llm_mode_condition_usage_is_metered(self) -> None:
        store = VariableStore()
        node = WorkflowNodeDef(
            id="cond",
            type=NodeType.CONDITION_BRANCH,
            data={
                "mode": "llm",
                "conditions": [{"id": "a", "label": "Urgent", "llm_prompt": "is urgent"}],
            },
        )
        llm = MagicMock()
        llm.model_id = "m"
        llm.chat = AsyncMock(
            return_value=SimpleNamespace(
                message=SimpleNamespace(content="Urgent"),
                usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
            )
        )
        ctx = _ctx()
        ctx.usage_tracker = MagicMock()
        ctx.usage_tracker.record = AsyncMock()

        with (
            patch("fim_one.db.create_session", _fake_session),
            patch("fim_one.web.deps.get_effective_fast_llm", AsyncMock(return_value=llm)),
        ):
            result = await ConditionBranchExecutor().execute(node, store, ctx)

        assert result.output == "condition-a"
        ctx.usage_tracker.record.assert_awaited_once()


@asynccontextmanager
async def _fake_session() -> AsyncIterator[MagicMock]:
    yield MagicMock()


# ---------------------------------------------------------------------------
# Output variable names
# ---------------------------------------------------------------------------


class TestOutputVariable:
    @pytest.mark.asyncio
    async def test_store_output_writes_output_variable(self) -> None:
        store = VariableStore()
        node = WorkflowNodeDef(
            id="llm_1", type=NodeType.LLM, data={"output_variable": "llm_result"}
        )
        await _store_output(node, store, "hi")
        assert await store.get("llm_1.output") == "hi"
        assert await store.get("llm_1.llm_result") == "hi"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", ["", "output", "a.b", "{{x}}", None, 3])
    async def test_store_output_ignores_unusable_names(self, bad: Any) -> None:
        store = VariableStore()
        node = WorkflowNodeDef(id="n", type=NodeType.LLM, data={"output_variable": bad})
        await _store_output(node, store, "v")
        assert await store.keys() == ["n.output"]

    @pytest.mark.asyncio
    async def test_picker_reference_resolves_for_llm_node(self) -> None:
        """The variable picker inserts {{node_id.<output_variable>}}."""
        store = VariableStore()
        node = WorkflowNodeDef(
            id="llm_1",
            type=NodeType.LLM,
            data={"prompt_template": "hi", "output_variable": "llm_result"},
        )
        llm = MagicMock()
        llm.chat = AsyncMock(
            return_value=SimpleNamespace(message=SimpleNamespace(content="answer"), usage=None)
        )
        with (
            patch("fim_one.db.create_session", _fake_session),
            patch("fim_one.web.deps.get_effective_llm", AsyncMock(return_value=llm)),
        ):
            result = await LLMExecutor().execute(node, store, _ctx())

        assert result.status == NodeStatus.COMPLETED
        assert await store.interpolate("{{llm_1.llm_result}}") == "answer"


# ---------------------------------------------------------------------------
# Error strategies and run status
# ---------------------------------------------------------------------------


# A connector node with no connector configured fails immediately, without I/O.
def _failing(node_id: str, strategy: str) -> dict[str, Any]:
    return _n(node_id, "CONNECTOR", error_strategy=strategy)


class TestErrorStrategyRunStatus:
    @pytest.mark.asyncio
    async def test_continue_completes_run_with_outputs(self) -> None:
        raw = {
            "nodes": [
                _n("s", "START"),
                _failing("c", "continue"),
                _n("end", "END", output_mapping={"x": "{{input.a}}"}),
            ],
            "edges": [_e("s", "c"), _e("c", "end")],
        }
        name, data = _final(await _run(raw, {"a": "1"}))
        assert name == "run_completed"
        assert data["outputs"] == {"x": "1"}
        assert data["failed_nodes"] == ["c"]

    @pytest.mark.asyncio
    async def test_fail_branch_keeps_join_reachable_from_other_branch(self) -> None:
        raw = {
            "nodes": [
                _n("s", "START"),
                _failing("bad", "fail_branch"),
                _n("only_after_bad", "END", output_mapping={"never": "x"}),
                _n("end", "END", output_mapping={"ok": "yes"}),
            ],
            "edges": [
                _e("s", "bad"),
                _e("bad", "only_after_bad"),
                _e("s", "end"),
                _e("bad", "end"),
            ],
        }
        events = await _run(raw)
        by_node = _last_event_by_node(events)
        assert by_node["bad"] == "node_failed"
        assert by_node["only_after_bad"] == "node_skipped"
        assert by_node["end"] == "node_completed"

        failed_event = next(d for n, d in events if n == "node_failed" and d["node_id"] == "bad")
        assert failed_event["skipped_downstream"] == ["only_after_bad"]

        name, data = _final(events)
        assert name == "run_completed"
        assert data["outputs"] == {"ok": "yes"}

    @pytest.mark.asyncio
    async def test_fail_branch_cutting_every_end_fails_run(self) -> None:
        raw = {
            "nodes": [_n("s", "START"), _failing("bad", "fail_branch"), _n("end", "END")],
            "edges": [_e("s", "bad"), _e("bad", "end")],
        }
        name, data = _final(await _run(raw))
        assert name == "run_failed"
        assert "No End node was reached" in data["error"]

    @pytest.mark.asyncio
    async def test_stop_workflow_still_fails_run(self) -> None:
        raw = {
            "nodes": [_n("s", "START"), _failing("bad", "stop_workflow"), _n("end", "END")],
            "edges": [_e("s", "bad"), _e("bad", "end")],
        }
        name, data = _final(await _run(raw))
        assert name == "run_failed"
        assert data["error"] == "Nodes failed: ['bad']"


# ---------------------------------------------------------------------------
# Per-type node timeouts
# ---------------------------------------------------------------------------


class TestNodeTimeoutDefaults:
    def test_defaults_by_type(self) -> None:
        raw = {
            "nodes": [
                _n("s", "START"),
                _n("agent", "AGENT"),
                _n("llm", "LLM"),
                _n("mcp", "MCP"),
                _n("conn", "CONNECTOR"),
                _n("e", "END"),
            ],
            "edges": [
                _e("s", "agent"),
                _e("agent", "llm"),
                _e("llm", "mcp"),
                _e("mcp", "conn"),
                _e("conn", "e"),
            ],
        }
        timeouts = {n.id: n.timeout_ms for n in parse_blueprint(raw).nodes}
        assert timeouts["agent"] == 300_000
        assert timeouts["llm"] == 120_000
        assert timeouts["mcp"] == 60_000
        assert timeouts["conn"] == 30_000
        assert default_timeout_ms(NodeType.KNOWLEDGE_RETRIEVAL) == 30_000

    def test_explicit_timeout_wins(self) -> None:
        raw = {
            "nodes": [_n("s", "START"), _n("agent", "AGENT", timeout_ms=5000), _n("e", "END")],
            "edges": [_e("s", "agent"), _e("agent", "e")],
        }
        agent = next(n for n in parse_blueprint(raw).nodes if n.id == "agent")
        assert agent.timeout_ms == 5000


# ---------------------------------------------------------------------------
# Built-in templates
# ---------------------------------------------------------------------------

_EDITOR_TYPES = {
    "start",
    "end",
    "llm",
    "conditionBranch",
    "agent",
    "knowledgeRetrieval",
    "connector",
    "humanIntervention",
    "mcp",
}


class TestBuiltinTemplates:
    @pytest.mark.parametrize("tpl", WORKFLOW_TEMPLATES, ids=lambda t: t["id"])
    def test_template_matches_editor_shape(self, tpl: dict[str, Any]) -> None:
        bp = tpl["blueprint"]
        parse_blueprint(bp)
        nodes = {n["id"]: n for n in bp["nodes"]}
        for node in bp["nodes"]:
            # Rendered by a real node component, not React Flow's fallback.
            assert node["type"] in _EDITOR_TYPES, node["id"]
            data = node["data"]
            if node["type"] == "start":
                assert data.get("variables"), "run panel reads Start variables"
            if node["type"] == "end":
                assert "output_mapping" in data, "End node executes output_mapping"
                assert "output_schema" not in data

        # Every condition edge must leave from a handle the node renders.
        for edge in bp["edges"]:
            source = nodes[edge["source"]]
            if source["type"] != "conditionBranch":
                continue
            rendered = {f"condition-{c['id']}" for c in source["data"]["conditions"]}
            rendered.add("source-default")
            assert edge.get("sourceHandle") in rendered, edge["id"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("category", "expected"),
        [("technical", "tech answer"), ("billing", "general answer")],
    )
    async def test_conditional_router_template_routes(self, category: str, expected: str) -> None:
        tpl = next(t for t in WORKFLOW_TEMPLATES if t["id"] == "conditional-router")

        async def fake_llm(
            self: LLMExecutor,
            node: WorkflowNodeDef,
            store: VariableStore,
            context: ExecutionContext,
        ) -> NodeResult:
            answer = "tech answer" if node.id == "llm_technical" else "general answer"
            await store.set(f"{node.id}.output", answer)
            return NodeResult(node_id=node.id, status=NodeStatus.COMPLETED, output=answer)

        with patch.object(LLMExecutor, "execute", fake_llm):
            name, data = _final(await _run(tpl["blueprint"], {"query": "q", "category": category}))

        assert name == "run_completed"
        assert data["outputs"] == {"result": expected}


# ---------------------------------------------------------------------------
# Run endpoints
# ---------------------------------------------------------------------------


class TestTriggerRateLimitSlot:
    @pytest.mark.asyncio
    async def test_invalid_blueprint_does_not_leak_slot(self) -> None:
        from fim_one.core.workflow.rate_limiter import WorkflowRateLimiter
        from fim_one.web.api import workflows as wf_api
        from fim_one.web.exceptions import AppError
        from fim_one.web.schemas.workflow import WorkflowTriggerRequest

        wf = SimpleNamespace(
            id="wf-1",
            user_id="owner-1",
            is_active=True,
            blueprint={"nodes": [{"id": "x", "data": {"type": "BOGUS"}}], "edges": []},
        )
        lookup = MagicMock()
        lookup.scalar_one_or_none.return_value = wf
        running = MagicMock()
        running.scalar_one.return_value = 0
        db = MagicMock()
        db.execute = AsyncMock(side_effect=[lookup, running])

        limiter = WorkflowRateLimiter(max_concurrent_runs=1)
        with (
            patch.object(wf_api, "_rate_limiter", limiter),
            patch("fim_one.web.api.chat._check_token_quota", AsyncMock()),
            pytest.raises(AppError),
        ):
            await wf_api.trigger_workflow("key", WorkflowTriggerRequest(inputs={}), db)

        assert not limiter._active_runs.get("owner-1")


class TestInteractiveRunQuota:
    @pytest.mark.asyncio
    async def test_run_refused_when_over_quota(self) -> None:
        from fim_one.web.api import workflows as wf_api
        from fim_one.web.exceptions import AppError
        from fim_one.web.schemas.workflow import WorkflowRunRequest

        wf = SimpleNamespace(id="wf-1", blueprint={"nodes": [{"id": "s"}]})
        user = MagicMock(id="user-1")
        acquire = AsyncMock(return_value=(True, None))
        with (
            patch.object(wf_api, "_get_accessible_workflow", AsyncMock(return_value=wf)),
            patch.object(wf_api._rate_limiter, "acquire", acquire),
            patch(
                "fim_one.web.api.chat._check_token_quota",
                AsyncMock(side_effect=AppError("token_quota_exceeded", status_code=429)),
            ),
            pytest.raises(AppError),
        ):
            await wf_api.run_workflow(
                "wf-1", WorkflowRunRequest(inputs={}), MagicMock(), user, MagicMock()
            )
        acquire.assert_not_awaited()


class TestEndOutputMapping:
    @pytest.mark.asyncio
    async def test_bare_name_reads_variable_else_constant(self) -> None:
        raw = {
            "nodes": [
                _n("s", "START"),
                _n(
                    "end",
                    "END",
                    output_mapping={
                        "name": "input.name",
                        "templated": "Hi {{input.name}}",
                        "constant": "Request rejected.",
                    },
                ),
            ],
            "edges": [_e("s", "end")],
        }
        name, data = _final(await _run(raw, {"name": "Ann"}))
        assert name == "run_completed"
        assert data["outputs"] == {
            "name": "Ann",
            "templated": "Hi Ann",
            "constant": "Request rejected.",
        }

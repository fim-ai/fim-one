"""Tests for the RunWorkflowTool builtin (inline workflow invocation)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from fim_one.core.tool.builtin.run_workflow import (
    _MAX_WORKFLOW_DEPTH,
    _active_workflows,
    RunWorkflowTool,
)
from fim_one.core.workflow.types import NodeType


class _FakeResult:
    def __init__(self, scalar: Any = None) -> None:
        self._scalar = scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class _FakeSession:
    """Minimal async session over a shared store so the audit run created in one
    session is visible to the finalize lookup in a later session."""

    def __init__(self, wf: Any, runs: dict[str, Any]) -> None:
        self._wf = wf
        self._runs = runs

    async def execute(self, _query: Any) -> _FakeResult:
        return _FakeResult(scalar=self._wf)

    def add(self, obj: Any) -> None:
        self._runs[obj.id] = obj

    async def commit(self) -> None:
        return None

    async def get(self, _model: Any, _id: str) -> Any:
        return self._runs.get(_id)

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False


def _make_session_factory(wf: Any) -> Any:
    runs: dict[str, Any] = {}

    def _factory() -> _FakeSession:
        return _FakeSession(wf, runs)

    _factory.runs = runs  # type: ignore[attr-defined]
    return _factory


class _FakeEngineCompleted:
    def __init__(self, **_: Any) -> None:
        pass

    async def execute_streaming(
        self, _parsed: Any, _inputs: Any, context: Any = None
    ) -> Any:
        yield (
            "run_completed",
            {"outputs": {"answer": 42}, "status": "completed", "total_tokens": 1234},
        )


class _FakeEngineFailed:
    def __init__(self, **_: Any) -> None:
        pass

    async def execute_streaming(
        self, _parsed: Any, _inputs: Any, context: Any = None
    ) -> Any:
        yield (
            "run_failed",
            {"status": "failed", "error": "boom", "total_tokens": 77},
        )


def _patch_runtime(
    monkeypatch: pytest.MonkeyPatch,
    wf: Any,
    nodes: list[Any],
    engine_cls: Any,
    quota: tuple[int, int] = (0, 0),
) -> Any:
    factory = _make_session_factory(wf)
    monkeypatch.setattr("fim_one.db.create_session", factory)
    monkeypatch.setattr(
        "fim_one.core.workflow.parser.parse_blueprint",
        lambda _bp: SimpleNamespace(nodes=nodes),
    )
    monkeypatch.setattr("fim_one.core.workflow.engine.WorkflowEngine", engine_cls)

    async def _fake_quota(_user_id: str) -> tuple[int, int]:
        return quota

    monkeypatch.setattr("fim_one.web.api.chat._get_quota_status", _fake_quota)
    return factory


def _wf(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = dict(
        id="wf1",
        name="nightly_reconcile",
        user_id="owner1",
        blueprint={"nodes": []},
        env_vars_blob=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_empty_name_returns_error() -> None:
    tool = RunWorkflowTool(workflow_ids=["wf1"], user_id="u1")
    assert "name is required" in await tool.run(name="  ")


@pytest.mark.asyncio
async def test_no_workflow_ids_returns_not_found() -> None:
    tool = RunWorkflowTool(workflow_ids=[], user_id="u1")
    assert "workflow not found" in await tool.run(name="anything")


@pytest.mark.asyncio
async def test_inputs_must_be_object() -> None:
    tool = RunWorkflowTool(workflow_ids=["wf1"], user_id="u1")
    out = await tool.run(name="nightly_reconcile", inputs="not-a-dict")
    assert "inputs must be an object" in out


@pytest.mark.asyncio
async def test_not_found_when_db_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime(monkeypatch, wf=None, nodes=[], engine_cls=_FakeEngineCompleted)
    tool = RunWorkflowTool(workflow_ids=["wf1"], user_id="u1")
    out = await tool.run(name="nightly_reconcile")
    assert "not found or not runnable" in out


@pytest.mark.asyncio
async def test_rejects_human_intervention_node(monkeypatch: pytest.MonkeyPatch) -> None:
    nodes = [SimpleNamespace(type=NodeType.HUMAN_INTERVENTION)]
    _patch_runtime(monkeypatch, wf=_wf(), nodes=nodes, engine_cls=_FakeEngineCompleted)
    tool = RunWorkflowTool(workflow_ids=["wf1"], user_id="u1")
    out = await tool.run(name="nightly_reconcile")
    assert "human-approval step" in out


@pytest.mark.asyncio
async def test_success_returns_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    nodes = [SimpleNamespace(type=NodeType.LLM)]
    factory = _patch_runtime(
        monkeypatch, wf=_wf(), nodes=nodes, engine_cls=_FakeEngineCompleted
    )
    tool = RunWorkflowTool(workflow_ids=["wf1"], user_id="u1")
    out = await tool.run(name="nightly_reconcile", inputs={"x": 1})
    assert "completed" in out
    assert "answer" in out and "42" in out
    # Token usage must be written back so it bills to the owner's quota window.
    (run,) = factory.runs.values()
    assert run.total_tokens == 1234


@pytest.mark.asyncio
async def test_over_quota_owner_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    nodes = [SimpleNamespace(type=NodeType.LLM)]
    factory = _patch_runtime(
        monkeypatch,
        wf=_wf(),
        nodes=nodes,
        engine_cls=_FakeEngineCompleted,
        quota=(100, 50),  # used >= cap
    )
    tool = RunWorkflowTool(workflow_ids=["wf1"], user_id="u1")
    out = await tool.run(name="nightly_reconcile")
    assert "quota exceeded" in out
    # Refused before any run record is created — no unmetered execution.
    assert not factory.runs


@pytest.mark.asyncio
async def test_failure_surfaces_error(monkeypatch: pytest.MonkeyPatch) -> None:
    nodes = [SimpleNamespace(type=NodeType.LLM)]
    _patch_runtime(monkeypatch, wf=_wf(), nodes=nodes, engine_cls=_FakeEngineFailed)
    tool = RunWorkflowTool(workflow_ids=["wf1"], user_id="u1")
    out = await tool.run(name="nightly_reconcile")
    assert "failed" in out and "boom" in out


@pytest.mark.asyncio
async def test_reentrancy_cycle_prevented(monkeypatch: pytest.MonkeyPatch) -> None:
    nodes = [SimpleNamespace(type=NodeType.LLM)]
    _patch_runtime(monkeypatch, wf=_wf(), nodes=nodes, engine_cls=_FakeEngineCompleted)
    tool = RunWorkflowTool(workflow_ids=["wf1"], user_id="u1")
    token = _active_workflows.set(frozenset({"wf1"}))
    try:
        out = await tool.run(name="nightly_reconcile")
    finally:
        _active_workflows.reset(token)
    assert "already running" in out


@pytest.mark.asyncio
async def test_depth_limit_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    nodes = [SimpleNamespace(type=NodeType.LLM)]
    _patch_runtime(monkeypatch, wf=_wf(), nodes=nodes, engine_cls=_FakeEngineCompleted)
    tool = RunWorkflowTool(workflow_ids=["wf1"], user_id="u1")
    deep = frozenset(f"other{i}" for i in range(_MAX_WORKFLOW_DEPTH))
    token = _active_workflows.set(deep)
    try:
        out = await tool.run(name="nightly_reconcile")
    finally:
        _active_workflows.reset(token)
    assert "nesting too deep" in out

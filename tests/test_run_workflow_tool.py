"""Tests for the RunWorkflowTool builtin (inline workflow invocation).

Since T10 P2 the tool starts runs through the run service and runs as the
agent's user (the caller), not the workflow's author: the caller's quota is
checked and billed, and the author's env vars are injected only when the
caller is the author (decision 2026-11-04).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any, ClassVar
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.core.tool.builtin.run_workflow import (
    _MAX_WORKFLOW_DEPTH,
    RunWorkflowTool,
    _active_workflows,
)
from fim_one.core.workflow import run_service as rs
from fim_one.db.base import Base
from fim_one.db.models.user import User
from fim_one.db.models.workflow import Workflow, WorkflowRun
from fim_one.web.exceptions import AppError

_BLUEPRINT = {
    "nodes": [
        {"id": "start_1", "type": "start", "data": {"type": "START"}},
        {"id": "llm_1", "type": "llm", "data": {"type": "LLM", "prompt_template": "hi"}},
        {"id": "end_1", "type": "end", "data": {"type": "END"}},
    ],
    "edges": [
        {"id": "e1", "source": "start_1", "target": "llm_1"},
        {"id": "e2", "source": "llm_1", "target": "end_1"},
    ],
}


class _FakeEngine:
    """Records what the service built and replays a scripted ending."""

    created: ClassVar[list[dict[str, Any]]] = []
    ending: ClassVar[tuple[str, dict[str, Any]]] = ("run_completed", {})

    def __init__(self, **kwargs: Any) -> None:
        _FakeEngine.created.append(kwargs)

    async def execute_streaming(
        self, _parsed: Any, _inputs: Any, context: Any = None
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        _FakeEngine.created[-1]["context"] = context
        yield _FakeEngine.ending


@pytest.fixture()
async def db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    import fim_one.db.models  # noqa: F401

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _cs() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    with patch("fim_one.db.create_session", _cs):
        yield factory
    await engine.dispose()


@pytest.fixture(autouse=True)
def service() -> Iterator[rs.WorkflowRunService]:
    _FakeEngine.created = []
    _FakeEngine.ending = (
        "run_completed",
        {"outputs": {"answer": 42}, "status": "completed", "total_tokens": 1234},
    )
    svc = rs.WorkflowRunService(engine_factory=_FakeEngine)
    with (
        patch.object(rs, "_default_service", svc),
        patch("fim_one.web.api.chat._check_token_quota", new=AsyncMock()),
    ):
        yield svc


async def _setup(
    factory: async_sessionmaker[AsyncSession],
    *,
    blueprint: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[str, str, str]:
    """Create author, caller and a workflow; return their ids."""
    from fim_one.core.security.encryption import encrypt_credential

    async with factory() as s:
        author = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4()}@x.io")
        caller = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4()}@x.io")
        s.add_all([author, caller])
        await s.flush()
        wf = Workflow(
            user_id=author.id,
            name="nightly_reconcile",
            blueprint=blueprint or _BLUEPRINT,
            status="active",
            is_active=True,
            env_vars_blob=encrypt_credential(env) if env else None,
        )
        s.add(wf)
        await s.commit()
        return author.id, caller.id, wf.id


async def _runs(factory: async_sessionmaker[AsyncSession]) -> list[WorkflowRun]:
    async with factory() as s:
        return list((await s.execute(select(WorkflowRun))).scalars().all())


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


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
async def test_not_found_when_not_in_ids(db: async_sessionmaker[AsyncSession]) -> None:
    _, caller, _ = await _setup(db)
    tool = RunWorkflowTool(workflow_ids=["other"], user_id=caller)
    out = await tool.run(name="nightly_reconcile")
    assert "not found or not runnable" in out


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejects_human_intervention_node(db: async_sessionmaker[AsyncSession]) -> None:
    bp = {
        "nodes": [
            {"id": "start_1", "type": "start", "data": {}},
            {"id": "h", "type": "humanIntervention", "data": {"title": "ok?"}},
            {"id": "end_1", "type": "end", "data": {}},
        ],
        "edges": [
            {"id": "e1", "source": "start_1", "target": "h"},
            {"id": "e2", "source": "h", "target": "end_1"},
        ],
    }
    _, caller, wf_id = await _setup(db, blueprint=bp)
    out = await RunWorkflowTool(workflow_ids=[wf_id], user_id=caller).run(
        name="nightly_reconcile"
    )
    assert "human-approval step" in out
    assert await _runs(db) == []


@pytest.mark.asyncio
async def test_success_runs_as_caller_and_bills_caller(
    db: async_sessionmaker[AsyncSession],
) -> None:
    author, caller, wf_id = await _setup(db, env={"SECRET": "author-only"})
    out = await RunWorkflowTool(workflow_ids=[wf_id], user_id=caller).run(
        name="nightly_reconcile", inputs={"x": 1}
    )
    assert "completed" in out and "42" in out

    (run,) = await _runs(db)
    assert run.user_id == caller != author
    assert run.mode == "tool"
    assert run.total_tokens == 1234
    built = _FakeEngine.created[-1]
    assert built["user_id"] == caller
    assert built["env_vars"] == {}
    assert built["context"].user_id == caller
    assert built["context"].env_vars == {}


@pytest.mark.asyncio
async def test_author_caller_gets_env(db: async_sessionmaker[AsyncSession]) -> None:
    author, _, wf_id = await _setup(db, env={"SECRET": "author-only"})
    await RunWorkflowTool(workflow_ids=[wf_id], user_id=author).run(name="nightly_reconcile")
    assert _FakeEngine.created[-1]["env_vars"] == {"SECRET": "author-only"}


@pytest.mark.asyncio
async def test_over_quota_caller_is_refused(db: async_sessionmaker[AsyncSession]) -> None:
    _, caller, wf_id = await _setup(db)
    checked: list[str] = []

    async def _quota(user_id: str) -> None:
        checked.append(user_id)
        raise AppError("token_quota_exceeded", status_code=429)

    with patch("fim_one.web.api.chat._check_token_quota", new=_quota):
        out = await RunWorkflowTool(workflow_ids=[wf_id], user_id=caller).run(
            name="nightly_reconcile"
        )
    assert "quota exceeded" in out
    assert checked == [caller]
    assert await _runs(db) == []


@pytest.mark.asyncio
async def test_missing_user_is_refused(db: async_sessionmaker[AsyncSession]) -> None:
    _, _, wf_id = await _setup(db)
    out = await RunWorkflowTool(workflow_ids=[wf_id], user_id=None).run(
        name="nightly_reconcile"
    )
    assert "run_identity_missing" in out


@pytest.mark.asyncio
async def test_failure_surfaces_error(db: async_sessionmaker[AsyncSession]) -> None:
    _, caller, wf_id = await _setup(db)
    _FakeEngine.ending = ("run_failed", {"status": "failed", "error": "boom", "total_tokens": 77})
    out = await RunWorkflowTool(workflow_ids=[wf_id], user_id=caller).run(
        name="nightly_reconcile"
    )
    assert "failed" in out and "boom" in out
    (run,) = await _runs(db)
    assert run.total_tokens == 77


@pytest.mark.asyncio
async def test_reentrancy_cycle_prevented(db: async_sessionmaker[AsyncSession]) -> None:
    _, caller, wf_id = await _setup(db)
    token = _active_workflows.set(frozenset({wf_id}))
    try:
        out = await RunWorkflowTool(workflow_ids=[wf_id], user_id=caller).run(
            name="nightly_reconcile"
        )
    finally:
        _active_workflows.reset(token)
    assert "already running" in out


@pytest.mark.asyncio
async def test_depth_limit_enforced(db: async_sessionmaker[AsyncSession]) -> None:
    _, caller, wf_id = await _setup(db)
    deep = frozenset(f"other{i}" for i in range(_MAX_WORKFLOW_DEPTH))
    token = _active_workflows.set(deep)
    try:
        out = await RunWorkflowTool(workflow_ids=[wf_id], user_id=caller).run(
            name="nightly_reconcile"
        )
    finally:
        _active_workflows.reset(token)
    assert "nesting too deep" in out

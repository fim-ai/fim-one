"""Every workflow entry point gets the same answer from the run service (T10 P2).

Six entry points start runs: interactive (SSE), batch, trigger key, schedule,
the ``run_workflow`` agent tool and single-node test. Before P2 each one
assembled its own checks, and they disagreed (scheduled runs skipped the plan
gate, the tool ran as the author, the schedule auto-approved). Here each entry
point is driven for real against one scenario at a time and its result is
normalised to "refused with code X" or "ran as user Y".

Principals (decision 2026-11-04): trigger key and schedule run as the author;
interactive, batch and tool runs by a subscriber run as the subscriber;
single-node test is author-only and runs as the author.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.core.tool.builtin.run_workflow import RunWorkflowTool
from fim_one.core.workflow import run_service as rs
from fim_one.core.workflow.scheduler import WorkflowScheduler
from fim_one.db.base import Base
from fim_one.db.models.organization import Organization, OrgMembership
from fim_one.db.models.resource_subscription import ResourceSubscription
from fim_one.db.models.user import User
from fim_one.db.models.workflow import Workflow, WorkflowApproval, WorkflowRun
from fim_one.web.api import workflows as wf_api
from fim_one.web.exceptions import AppError
from fim_one.web.schemas.workflow import (
    NodeTestRequest,
    WorkflowBatchRunRequest,
    WorkflowRunRequest,
    WorkflowTriggerRequest,
)

ENTRIES = ["interactive", "batch", "trigger", "schedule", "tool", "test_node"]
# Entry points whose principal is the author.
AUTHOR_ENTRIES = {"trigger", "schedule", "test_node"}
RUN_LIMIT_SECONDS = 37


def _blueprint(middle: dict[str, Any]) -> dict[str, Any]:
    return {
        "nodes": [
            {"id": "start_1", "type": "start", "data": {"type": "START"}},
            middle,
            {"id": "end_1", "type": "end", "data": {"type": "END"}},
        ],
        "edges": [
            {"id": "e1", "source": "start_1", "target": middle["id"]},
            {"id": "e2", "source": middle["id"], "target": "end_1"},
        ],
    }


LLM_BLUEPRINT = _blueprint(
    {"id": "llm_1", "type": "llm", "data": {"type": "LLM", "prompt_template": "hi"}}
)
APPROVAL_BLUEPRINT = _blueprint(
    {
        "id": "human_1",
        "type": "humanIntervention",
        "data": {
            "type": "HUMAN_INTERVENTION",
            "title": "Sign off",
            "timeout_hours": 0.0003,  # ~1 s: the approval expires, the run ends
            "poll_interval_seconds": 0.05,
        },
    }
)


# ---------------------------------------------------------------------------
# World
# ---------------------------------------------------------------------------


@dataclass
class World:
    factory: async_sessionmaker[AsyncSession]
    author: User
    subscriber: User
    wf: Workflow
    engines: list[dict[str, Any]] = field(default_factory=list)


class _RecordingEngine:
    """Stands in for WorkflowEngine: records its arguments, spends 42 tokens."""

    sink: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, **kwargs: Any) -> None:
        self._kwargs = dict(kwargs)
        _RecordingEngine.sink.append(self._kwargs)

    async def execute_streaming(
        self, _parsed: Any, _inputs: Any, context: Any = None
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        self._kwargs["context"] = context
        yield "node_completed", {"node_id": "llm_1", "status": "completed"}
        yield "run_completed", {"status": "completed", "outputs": {"ok": 1}, "total_tokens": 42}


class _TokenSpendingExecutor:
    async def execute(self, node: Any, store: Any, context: Any) -> Any:
        from fim_one.core.workflow.types import NodeResult, NodeStatus

        await context.usage_tracker.record({"total_tokens": 42})
        return NodeResult(node_id=node.id, status=NodeStatus.COMPLETED, output="ok")


@pytest.fixture()
async def world() -> AsyncIterator[World]:
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

    async with factory() as s:
        author = User(id=str(uuid.uuid4()), email="author@x.io")
        subscriber = User(id=str(uuid.uuid4()), email="sub@x.io")
        s.add_all([author, subscriber])
        await s.flush()
        org = Organization(name="O", slug=f"o-{uuid.uuid4()}", owner_id=author.id)
        s.add(org)
        await s.flush()
        s.add(OrgMembership(org_id=org.id, user_id=subscriber.id, role="member"))
        wf = Workflow(
            user_id=author.id,
            name="reconcile",
            blueprint=LLM_BLUEPRINT,
            visibility="org",
            org_id=org.id,
            status="active",
            is_active=True,
            max_run_duration_seconds=RUN_LIMIT_SECONDS,
        )
        s.add(wf)
        await s.flush()
        s.add(
            ResourceSubscription(
                user_id=subscriber.id, resource_type="workflow", resource_id=wf.id, org_id=org.id
            )
        )
        await s.commit()

    _RecordingEngine.sink = []
    service = rs.WorkflowRunService(engine_factory=_RecordingEngine)
    with (
        patch("fim_one.db.create_session", _cs),
        patch.object(rs, "_default_service", service),
        patch("fim_one.web.api.chat._get_quota_status", new=AsyncMock(return_value=(0, 0))),
        patch(
            "fim_one.web.services.quota_enforcer.is_user_unentitled",
            new=AsyncMock(return_value=False),
        ),
    ):
        yield World(factory, author, subscriber, wf, _RecordingEngine.sink)
    await engine.dispose()


@pytest.fixture()
def real_engine() -> Iterator[None]:
    """Use the real WorkflowEngine (for the approval scenario)."""
    with patch.object(rs.get_run_service(), "_engine_factory", None):
        yield


# ---------------------------------------------------------------------------
# Driving the six entry points
# ---------------------------------------------------------------------------


@dataclass
class Result:
    refused: str | None  # error code when the run was refused before starting


async def _drive(entry: str, w: World) -> Result:
    async with w.factory() as db:
        wf = await db.get(Workflow, w.wf.id)
        assert wf is not None
        try:
            if entry == "interactive":
                request = MagicMock()
                request.is_disconnected = AsyncMock(return_value=False)
                resp = await wf_api.run_workflow(
                    wf.id,
                    WorkflowRunRequest(inputs={}),
                    request,
                    current_user=w.subscriber,
                    db=db,
                )
                assert isinstance(resp, StreamingResponse)
                async for _chunk in resp.body_iterator:
                    pass
            elif entry == "batch":
                await wf_api.batch_run_workflow(
                    wf.id,
                    WorkflowBatchRunRequest(inputs=[{}]),
                    current_user=w.subscriber,
                    db=db,
                )
            elif entry == "trigger":
                await wf_api._execute_trigger(wf, WorkflowTriggerRequest(inputs={}))
            elif entry == "schedule":
                await WorkflowScheduler(poll_interval=60)._execute_workflow(wf)
                async with w.factory() as s:
                    run = (await s.execute(select(WorkflowRun))).scalar_one_or_none()
                if run is not None and run.status == "failed" and run.duration_ms == 0:
                    return Result(refused=run.error)  # refusal recorded for the author
            elif entry == "tool":
                out = await RunWorkflowTool(
                    workflow_ids=[wf.id], user_id=w.subscriber.id
                ).run(name=wf.name)
                if out.startswith("[Error]"):
                    # Refusals end with "(<error_code>)".
                    return Result(refused=out.rsplit("(", 1)[-1].rstrip(")"))
            elif entry == "test_node":
                node_id = wf.blueprint["nodes"][1]["id"]
                with patch(
                    "fim_one.core.workflow.nodes.get_executor",
                    return_value=_TokenSpendingExecutor(),
                ):
                    await wf_api.test_node(
                        wf.id, NodeTestRequest(node_id=node_id), current_user=w.author, db=db
                    )
        except AppError as exc:
            return Result(refused=exc.error_code)
    await asyncio.sleep(0)
    return Result(refused=None)


async def _runs(w: World) -> list[WorkflowRun]:
    async with w.factory() as s:
        return list((await s.execute(select(WorkflowRun))).scalars().all())


def _expected_principal(entry: str, w: World) -> str:
    return str(w.author.id if entry in AUTHOR_ENTRIES else w.subscriber.id)


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ENTRIES)
async def test_over_quota_is_refused(entry: str, world: World) -> None:
    with patch(
        "fim_one.web.api.chat._get_quota_status", new=AsyncMock(return_value=(100, 50))
    ):
        result = await _drive(entry, world)
    assert result.refused == "token_quota_exceeded"
    assert world.engines == []
    assert all(r.total_tokens == 0 for r in await _runs(world))


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ENTRIES)
async def test_no_plan_is_refused(entry: str, world: World) -> None:
    with patch(
        "fim_one.web.services.quota_enforcer.is_user_unentitled",
        new=AsyncMock(return_value=True),
    ):
        result = await _drive(entry, world)
    assert result.refused == "subscription_required"
    assert world.engines == []


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ENTRIES)
async def test_disabled_author_is_refused(entry: str, world: World) -> None:
    async with world.factory() as s:
        author = await s.get(User, world.author.id)
        assert author is not None
        author.is_active = False
        await s.commit()
    result = await _drive(entry, world)
    assert result.refused == "workflow_owner_inactive"
    assert world.engines == []


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", [e for e in ENTRIES if e != "test_node"])
async def test_run_limit_comes_from_the_workflow(entry: str, world: World) -> None:
    """Single-node test is excluded: it is bounded by the node's own timeout."""
    result = await _drive(entry, world)
    assert result.refused is None
    (built,) = world.engines
    assert built["workflow_timeout_ms"] == RUN_LIMIT_SECONDS * 1000


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ENTRIES)
async def test_tokens_billed_to_the_principal(entry: str, world: World) -> None:
    result = await _drive(entry, world)
    assert result.refused is None
    (run,) = await _runs(world)
    assert run.total_tokens == 42
    assert run.user_id == _expected_principal(entry, world)
    assert run.mode == entry
    if entry != "test_node":
        (built,) = world.engines
        assert built["user_id"] == _expected_principal(entry, world)
        ctx = built["context"]
        assert ctx.user_id == _expected_principal(entry, world)
        assert ctx.db_session_factory is not None  # approval nodes persist


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ENTRIES)
async def test_approval_step(entry: str, world: World, real_engine: None) -> None:
    """Approval steps create a pending approval everywhere a run can wait.

    The agent tool cannot wait and refuses; a single-node test auto-approves
    (it has no run to attach an approval to).
    """
    async with world.factory() as s:
        wf = await s.get(Workflow, world.wf.id)
        assert wf is not None
        wf.blueprint = APPROVAL_BLUEPRINT
        await s.commit()

    result = await asyncio.wait_for(_drive(entry, world), timeout=30)

    async with world.factory() as s:
        approvals = list((await s.execute(select(WorkflowApproval))).scalars().all())
    if entry == "tool":
        assert result.refused == "workflow_has_approval_step"
        assert approvals == []
    elif entry == "test_node":
        assert result.refused is None
        assert approvals == []
    else:
        assert result.refused is None
        assert len(approvals) == 1
        assert approvals[0].status == "expired"
        (run,) = await _runs(world)
        assert run.id == approvals[0].workflow_run_id
        assert run.user_id == _expected_principal(entry, world)
        assert run.status == "failed"


# ---------------------------------------------------------------------------
# grep gate: entry points no longer assemble runs themselves
# ---------------------------------------------------------------------------

_SRC = Path(__file__).resolve().parents[1] / "src" / "fim_one"
_ENTRY_FILES = [
    _SRC / "web" / "api" / "workflows.py",
    _SRC / "core" / "workflow" / "scheduler.py",
    _SRC / "core" / "tool" / "builtin" / "run_workflow.py",
]


@pytest.mark.parametrize("path", _ENTRY_FILES, ids=lambda p: p.name)
@pytest.mark.parametrize(
    "needle", ["WorkflowEngine(", "decrypt_credential", "_get_quota_status"]
)
def test_entry_points_do_not_assemble_runs(path: Path, needle: str) -> None:
    assert needle not in path.read_text(encoding="utf-8")

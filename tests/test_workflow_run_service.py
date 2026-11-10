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
from fim_one.db.models.channel import ConfirmationRequest
from fim_one.db.models.organization import Organization, OrgMembership
from fim_one.db.models.resource_subscription import ResourceSubscription
from fim_one.db.models.user import User
from fim_one.db.models.workflow import Workflow, WorkflowRun
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
            "timeout_hours": 1,
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
async def world(tmp_path: Path) -> AsyncIterator[World]:
    import fim_one.db.models  # noqa: F401

    # A file, not ``:memory:``: the in-memory engine hands every session the
    # same connection, and runs that overlap (a batch, a run started beside
    # it) then commit into each other's statements.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'world.db'}")
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
        patch("fim_one.web.services.quota_backend.usage_and_cap", new=AsyncMock(return_value=(0, 0))),
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
        "fim_one.web.services.quota_backend.usage_and_cap", new=AsyncMock(return_value=(100, 50))
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


@pytest.mark.usefixtures("human_intervention_enabled")
@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ENTRIES)
async def test_approval_step(entry: str, world: World, real_engine: None) -> None:
    """Approval steps create a pending approval everywhere a run can wait,
    and the run stops there as ``waiting`` until it is decided.

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
        approvals = list(
            (
                await s.execute(
                    select(ConfirmationRequest).where(
                        ConfirmationRequest.kind == "workflow_approval"
                    )
                )
            )
            .scalars()
            .all()
        )
    if entry == "tool":
        assert result.refused == "workflow_has_approval_step"
        assert approvals == []
    elif entry == "test_node":
        assert result.refused is None
        assert approvals == []
    else:
        assert result.refused is None
        assert len(approvals) == 1
        assert approvals[0].status == "pending"
        (run,) = await _runs(world)
        assert run.id == approvals[0].workflow_run_id
        assert run.user_id == _expected_principal(entry, world)
        # Unassigned: the run's user decides, whoever authored the workflow.
        assert (approvals[0].eligible_scope, approvals[0].eligible_user_id) == (
            "initiator",
            run.user_id,
        )
        assert run.status == "waiting"
        assert run.completed_at is None
        assert run.suspend_state["waiting"]["human_1"]["confirmation_id"] == approvals[0].id
        # Nothing of the run is left in the process, and it holds no slot.
        service = rs.get_run_service()
        assert service.active_run_ids() == []
        assert not service.rate_limiter._active_runs.get(run.user_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ENTRIES)
async def test_approval_step_refused_while_switched_off(
    entry: str, world: World, real_engine: None
) -> None:
    """With Human Intervention off (the default), a stored workflow that
    contains the step is refused at every entry point before it starts: no
    engine, no approval, and the step is not skipped or auto-approved."""
    async with world.factory() as s:
        wf = await s.get(Workflow, world.wf.id)
        assert wf is not None
        wf.blueprint = APPROVAL_BLUEPRINT
        await s.commit()

    result = await asyncio.wait_for(_drive(entry, world), timeout=30)

    assert result.refused == "human_intervention_disabled"
    async with world.factory() as s:
        assert list((await s.execute(select(ConfirmationRequest))).scalars().all()) == []
    runs = await _runs(world)
    if entry == "schedule":
        # Nobody to tell: the refusal is recorded for the author.
        (run,) = runs
        assert (run.status, run.error) == ("failed", "human_intervention_disabled")
    else:
        assert runs == []


# ---------------------------------------------------------------------------
# Dry run: the answer a real run would get, without starting one
# ---------------------------------------------------------------------------


async def _dry_run(w: World, user: User) -> dict[str, Any]:
    async with w.factory() as db:
        resp = await wf_api.run_workflow(
            w.wf.id,
            WorkflowRunRequest(inputs={}, dry_run=True),
            MagicMock(),
            current_user=user,
            db=db,
        )
    data: dict[str, Any] = resp.data  # type: ignore[attr-defined]
    return data


async def _assert_nothing_started(w: World) -> None:
    """A dry run creates no run, starts no engine and uses no rate-limit budget."""
    assert await _runs(w) == []
    assert w.engines == []
    limiter = rs.get_run_service().rate_limiter
    assert not any(limiter._active_runs.values())
    assert not any(limiter._run_timestamps.values())


@pytest.mark.asyncio
async def test_dry_run_of_a_runnable_workflow_is_valid(world: World) -> None:
    for user in (world.author, world.subscriber):
        data = await _dry_run(world, user)
        assert (data["valid"], data["errors"], data["error_code"]) == (True, [], None)
    await _assert_nothing_started(world)


@pytest.mark.asyncio
async def test_dry_run_reports_a_switched_off_step(world: World) -> None:
    """A workflow whose real run is refused for a switched-off Human
    Intervention step is not reported as valid."""
    async with world.factory() as s:
        wf = await s.get(Workflow, world.wf.id)
        assert wf is not None
        wf.blueprint = APPROVAL_BLUEPRINT
        await s.commit()

    own = await _dry_run(world, world.author)
    assert own["valid"] is False
    assert own["error_code"] == "human_intervention_disabled"
    assert "switched off" in own["errors"][0]
    # The author still gets the plan beside the refusal.
    assert [p["node_id"] for p in own["execution_plan"]] == ["start_1", "human_1", "end_1"]

    theirs = await _dry_run(world, world.subscriber)
    assert theirs["valid"] is False
    assert theirs["error_code"] == "human_intervention_disabled"
    assert theirs["execution_plan"] == []
    await _assert_nothing_started(world)

    # The real run gives the same answer.
    assert (await _drive("interactive", world)).refused == "human_intervention_disabled"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [
        "token_quota_exceeded",
        "subscription_required",
        "workflow_owner_inactive",
        "account_disabled",
    ],
)
async def test_dry_run_reports_what_a_real_run_is_refused_for(
    scenario: str, world: World
) -> None:
    quota = (100, 50) if scenario == "token_quota_exceeded" else (0, 0)
    if scenario in ("workflow_owner_inactive", "account_disabled"):
        disabled = world.author if scenario == "workflow_owner_inactive" else world.subscriber
        async with world.factory() as s:
            user = await s.get(User, disabled.id)
            assert user is not None
            user.is_active = False
            await s.commit()

    with (
        patch("fim_one.web.services.quota_backend.usage_and_cap", new=AsyncMock(return_value=quota)),
        patch(
            "fim_one.web.services.quota_enforcer.is_user_unentitled",
            new=AsyncMock(return_value=scenario == "subscription_required"),
        ),
    ):
        data = await _dry_run(world, world.subscriber)
        real = await _drive("interactive", world)

    assert data["valid"] is False
    assert data["error_code"] == scenario
    assert len(data["errors"]) == 1
    assert real.refused == scenario
    await _assert_nothing_started(world)


# ---------------------------------------------------------------------------
# Batch runs and the concurrent-run limit
# ---------------------------------------------------------------------------


class _GatedEngine:
    """Engine whose runs stay active until ``release`` is set."""

    running = 0
    peak = 0
    release: ClassVar[asyncio.Event]

    def __init__(self, **_kwargs: Any) -> None:
        pass

    async def execute_streaming(
        self, _parsed: Any, _inputs: Any, context: Any = None
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        cls = _GatedEngine
        cls.running += 1
        cls.peak = max(cls.peak, cls.running)
        try:
            await cls.release.wait()
        finally:
            cls.running -= 1
        yield "run_completed", {"status": "completed", "outputs": {}, "total_tokens": 0}


async def _until(condition: Any, timeout: float = 5.0) -> None:
    async def _poll() -> None:
        while not condition():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), timeout=timeout)


@pytest.mark.asyncio
async def test_batch_items_count_toward_the_concurrent_limit(world: World) -> None:
    """A batch of 6 at max_parallel=5 holds 2 of the caller's 3 slots, not 5
    outside the limit: the third stays free for a run started by hand, a
    fourth run is refused, and every item still completes."""
    _GatedEngine.running = _GatedEngine.peak = 0
    _GatedEngine.release = asyncio.Event()
    service = rs.WorkflowRunService(engine_factory=_GatedEngine)
    assert service.rate_limiter.max_concurrent_runs == 3

    with patch.object(rs, "_default_service", service):
        async with world.factory() as db:
            wf = await db.get(Workflow, world.wf.id)
            assert wf is not None
            batch = asyncio.create_task(
                wf_api.batch_run_workflow(
                    wf.id,
                    WorkflowBatchRunRequest(inputs=[{"i": i} for i in range(6)], max_parallel=5),
                    current_user=world.subscriber,
                    db=db,
                )
            )
            await _until(lambda: _GatedEngine.running >= 2)
            await asyncio.sleep(0.1)  # time for more items to start, if they could
            assert _GatedEngine.peak == 2

            by_hand = await service.start_run(
                wf, caller_id=world.subscriber.id, inputs={}, mode=rs.RunMode.INTERACTIVE
            )
            with pytest.raises(rs.RunRefusedError) as exc:
                await service.start_run(
                    wf, caller_id=world.subscriber.id, inputs={}, mode=rs.RunMode.INTERACTIVE
                )
            assert exc.value.error_code == "workflow_rate_limited"

            _GatedEngine.release.set()
            response = await asyncio.wait_for(batch, timeout=10)
            await by_hand.wait()

    results = response.data["results"]
    assert [r["status"] for r in results] == ["completed"] * 6
    assert _GatedEngine.peak == 3  # 2 batch items + the run started by hand
    assert service.rate_limiter._active_runs[world.subscriber.id] == set()
    runs = await _runs(world)
    assert sorted(r.mode for r in runs) == ["batch"] * 6 + ["interactive"]


@pytest.mark.asyncio
async def test_batch_counts_once_against_runs_per_minute(world: World) -> None:
    """Submitting a batch uses one start of the per-minute allowance, and is
    refused once the allowance is spent."""
    service = rs.WorkflowRunService(
        engine_factory=_RecordingEngine,
        rate_limiter=rs.WorkflowRateLimiter(max_runs_per_minute=2, max_concurrent_runs=3),
    )
    with patch.object(rs, "_default_service", service):
        async with world.factory() as db:
            wf = await db.get(Workflow, world.wf.id)
            assert wf is not None
            for _ in range(2):
                ok = await wf_api.batch_run_workflow(
                    wf.id,
                    WorkflowBatchRunRequest(inputs=[{}, {}, {}]),
                    current_user=world.subscriber,
                    db=db,
                )
                assert [r["status"] for r in ok.data["results"]] == ["completed"] * 3
            with pytest.raises(AppError) as exc:
                await wf_api.batch_run_workflow(
                    wf.id,
                    WorkflowBatchRunRequest(inputs=[{}]),
                    current_user=world.subscriber,
                    db=db,
                )
    assert exc.value.error_code == "workflow_rate_limited"
    assert exc.value.status_code == 429
    assert len(await _runs(world)) == 6


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


# ---------------------------------------------------------------------------
# Run lifecycle: slots and records never leak
# ---------------------------------------------------------------------------


class _SlowEngine:
    """Waits for its cancel event, then reports a cancelled run."""

    def __init__(self, **kwargs: Any) -> None:
        self._cancel: asyncio.Event = kwargs["cancel_event"]

    async def execute_streaming(
        self, _parsed: Any, _inputs: Any, context: Any = None
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        await self._cancel.wait()
        yield "run_completed", {"status": "cancelled", "error": "Execution cancelled"}


def _service(engine_factory: Any) -> rs.WorkflowRunService:
    gate = MagicMock()
    gate.check = AsyncMock()
    return rs.WorkflowRunService(gate=gate, engine_factory=engine_factory)


class TestRunLifecycle:
    @pytest.mark.asyncio
    async def test_failed_start_releases_slot_and_closes_record(self, world: World) -> None:
        def _boom(**_: Any) -> Any:
            raise RuntimeError("engine unavailable")

        svc = _service(_boom)
        with pytest.raises(RuntimeError):
            await svc.start_run(
                world.wf, caller_id=world.subscriber.id, inputs={}, mode=rs.RunMode.INTERACTIVE
            )
        assert not svc.rate_limiter._active_runs.get(world.subscriber.id)
        (run,) = await _runs(world)
        assert run.status == "failed"
        assert run.error == "Run could not start"

    @pytest.mark.asyncio
    async def test_run_finishes_without_a_reader(self, world: World) -> None:
        svc = _service(_RecordingEngine)
        started = await svc.start_run(
            world.wf, caller_id=world.subscriber.id, inputs={}, mode=rs.RunMode.INTERACTIVE
        )
        # Nobody reads events(): the run still completes and frees its slot.
        await asyncio.wait_for(started.wait(), timeout=5)
        assert started.done
        assert not svc.rate_limiter._active_runs.get(world.subscriber.id)
        (run,) = await _runs(world)
        assert run.status == "completed"

    @pytest.mark.asyncio
    async def test_a_failed_run_keeps_the_text_for_its_record_and_a_code_for_callers(
        self, world: World
    ) -> None:
        class _FailingEngine:
            def __init__(self, **_kwargs: Any) -> None:
                pass

            async def execute_streaming(
                self, _parsed: Any, _inputs: Any, context: Any = None
            ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
                yield "node_failed", {"node_id": "db_1", "error": "[Error] relation missing"}
                yield (
                    "run_failed",
                    {
                        "status": "failed",
                        "error": "Nodes failed: ['db_1']",
                        "error_code": "workflow_step_failed",
                    },
                )

        svc = _service(_FailingEngine)
        started = await svc.start_run(
            world.wf, caller_id=None, inputs={}, mode=rs.RunMode.TRIGGER
        )
        outcome = await asyncio.wait_for(started.wait(), timeout=5)
        assert outcome.error_code == "workflow_step_failed"
        # The owner's run record has the step and what it said.
        (run,) = await _runs(world)
        assert run.error == "Nodes failed: ['db_1']"
        assert run.node_results["db_1"]["error"] == "[Error] relation missing"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", [rs.RunMode.BATCH, rs.RunMode.TOOL, rs.RunMode.TRIGGER])
    async def test_any_mode_can_be_cancelled(self, world: World, mode: rs.RunMode) -> None:
        svc = _service(_SlowEngine)
        started = await svc.start_run(
            world.wf, caller_id=world.subscriber.id, inputs={}, mode=mode, acquire_slot=False
        )
        assert svc.cancel_run(started.run_id) is True
        outcome = await asyncio.wait_for(started.wait(), timeout=5)
        assert outcome.status == "cancelled"
        assert outcome.error_code == "workflow_run_cancelled"
        assert svc.cancel_run(started.run_id) is False  # no longer registered
        (run,) = await _runs(world)
        assert run.status == "cancelled"

    @pytest.mark.asyncio
    async def test_closing_the_stream_does_not_stop_the_run(self, world: World) -> None:
        """T10 W7: a closed tab or a dropped connection is not a cancel."""
        release = asyncio.Event()

        class _Held(_SlowEngine):
            async def execute_streaming(
                self, _parsed: Any, _inputs: Any, context: Any = None
            ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
                yield "node_completed", {"node_id": "llm_1", "status": "completed"}
                await release.wait()
                assert not self._cancel.is_set()
                yield "run_completed", {"status": "completed", "outputs": {"ok": 1}}

        svc = _service(_Held)
        request = MagicMock()
        request.is_disconnected = AsyncMock(return_value=False)
        with patch.object(rs, "_default_service", svc):
            async with world.factory() as db:
                resp = await wf_api.run_workflow(
                    world.wf.id,
                    WorkflowRunRequest(inputs={}),
                    request,
                    current_user=world.subscriber,
                    db=db,
                )
        assert isinstance(resp, StreamingResponse)
        stream = resp.body_iterator
        first = await stream.__anext__()  # type: ignore[union-attr]
        assert "run_started" in str(first)
        await stream.aclose()  # type: ignore[union-attr]  # client went away

        await asyncio.sleep(0.1)
        (run,) = await _runs(world)
        assert run.status == "running"  # still going, with nobody reading
        assert list(svc._active_runs) == [run.id]

        release.set()
        await _until(lambda: not svc._active_runs)
        (run,) = await _runs(world)
        assert run.status == "completed"
        assert run.outputs == {"ok": 1}

"""Tests for the HumanIntervention workflow node executor and approval API."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.workflow.nodes import HumanInterventionExecutor
from fim_one.core.workflow.types import (
    ExecutionContext,
    NodeStatus,
    NodeType,
    WorkflowNodeDef,
)
from fim_one.core.workflow.variable_store import VariableStore
from fim_one.db.base import Base
from fim_one.db.models.channel import ConfirmationRequest
from fim_one.db.models.organization import Organization, OrgMembership
from fim_one.db.models.user import User
from fim_one.db.models.workflow import Workflow, WorkflowRun

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(data: dict[str, Any] | None = None) -> WorkflowNodeDef:
    """Create a HumanIntervention node definition for testing."""
    return WorkflowNodeDef(
        id="human-1",
        type=NodeType.HUMAN_INTERVENTION,
        data=data or {
            "title": "Review this content",
            "description": "Please check the output",
            "timeout_hours": 0.001,  # very short for tests (~3.6s)
            "poll_interval_seconds": 0.1,
        },
    )


@pytest.fixture()
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A real database: the executor asks the confirmation service and reads the outcome."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'hi.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.fixture()
async def world(factory: async_sessionmaker[AsyncSession]) -> dict[str, str]:
    """A runner and a colleague in one organization, a workflow and a run."""
    runner, colleague, outsider, org = (str(uuid.uuid4()) for _ in range(4))
    async with factory() as db:
        for uid, name in ((runner, "runner"), (colleague, "colleague"), (outsider, "outsider")):
            db.add(User(id=uid, username=name, email=f"{name}-{uid[:6]}@x.io"))
        db.add(Organization(id=org, name="Org", slug=f"org-{org[:6]}", owner_id=runner))
        await db.flush()
        for uid in (runner, colleague):
            db.add(OrgMembership(id=str(uuid.uuid4()), org_id=org, user_id=uid, role="member"))
        wf = Workflow(
            id="wf-1", user_id=runner, org_id=org, name="wf", blueprint={"nodes": [], "edges": []}
        )
        db.add(wf)
        await db.flush()
        db.add(
            WorkflowRun(
                id="run-1",
                workflow_id="wf-1",
                user_id=runner,
                blueprint_snapshot={"nodes": [], "edges": []},
                status="running",
            )
        )
        await db.commit()
    return {"runner": runner, "colleague": colleague, "outsider": outsider, "org": org}


@asynccontextmanager
async def _opens(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as s:
        yield s


def _make_context(
    factory: async_sessionmaker[AsyncSession] | None, user_id: str = "user-1"
) -> ExecutionContext:
    return ExecutionContext(
        run_id="run-1",
        user_id=user_id,
        workflow_id="wf-1",
        db_session_factory=(lambda: _opens(factory)) if factory is not None else None,
    )


class _Step:
    """One approval step of a run: asked once, then run again for the outcome.

    The executor does not wait. Its first call makes the request and
    returns WAITING with what it waits on; the engine keeps that and hands
    it back (``ExecutionContext.suspensions``) when it runs the step again.
    """

    def __init__(self, node: WorkflowNodeDef, store: VariableStore, ctx: ExecutionContext) -> None:
        self.executor = HumanInterventionExecutor()
        self.node, self.store, self.ctx = node, store, ctx

    async def ask(self) -> Any:
        result = await self.executor.execute(self.node, self.store, self.ctx)
        assert result.status == NodeStatus.WAITING, result
        assert result.suspension is not None and result.suspension["kind"] == "workflow_approval"
        self.ctx.suspensions[self.node.id] = result.suspension
        return result

    async def again(self) -> Any:
        return await self.executor.execute(self.node, self.store, self.ctx)


async def _pending_approval(
    factory: async_sessionmaker[AsyncSession], *, attempts: int = 100
) -> ConfirmationRequest:
    for _ in range(attempts):
        async with factory() as db:
            row = (
                await db.execute(
                    select(ConfirmationRequest).where(
                        ConfirmationRequest.kind == "workflow_approval"
                    )
                )
            ).scalar_one_or_none()
        if row is not None:
            return row
        await asyncio.sleep(0.02)
    raise AssertionError("no approval was created")


async def _decide(
    factory: async_sessionmaker[AsyncSession],
    row_id: str,
    *,
    status: str,
    by: str | None = None,
    note: str | None = None,
) -> None:
    async with factory() as db:
        row = await db.get(ConfirmationRequest, row_id)
        assert row is not None
        row.status = status
        row.decided_by_user_id = by
        row.decision_note = note
        await db.commit()


# ---------------------------------------------------------------------------
# Test: missing db_session_factory fails gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_db_session_factory_auto_approves() -> None:
    """Executor should auto-approve when db_session_factory is not provided."""
    node = _make_node()
    store = VariableStore()
    ctx = _make_context(None)

    result = await HumanInterventionExecutor().execute(node, store, ctx)

    assert result.status == NodeStatus.COMPLETED
    assert result.output["auto_approved"] is True
    assert result.output["status"] == "approved"


# ---------------------------------------------------------------------------
# Test: approval request is created on execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_record_created(
    factory: async_sessionmaker[AsyncSession], world: dict[str, str]
) -> None:
    """The executor records a workflow approval for the run and returns WAITING."""
    node = _make_node({
        "title": "Review content",
        "description": "Check output quality",
        "timeout_hours": 1,
        "poll_interval_seconds": 0.05,
    })
    store = VariableStore()
    ctx = _make_context(factory, world["runner"])
    step = _Step(node, store, ctx)
    asked = await step.ask()

    created = await _pending_approval(factory)
    assert asked.suspension["confirmation_id"] == created.id
    assert asked.suspension["expires_at"] is not None
    assert created.title == "Review content"
    assert created.description == "Check output quality"
    assert created.status == "pending"
    assert created.workflow_run_id == "run-1"
    assert created.node_id == "human-1"
    assert created.user_id == world["runner"]
    # Unassigned: the run's user decides.
    assert (created.eligible_scope, created.eligible_user_id) == ("initiator", world["runner"])
    assert created.mode == "inline" and created.channel_id is None
    assert created.payload == {"timeout_hours": 1.0, "assignee": None}

    await _decide(factory, created.id, status="approved", by="reviewer-1", note="LGTM")
    result = await step.again()

    assert result.status == NodeStatus.COMPLETED
    assert result.output["status"] == "approved"
    assert result.output["approval_id"] == created.id
    assert result.output["decision_by"] == "reviewer-1"
    assert result.output["decision_note"] == "LGTM"
    assert await store.get("approval_result") == result.output


# ---------------------------------------------------------------------------
# Test: polling detects approval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_polling_detects_approval(
    factory: async_sessionmaker[AsyncSession], world: dict[str, str]
) -> None:
    """Run again after the decision, the step completes with it."""
    node = _make_node({
        "title": "Quick approval",
        "timeout_hours": 1,
        "poll_interval_seconds": 0.05,
        "output_variable": "approval_result",
    })
    store = VariableStore()
    ctx = _make_context(factory, world["runner"])
    step = _Step(node, store, ctx)
    asked = await step.ask()

    created = await _pending_approval(factory)
    # Run again while nobody has decided: still waiting, on the same request.
    still = await step.again()
    assert still.status == NodeStatus.WAITING and still.suspension == asked.suspension
    await _decide(factory, created.id, status="approved", by="mgr-1", note="Approved!")
    result = await step.again()

    assert result.status == NodeStatus.COMPLETED
    assert result.output["decision_by"] == "mgr-1"
    assert result.output["decision_note"] == "Approved!"


# ---------------------------------------------------------------------------
# Test: polling detects rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_polling_detects_rejection(
    factory: async_sessionmaker[AsyncSession], world: dict[str, str]
) -> None:
    """Executor should fail the node when approval is rejected."""
    node = _make_node({"title": "Reject me", "timeout_hours": 1, "poll_interval_seconds": 0.05})
    store = VariableStore()
    ctx = _make_context(factory, world["runner"])
    step = _Step(node, store, ctx)
    asked = await step.ask()

    created = await _pending_approval(factory)
    await _decide(factory, created.id, status="rejected", by="reviewer", note="Not good enough")
    result = await step.again()

    assert result.status == NodeStatus.FAILED
    assert "Approval rejected: Not good enough" in (result.error or "")


# ---------------------------------------------------------------------------
# Test: timeout expires approval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_expires_approval(
    factory: async_sessionmaker[AsyncSession], world: dict[str, str]
) -> None:
    """Executor should mark approval as expired when timeout is reached."""
    node = _make_node({
        "title": "Will timeout",
        "timeout_hours": 0.00001,  # ~0.04 s
        "poll_interval_seconds": 0.02,
    })
    store = VariableStore()
    step = _Step(node, store, _make_context(factory, world["runner"]))
    await step.ask()
    await asyncio.sleep(0.1)  # past the request's deadline
    result = await step.again()

    assert result.status == NodeStatus.FAILED
    assert "timed out" in (result.error or "").lower()
    async with factory() as db:
        row = (await db.execute(select(ConfirmationRequest))).scalar_one()
    assert row.status == "expired"
    assert row.responded_at is not None


# ---------------------------------------------------------------------------
# Test: deleted approval record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleted_approval_record(
    factory: async_sessionmaker[AsyncSession], world: dict[str, str]
) -> None:
    """If the approval record vanishes, executor should fail gracefully."""
    node = _make_node(
        {"title": "Vanishing approval", "timeout_hours": 1, "poll_interval_seconds": 0.05}
    )
    store = VariableStore()
    ctx = _make_context(factory, world["runner"])
    step = _Step(node, store, ctx)
    asked = await step.ask()

    created = await _pending_approval(factory)
    async with factory() as db:
        row = await db.get(ConfirmationRequest, created.id)
        assert row is not None
        await db.delete(row)
        await db.commit()
    result = await step.again()

    assert result.status == NodeStatus.FAILED
    assert "not found" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# Test: variable interpolation in title/description/assignee
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_variable_interpolation(
    factory: async_sessionmaker[AsyncSession], world: dict[str, str]
) -> None:
    """Node data templates should be interpolated from the variable store."""
    node = _make_node({
        "title": "Review by {{manager_name}}",
        "description": "Check {{content_type}} content",
        "assignee": "{{manager_id}}",
        "timeout_hours": 1,
        "poll_interval_seconds": 0.05,
    })
    store = VariableStore()
    await store.set("manager_name", "Alice")
    await store.set("content_type", "marketing")
    await store.set("manager_id", world["colleague"])
    ctx = _make_context(factory, world["runner"])
    step = _Step(node, store, ctx)
    asked = await step.ask()

    record = await _pending_approval(factory)
    assert record.title == "Review by Alice"
    assert record.description == "Check marketing content"
    # Assigned: the colleague decides, not the runner.
    assert (record.eligible_scope, record.eligible_user_id) == ("assignee", world["colleague"])
    assert record.payload["assignee"] == world["colleague"]
    await _decide(factory, record.id, status="approved", by="boss")
    result = await step.again()
    assert result.status == NodeStatus.COMPLETED


@pytest.mark.asyncio
async def test_an_assignee_filled_in_as_a_username_is_recorded_as_the_user(
    factory: async_sessionmaker[AsyncSession], world: dict[str, str]
) -> None:
    """A run that fills the assignee in with a username stores the user id as decider."""
    node = _make_node({
        "title": "Sign-off",
        "assignee": "{{who}}",
        "timeout_hours": 1,
        "poll_interval_seconds": 0.05,
    })
    store = VariableStore()
    await store.set("who", "colleague")
    step = _Step(node, store, _make_context(factory, world["runner"]))
    await step.ask()

    record = await _pending_approval(factory)
    assert (record.eligible_scope, record.eligible_user_id) == ("assignee", world["colleague"])
    # What the run filled in is kept as written.
    assert record.payload["assignee"] == "colleague"
    await _decide(factory, record.id, status="approved", by=world["colleague"])
    assert (await step.again()).status == NodeStatus.COMPLETED


# ---------------------------------------------------------------------------
# Test: an assignee outside the run's organization fails the node (B13)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assignee_outside_the_organization_fails_the_node(
    factory: async_sessionmaker[AsyncSession], world: dict[str, str]
) -> None:
    """The interpolated assignee is checked against the run's organization."""
    node = _make_node({"title": "Sign-off", "assignee": "{{who}}", "timeout_hours": 1})
    store = VariableStore()
    await store.set("who", world["outsider"])

    result = await HumanInterventionExecutor().execute(
        node, store, _make_context(factory, world["runner"])
    )

    assert result.status == NodeStatus.FAILED
    assert "not a member of the workflow's organization" in (result.error or "")
    async with factory() as db:
        assert (await db.execute(select(ConfirmationRequest))).scalars().all() == []


# ---------------------------------------------------------------------------
# Test: executor is registered in EXECUTOR_REGISTRY
# ---------------------------------------------------------------------------


def test_executor_registered() -> None:
    """HumanInterventionExecutor should be in the EXECUTOR_REGISTRY."""
    from fim_one.core.workflow.nodes import EXECUTOR_REGISTRY

    assert NodeType.HUMAN_INTERVENTION in EXECUTOR_REGISTRY
    assert EXECUTOR_REGISTRY[NodeType.HUMAN_INTERVENTION] is HumanInterventionExecutor


# ---------------------------------------------------------------------------
# Test: get_executor returns the right instance
# ---------------------------------------------------------------------------


def test_get_executor() -> None:
    """get_executor(HUMAN_INTERVENTION) should return HumanInterventionExecutor."""
    from fim_one.core.workflow.nodes import get_executor

    executor = get_executor(NodeType.HUMAN_INTERVENTION)
    assert isinstance(executor, HumanInterventionExecutor)


# ---------------------------------------------------------------------------
# Test: Approval schemas
# ---------------------------------------------------------------------------


def test_approval_response_schema() -> None:
    """WorkflowApprovalResponse should serialize correctly."""
    from fim_one.web.schemas.workflow import WorkflowApprovalResponse

    resp = WorkflowApprovalResponse(
        id="ap-1",
        workflow_run_id="run-1",
        node_id="human-1",
        title="Review content",
        description="Check quality",
        status="pending",
        assignee="user-1",
        decision_by=None,
        decision_note=None,
        timeout_hours=24.0,
        created_at="2026-01-01T00:00:00",
        resolved_at=None,
        updated_at=None,
    )
    data = resp.model_dump()
    assert data["id"] == "ap-1"
    assert data["status"] == "pending"
    assert data["timeout_hours"] == 24.0
    assert data["assignee"] == "user-1"


def test_approval_decision_request_schema() -> None:
    """ApprovalDecisionRequest should accept optional note."""
    from fim_one.web.schemas.workflow import ApprovalDecisionRequest

    # With note
    req = ApprovalDecisionRequest(note="Looks good")
    assert req.note == "Looks good"

    # Without note
    req = ApprovalDecisionRequest()
    assert req.note is None


# ---------------------------------------------------------------------------
# Test: NodeType enum includes HUMAN_INTERVENTION
# ---------------------------------------------------------------------------


def test_node_type_enum() -> None:
    """NodeType should have HUMAN_INTERVENTION variant."""
    assert NodeType.HUMAN_INTERVENTION.value == "HUMAN_INTERVENTION"

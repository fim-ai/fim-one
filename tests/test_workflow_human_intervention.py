"""Tests for the HumanIntervention workflow node executor and approval API."""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.core.workflow.nodes import HumanInterventionExecutor
from fim_one.core.workflow.types import (
    ExecutionContext,
    NodeStatus,
    NodeType,
    WorkflowNodeDef,
)
from fim_one.core.workflow.variable_store import VariableStore


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


class FakeApprovalRecord:
    """Minimal mock of a WorkflowApproval ORM row."""

    def __init__(
        self,
        *,
        approval_id: str = "",
        status: str = "pending",
        decision_by: str | None = None,
        decision_note: str | None = None,
    ) -> None:
        self.id = approval_id or str(uuid.uuid4())
        self.workflow_run_id = "run-1"
        self.node_id = "human-1"
        self.title = "Review"
        self.description = None
        self.assignee = None
        self.status = status
        self.decision_by = decision_by
        self.decision_note = decision_note
        self.timeout_hours = 24.0
        self.created_at = datetime.now(timezone.utc)
        self.resolved_at = None
        self.updated_at = None


class FakeDBSession:
    """Minimal async DB session mock for testing the executor."""

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.committed = False
        self._query_results: list[Any] = []  # queue of results
        self._poll_count = 0

    def set_query_results(self, results: list[Any]) -> None:
        """Set the sequence of results returned by execute().scalar_one_or_none()."""
        self._query_results = list(results)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed = True

    async def execute(self, stmt: Any) -> Any:
        self._poll_count += 1
        mock_result = MagicMock()
        if self._query_results:
            record = self._query_results.pop(0)
        else:
            record = None
        mock_result.scalar_one_or_none.return_value = record
        return mock_result


@asynccontextmanager
async def fake_db_session_factory():
    """Yield a fresh FakeDBSession."""
    yield FakeDBSession()


def _make_context(
    db_session_factory: Any = None,
) -> ExecutionContext:
    return ExecutionContext(
        run_id="run-1",
        user_id="user-1",
        workflow_id="wf-1",
        db_session_factory=db_session_factory,
    )


# ---------------------------------------------------------------------------
# Test: missing db_session_factory fails gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_db_session_factory_auto_approves() -> None:
    """Executor should auto-approve when db_session_factory is not provided."""
    executor = HumanInterventionExecutor()
    node = _make_node()
    store = VariableStore()
    ctx = _make_context(db_session_factory=None)

    result = await executor.execute(node, store, ctx)

    assert result.status == NodeStatus.COMPLETED
    assert result.output["auto_approved"] is True
    assert result.output["status"] == "approved"


# ---------------------------------------------------------------------------
# Test: approval record is created on execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_record_created() -> None:
    """Executor should create a WorkflowApproval record and poll for it."""
    created_records: list[Any] = []

    # Track what gets added to the session
    class TrackingSession:
        def __init__(self) -> None:
            self.added: list[Any] = []

        def add(self, obj: Any) -> None:
            self.added.append(obj)
            created_records.append(obj)

        async def commit(self) -> None:
            pass

        async def execute(self, stmt: Any) -> Any:
            # Return an approved record on first poll
            mock_result = MagicMock()
            record = FakeApprovalRecord(
                status="approved",
                decision_by="reviewer-1",
                decision_note="LGTM",
            )
            mock_result.scalar_one_or_none.return_value = record
            return mock_result

    @asynccontextmanager
    async def session_factory():
        yield TrackingSession()

    executor = HumanInterventionExecutor()
    node = _make_node({
        "title": "Review content",
        "description": "Check output quality",
        "timeout_hours": 1,
        "poll_interval_seconds": 0.05,
    })
    store = VariableStore()
    ctx = _make_context(db_session_factory=session_factory)

    result = await executor.execute(node, store, ctx)

    # A record was created
    assert len(created_records) == 1
    created = created_records[0]
    assert created.title == "Review content"
    assert created.description == "Check output quality"
    assert created.status == "pending"
    assert created.workflow_run_id == "run-1"
    assert created.node_id == "human-1"

    # Result should be completed (approval was found)
    assert result.status == NodeStatus.COMPLETED
    assert result.output["status"] == "approved"
    assert result.output["decision_by"] == "reviewer-1"


# ---------------------------------------------------------------------------
# Test: polling detects approval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_polling_detects_approval() -> None:
    """Executor should poll until it finds an approved status."""
    poll_count = 0

    class PollSession:
        def add(self, obj: Any) -> None:
            pass

        async def commit(self) -> None:
            pass

        async def execute(self, stmt: Any) -> Any:
            nonlocal poll_count
            poll_count += 1
            mock_result = MagicMock()
            # First 2 polls return pending, third returns approved
            if poll_count <= 2:
                record = FakeApprovalRecord(status="pending")
            else:
                record = FakeApprovalRecord(
                    status="approved",
                    decision_by="mgr-1",
                    decision_note="Approved!",
                )
            mock_result.scalar_one_or_none.return_value = record
            return mock_result

    @asynccontextmanager
    async def session_factory():
        yield PollSession()

    executor = HumanInterventionExecutor()
    node = _make_node({
        "title": "Quick approval",
        "timeout_hours": 1,
        "poll_interval_seconds": 0.05,
    })
    store = VariableStore()
    ctx = _make_context(db_session_factory=session_factory)

    result = await executor.execute(node, store, ctx)

    assert result.status == NodeStatus.COMPLETED
    assert result.output["status"] == "approved"
    # Should have polled at least 3 times (2 pending + 1 approved)
    assert poll_count >= 3

    # Check variable store was updated
    stored = await store.get("human-1.output")
    assert stored["status"] == "approved"


# ---------------------------------------------------------------------------
# Test: polling detects rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_polling_detects_rejection() -> None:
    """Executor should fail the node when approval is rejected."""
    poll_count = 0

    class RejectSession:
        def add(self, obj: Any) -> None:
            pass

        async def commit(self) -> None:
            pass

        async def execute(self, stmt: Any) -> Any:
            nonlocal poll_count
            poll_count += 1
            mock_result = MagicMock()
            if poll_count <= 1:
                record = FakeApprovalRecord(status="pending")
            else:
                record = FakeApprovalRecord(
                    status="rejected",
                    decision_by="mgr-1",
                    decision_note="Content not appropriate",
                )
            mock_result.scalar_one_or_none.return_value = record
            return mock_result

    @asynccontextmanager
    async def session_factory():
        yield RejectSession()

    executor = HumanInterventionExecutor()
    node = _make_node({
        "title": "Review",
        "timeout_hours": 1,
        "poll_interval_seconds": 0.05,
    })
    store = VariableStore()
    ctx = _make_context(db_session_factory=session_factory)

    result = await executor.execute(node, store, ctx)

    assert result.status == NodeStatus.FAILED
    assert "rejected" in (result.error or "").lower()
    assert "Content not appropriate" in (result.error or "")


# ---------------------------------------------------------------------------
# Test: timeout expires approval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_expires_approval() -> None:
    """Executor should mark approval as expired when timeout is reached."""
    expired_records: list[Any] = []

    class TimeoutSession:
        def add(self, obj: Any) -> None:
            pass

        async def commit(self) -> None:
            pass

        async def execute(self, stmt: Any) -> Any:
            mock_result = MagicMock()
            record = FakeApprovalRecord(status="pending")
            # Track records that get modified
            expired_records.append(record)
            mock_result.scalar_one_or_none.return_value = record
            return mock_result

    @asynccontextmanager
    async def session_factory():
        yield TimeoutSession()

    executor = HumanInterventionExecutor()
    # Use an extremely short timeout (0.0001 hours ~ 0.36 seconds)
    node = _make_node({
        "title": "Will timeout",
        "timeout_hours": 0.0001,
        "poll_interval_seconds": 0.05,
    })
    store = VariableStore()
    ctx = _make_context(db_session_factory=session_factory)

    result = await executor.execute(node, store, ctx)

    assert result.status == NodeStatus.FAILED
    assert "timed out" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# Test: deleted approval record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleted_approval_record() -> None:
    """If the approval record vanishes, executor should fail gracefully."""
    poll_count = 0

    class DeletedSession:
        def add(self, obj: Any) -> None:
            pass

        async def commit(self) -> None:
            pass

        async def execute(self, stmt: Any) -> Any:
            nonlocal poll_count
            poll_count += 1
            mock_result = MagicMock()
            if poll_count <= 1:
                # First poll returns pending
                mock_result.scalar_one_or_none.return_value = FakeApprovalRecord(
                    status="pending"
                )
            else:
                # Second poll: record is gone
                mock_result.scalar_one_or_none.return_value = None
            return mock_result

    @asynccontextmanager
    async def session_factory():
        yield DeletedSession()

    executor = HumanInterventionExecutor()
    node = _make_node({
        "title": "Vanishing approval",
        "timeout_hours": 1,
        "poll_interval_seconds": 0.05,
    })
    store = VariableStore()
    ctx = _make_context(db_session_factory=session_factory)

    result = await executor.execute(node, store, ctx)

    assert result.status == NodeStatus.FAILED
    assert "not found" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# Test: variable interpolation in title/description/assignee
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_variable_interpolation() -> None:
    """Node data templates should be interpolated from the variable store."""
    created_records: list[Any] = []

    class InterpolationSession:
        def add(self, obj: Any) -> None:
            created_records.append(obj)

        async def commit(self) -> None:
            pass

        async def execute(self, stmt: Any) -> Any:
            mock_result = MagicMock()
            record = FakeApprovalRecord(status="approved", decision_by="boss")
            mock_result.scalar_one_or_none.return_value = record
            return mock_result

    @asynccontextmanager
    async def session_factory():
        yield InterpolationSession()

    executor = HumanInterventionExecutor()
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
    await store.set("manager_id", "user-alice-123")
    ctx = _make_context(db_session_factory=session_factory)

    result = await executor.execute(node, store, ctx)

    assert result.status == NodeStatus.COMPLETED
    assert len(created_records) == 1
    record = created_records[0]
    assert record.title == "Review by Alice"
    assert record.description == "Check marketing content"
    assert record.assignee == "user-alice-123"


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
# Test: WorkflowApproval ORM model structure
# ---------------------------------------------------------------------------


def test_workflow_approval_model_fields() -> None:
    """WorkflowApproval model should have all required fields."""
    from fim_one.web.models.workflow import WorkflowApproval

    # Check the table name
    assert WorkflowApproval.__tablename__ == "workflow_approvals"

    # Check column names exist in the mapper
    mapper = WorkflowApproval.__mapper__
    column_names = {c.key for c in mapper.column_attrs}
    expected = {
        "id",
        "workflow_run_id",
        "node_id",
        "title",
        "description",
        "assignee",
        "status",
        "decision_by",
        "decision_note",
        "timeout_hours",
        "resolved_at",
        "created_at",
        "updated_at",
    }
    assert expected.issubset(column_names), f"Missing columns: {expected - column_names}"


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

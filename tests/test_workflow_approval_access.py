"""Who may see and decide a workflow approval.

An assigned approval belongs to its assignee.  An unassigned one belongs to
the user its run is attributed to.  Before this rule, an unassigned approval
was listed to, and could be decided by, every logged-in user on the instance
regardless of organization.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fim_one.db.base import Base
from fim_one.db.models.user import User
from fim_one.db.models.workflow import Workflow, WorkflowApproval, WorkflowRun
from fim_one.web.api.workflows import (
    approve_approval,
    list_pending_approvals,
    list_run_approvals,
    reject_approval,
)
from fim_one.web.exceptions import AppError
from fim_one.web.schemas.workflow import ApprovalDecisionRequest


@pytest.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    import fim_one.db.models  # noqa: F401

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _make_user(session: AsyncSession) -> User:
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4()}@x.io")
    session.add(user)
    await session.flush()
    return user


async def _make_approval(
    session: AsyncSession, run_user: User, assignee: str | None = None
) -> WorkflowApproval:
    wf = Workflow(user_id=run_user.id, name="wf", blueprint={"nodes": [], "edges": []})
    session.add(wf)
    await session.flush()
    run = WorkflowRun(
        workflow_id=wf.id,
        user_id=run_user.id,
        blueprint_snapshot={"nodes": [], "edges": []},
        status="running",
    )
    session.add(run)
    await session.flush()
    approval = WorkflowApproval(
        workflow_run_id=run.id,
        node_id="human_1",
        title="Approve payment",
        assignee=assignee,
        status="pending",
    )
    session.add(approval)
    await session.commit()
    return approval


def _ids(resp) -> set[str]:  # type: ignore[no-untyped-def]
    return {a["id"] for a in resp.data}


class TestUnassignedApproval:
    @pytest.mark.asyncio
    async def test_listed_only_to_run_user(self, session: AsyncSession) -> None:
        runner = await _make_user(session)
        stranger = await _make_user(session)
        approval = await _make_approval(session, runner)

        assert approval.id in _ids(
            await list_pending_approvals(current_user=runner, db=session)
        )
        assert approval.id not in _ids(
            await list_pending_approvals(current_user=stranger, db=session)
        )

    @pytest.mark.asyncio
    async def test_stranger_cannot_approve(self, session: AsyncSession) -> None:
        runner = await _make_user(session)
        stranger = await _make_user(session)
        approval = await _make_approval(session, runner)

        with pytest.raises(AppError) as exc:
            await approve_approval(
                approval.id, ApprovalDecisionRequest(), current_user=stranger, db=session
            )
        assert exc.value.error_code == "approval_not_found"
        await session.refresh(approval)
        assert approval.status == "pending"

    @pytest.mark.asyncio
    async def test_stranger_cannot_reject(self, session: AsyncSession) -> None:
        runner = await _make_user(session)
        stranger = await _make_user(session)
        approval = await _make_approval(session, runner)

        with pytest.raises(AppError) as exc:
            await reject_approval(
                approval.id, ApprovalDecisionRequest(), current_user=stranger, db=session
            )
        assert exc.value.error_code == "approval_not_found"

    @pytest.mark.asyncio
    async def test_run_user_can_approve(self, session: AsyncSession) -> None:
        runner = await _make_user(session)
        approval = await _make_approval(session, runner)

        resp = await approve_approval(
            approval.id, ApprovalDecisionRequest(), current_user=runner, db=session
        )
        assert resp.data["status"] == "approved"
        assert resp.data["decision_by"] == runner.id


class TestAssignedApproval:
    @pytest.mark.asyncio
    async def test_assignee_sees_and_decides(self, session: AsyncSession) -> None:
        runner = await _make_user(session)
        assignee = await _make_user(session)
        approval = await _make_approval(session, runner, assignee=assignee.id)

        assert approval.id in _ids(
            await list_pending_approvals(current_user=assignee, db=session)
        )
        resp = await reject_approval(
            approval.id, ApprovalDecisionRequest(), current_user=assignee, db=session
        )
        assert resp.data["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_run_user_cannot_decide_assigned(self, session: AsyncSession) -> None:
        runner = await _make_user(session)
        assignee = await _make_user(session)
        approval = await _make_approval(session, runner, assignee=assignee.id)

        assert approval.id not in _ids(
            await list_pending_approvals(current_user=runner, db=session)
        )
        with pytest.raises(AppError) as exc:
            await approve_approval(
                approval.id, ApprovalDecisionRequest(), current_user=runner, db=session
            )
        assert exc.value.error_code == "approval_not_assigned"


class TestRunApprovalListing:
    @pytest.mark.asyncio
    async def test_other_users_run_hides_unassigned(self, session: AsyncSession) -> None:
        runner = await _make_user(session)
        viewer = await _make_user(session)
        approval = await _make_approval(session, runner)
        run = await session.get(WorkflowRun, approval.workflow_run_id)
        assert run is not None

        with patch(
            "fim_one.web.api.workflows._get_accessible_workflow", AsyncMock()
        ):
            seen_by_viewer = await list_run_approvals(
                run.workflow_id, run.id, current_user=viewer, db=session
            )
            seen_by_runner = await list_run_approvals(
                run.workflow_id, run.id, current_user=runner, db=session
            )
        assert _ids(seen_by_viewer) == set()
        assert _ids(seen_by_runner) == {approval.id}


class TestMissingRunRow:
    @pytest.mark.asyncio
    async def test_assignee_still_decides(self, session: AsyncSession) -> None:
        assignee = await _make_user(session)
        approval = WorkflowApproval(
            workflow_run_id=str(uuid.uuid4()),  # run row never written
            node_id="human_1",
            title="t",
            assignee=assignee.id,
            status="pending",
        )
        session.add(approval)
        await session.commit()

        assert approval.id in _ids(
            await list_pending_approvals(current_user=assignee, db=session)
        )
        resp = await approve_approval(
            approval.id, ApprovalDecisionRequest(), current_user=assignee, db=session
        )
        assert resp.data["status"] == "approved"

"""Who may see and decide a workflow approval.

An assigned approval belongs to its assignee. An unassigned one belongs to
the user its run is attributed to, and not to the workflow's author when
someone else ran it. A platform administrator may decide either. An
assignee must be an active user in the run's organization, which is
checked when the approval is created and when a blueprint naming one
literally is saved.

Approvals are confirmation requests of ``kind="workflow_approval"``
created through ``ConfirmationService``; the endpoints are a view onto it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.assembly.confirmation import ConfirmationService, ConfirmationSpec
from fim_one.core.assembly.errors import AssemblyRefusal
from fim_one.core.assembly.principal import AuthMethod, Binding, resolve_principal
from fim_one.db.base import Base
from fim_one.db.models.channel import ConfirmationRequest
from fim_one.db.models.organization import Organization, OrgMembership
from fim_one.db.models.user import User
from fim_one.db.models.workflow import Workflow, WorkflowRun
from fim_one.web.api.confirmations import ConfirmationRespondRequest, respond_to_confirmation
from fim_one.web.api.workflows import (
    _approval_to_response,
    approval_inbox,
    approval_inbox_count,
    clear_workflow_runs,
    delete_workflow,
    delete_workflow_run,
    ensure_assignees_valid,
    list_run_approvals,
    publish_workflow,
)
from fim_one.web.exceptions import AppError
from fim_one.web.schemas.common import PublishRequest


# How the portal lists and decides approvals since T10 P5: the approval
# inbox lists them, and deciding is the confirmations endpoint every other
# request uses. The helpers keep the tests below in the shape they had when
# workflows had approval endpoints of their own.


@dataclass
class ApprovalDecisionRequest:
    note: str | None = None


async def list_pending_approvals(*, current_user: User, db: AsyncSession) -> Any:
    inbox = await approval_inbox(current_user=current_user, db=db)
    count = await approval_inbox_count(current_user=current_user, db=db)
    assert count.data["count"] == inbox.data["count"] == len(inbox.data["items"])
    return SimpleNamespace(data=inbox.data["items"])


async def _decide(
    decision: str, approval_id: str, body: ApprovalDecisionRequest, current_user: User, db: Any
) -> Any:
    await respond_to_confirmation(
        approval_id,
        ConfirmationRespondRequest(decision=decision, reason=body.note),  # type: ignore[arg-type]
        current_user=current_user,
        db=db,
    )
    row = (
        await db.execute(
            select(ConfirmationRequest)
            .where(ConfirmationRequest.id == approval_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return SimpleNamespace(data=_approval_to_response(row).model_dump())


async def approve_approval(
    approval_id: str, body: ApprovalDecisionRequest, *, current_user: User, db: Any
) -> Any:
    return await _decide("approve", approval_id, body, current_user, db)


async def reject_approval(
    approval_id: str, body: ApprovalDecisionRequest, *, current_user: User, db: Any
) -> Any:
    return await _decide("reject", approval_id, body, current_user, db)


@pytest.fixture()
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'approvals.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _create_session() -> AsyncIterator[AsyncSession]:
        async with maker() as s:
            yield s

    with patch("fim_one.db.create_session", _create_session):
        yield maker
    await engine.dispose()


@pytest.fixture()
async def session(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as s:
        yield s


def _user(**kw: Any) -> User:
    return User(id=str(uuid.uuid4()), email=f"{uuid.uuid4()}@x.io", **kw)


class World:
    """Two organizations; author, runner and colleague in one, stranger in the other."""

    def __init__(self) -> None:
        self.org = str(uuid.uuid4())
        self.other_org = str(uuid.uuid4())
        self.author = _user()
        self.runner = _user()
        self.colleague = _user()
        self.stranger = _user()
        self.admin = _user(is_admin=True)
        self.loner = _user()  # in no organization
        self.inactive = _user(is_active=False)


@pytest.fixture()
async def world(session: AsyncSession) -> World:
    w = World()
    for user in (w.author, w.runner, w.colleague, w.stranger, w.admin, w.loner, w.inactive):
        session.add(user)
    session.add(Organization(id=w.org, name="Org", slug=f"o-{w.org[:6]}", owner_id=w.author.id))
    session.add(
        Organization(id=w.other_org, name="Other", slug=f"x-{w.org[:6]}", owner_id=w.stranger.id)
    )
    await session.flush()
    for uid in (w.author.id, w.runner.id, w.colleague.id, w.inactive.id):
        session.add(OrgMembership(id=str(uuid.uuid4()), org_id=w.org, user_id=uid, role="member"))
    session.add(
        OrgMembership(id=str(uuid.uuid4()), org_id=w.other_org, user_id=w.stranger.id, role="owner")
    )
    await session.commit()
    return w


async def _make_run(session: AsyncSession, w: World, *, runner: User | None = None) -> WorkflowRun:
    wf = Workflow(
        user_id=w.author.id, org_id=w.org, name="wf", blueprint={"nodes": [], "edges": []}
    )
    session.add(wf)
    await session.flush()
    run = WorkflowRun(
        workflow_id=wf.id,
        user_id=(runner or w.runner).id,
        blueprint_snapshot={"nodes": [], "edges": []},
        status="running",
    )
    session.add(run)
    await session.commit()
    return run


async def _make_approval(
    factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    w: World,
    *,
    assignee: str | None = None,
    runner: User | None = None,
    org_id: str | None = "org",
) -> ConfirmationRequest:
    """An approval the way the Human Intervention node creates one."""
    run = await _make_run(session, w, runner=runner)
    initiator = await resolve_principal(session, user_id=run.user_id, auth=AuthMethod.SESSION)
    row: ConfirmationRequest = await ConfirmationService(factory).request(
        ConfirmationSpec(
            kind="workflow_approval",
            initiator=initiator,
            binding=Binding(
                kind="workflow", id=run.workflow_id, owner_id=w.author.id, org_id=w.org
            ),
            org_id=w.org if org_id == "org" else org_id,
            title="Approve payment",
            description=None,
            payload={"timeout_hours": 24.0, "assignee": assignee},
            eligible_scope="assignee" if assignee else "initiator",
            eligible_user_id=assignee or run.user_id,
            delivery="inline_only",
            channel_id=None,
            timeout_seconds=24 * 3600,
            workflow_run_id=run.id,
            node_id="human_1",
        )
    )
    return row


def _ids(resp: Any) -> set[str]:
    return {a["id"] for a in resp.data}


async def _status(factory: async_sessionmaker[AsyncSession], row_id: str) -> str:
    async with factory() as s:
        stored = await s.get(ConfirmationRequest, row_id)
        assert stored is not None
        return str(stored.status)


class TestUnassignedApproval:
    async def test_listed_only_to_run_user(
        self, factory: async_sessionmaker[AsyncSession], session: AsyncSession, world: World
    ) -> None:
        approval = await _make_approval(factory, session, world)

        assert approval.id in _ids(
            await list_pending_approvals(current_user=world.runner, db=session)
        )
        for other in (world.author, world.colleague, world.stranger, world.admin):
            assert approval.id not in _ids(
                await list_pending_approvals(current_user=other, db=session)
            )

    @pytest.mark.parametrize("who", ["author", "colleague", "stranger"])
    async def test_nobody_else_can_decide(
        self,
        factory: async_sessionmaker[AsyncSession],
        session: AsyncSession,
        world: World,
        who: str,
    ) -> None:
        """Including the workflow's author: someone else's run is not theirs to approve."""
        approval = await _make_approval(factory, session, world)
        user = getattr(world, who)

        for endpoint in (approve_approval, reject_approval):
            with pytest.raises(AppError) as exc:
                await endpoint(
                    approval.id, ApprovalDecisionRequest(), current_user=user, db=session
                )
            assert exc.value.error_code == "confirmation_forbidden"
        assert await _status(factory, approval.id) == "pending"

    async def test_run_user_can_approve(
        self, factory: async_sessionmaker[AsyncSession], session: AsyncSession, world: World
    ) -> None:
        approval = await _make_approval(factory, session, world)

        resp = await approve_approval(
            approval.id,
            ApprovalDecisionRequest(note="fine"),
            current_user=world.runner,
            db=session,
        )
        assert resp.data["status"] == "approved"
        assert resp.data["decision_by"] == world.runner.id
        assert resp.data["decision_note"] == "fine"
        assert resp.data["workflow_run_id"] == approval.workflow_run_id
        assert resp.data["node_id"] == "human_1"
        assert resp.data["assignee"] is None
        assert resp.data["timeout_hours"] == 24.0

    async def test_a_platform_administrator_can_decide(
        self, factory: async_sessionmaker[AsyncSession], session: AsyncSession, world: World
    ) -> None:
        approval = await _make_approval(factory, session, world)
        resp = await reject_approval(
            approval.id, ApprovalDecisionRequest(), current_user=world.admin, db=session
        )
        assert resp.data["status"] == "rejected"
        assert resp.data["decision_by"] == world.admin.id

    async def test_second_decision_is_refused(
        self, factory: async_sessionmaker[AsyncSession], session: AsyncSession, world: World
    ) -> None:
        approval = await _make_approval(factory, session, world)
        await approve_approval(
            approval.id, ApprovalDecisionRequest(), current_user=world.runner, db=session
        )
        with pytest.raises(AppError) as exc:
            await reject_approval(
                approval.id, ApprovalDecisionRequest(), current_user=world.runner, db=session
            )
        assert exc.value.error_code == "confirmation_already_decided"


class TestAssignedApproval:
    async def test_assignee_sees_and_decides(
        self, factory: async_sessionmaker[AsyncSession], session: AsyncSession, world: World
    ) -> None:
        approval = await _make_approval(factory, session, world, assignee=world.colleague.id)

        assert approval.id in _ids(
            await list_pending_approvals(current_user=world.colleague, db=session)
        )
        resp = await reject_approval(
            approval.id, ApprovalDecisionRequest(), current_user=world.colleague, db=session
        )
        assert resp.data["status"] == "rejected"
        assert resp.data["assignee"] == world.colleague.id

    async def test_run_user_cannot_decide_assigned(
        self, factory: async_sessionmaker[AsyncSession], session: AsyncSession, world: World
    ) -> None:
        approval = await _make_approval(factory, session, world, assignee=world.colleague.id)

        assert approval.id not in _ids(
            await list_pending_approvals(current_user=world.runner, db=session)
        )
        with pytest.raises(AppError) as exc:
            await approve_approval(
                approval.id, ApprovalDecisionRequest(), current_user=world.runner, db=session
            )
        assert exc.value.error_code == "confirmation_forbidden"

    async def test_a_platform_administrator_can_decide_an_assigned_one(
        self, factory: async_sessionmaker[AsyncSession], session: AsyncSession, world: World
    ) -> None:
        approval = await _make_approval(factory, session, world, assignee=world.colleague.id)
        resp = await approve_approval(
            approval.id, ApprovalDecisionRequest(), current_user=world.admin, db=session
        )
        assert resp.data["status"] == "approved"


class TestAssigneeMustBeInTheOrganization:
    """B13: the assignee is checked when the approval is created."""

    @pytest.mark.parametrize("who", ["stranger", "inactive", "loner", "nobody"])
    async def test_refused_at_creation(
        self,
        factory: async_sessionmaker[AsyncSession],
        session: AsyncSession,
        world: World,
        who: str,
    ) -> None:
        assignee = str(uuid.uuid4()) if who == "nobody" else getattr(world, who).id
        with pytest.raises(AssemblyRefusal) as exc:
            await _make_approval(factory, session, world, assignee=assignee)
        assert exc.value.error_code == "approval_assignee_not_in_org"
        async with factory() as s:
            assert (await s.execute(select(ConfirmationRequest))).scalars().all() == []

    async def test_a_member_is_accepted(
        self, factory: async_sessionmaker[AsyncSession], session: AsyncSession, world: World
    ) -> None:
        approval = await _make_approval(factory, session, world, assignee=world.colleague.id)
        assert approval.eligible_scope == "assignee"
        assert approval.eligible_user_id == world.colleague.id

    @pytest.mark.parametrize("written_as", ["username", "email", "EMAIL", " padded id "])
    async def test_an_assignee_written_as_username_or_email_names_the_user(
        self,
        factory: async_sessionmaker[AsyncSession],
        session: AsyncSession,
        world: World,
        written_as: str,
    ) -> None:
        """The form asks for an id, a username or an email; the row records the user id."""
        world.colleague.username = "colleague_1"
        await session.commit()
        text = {
            "username": "colleague_1",
            "email": world.colleague.email,
            "EMAIL": world.colleague.email.upper(),
            " padded id ": f" {world.colleague.id} ",
        }[written_as]

        approval = await _make_approval(factory, session, world, assignee=text)

        assert approval.eligible_scope == "assignee"
        assert approval.eligible_user_id == world.colleague.id
        resp = await approve_approval(
            approval.id, ApprovalDecisionRequest(), current_user=world.colleague, db=session
        )
        assert resp.data["status"] == "approved"

    async def test_a_username_outside_the_organization_is_refused(
        self, factory: async_sessionmaker[AsyncSession], session: AsyncSession, world: World
    ) -> None:
        world.stranger.username = "stranger_1"
        await session.commit()
        for text in ("stranger_1", world.stranger.email, world.inactive.email):
            with pytest.raises(AssemblyRefusal) as exc:
                await _make_approval(factory, session, world, assignee=text)
            assert exc.value.error_code == "approval_assignee_not_in_org"

    async def test_a_name_matching_two_users_is_refused(
        self, factory: async_sessionmaker[AsyncSession], session: AsyncSession, world: World
    ) -> None:
        """Email addresses are not unique in the table; a shared one names nobody."""
        world.runner.email = world.colleague.email
        await session.commit()
        with pytest.raises(AssemblyRefusal) as exc:
            await _make_approval(factory, session, world, assignee=world.colleague.email)
        assert exc.value.error_code == "approval_assignee_not_in_org"
        assert "more than one" in exc.value.detail

    async def test_personal_workflow_needs_a_shared_organization(
        self, factory: async_sessionmaker[AsyncSession], session: AsyncSession, world: World
    ) -> None:
        """No workflow organization: the assignee must share one with the runner."""
        ok = await _make_approval(factory, session, world, assignee=world.colleague.id, org_id=None)
        assert ok.status == "pending"
        with pytest.raises(AssemblyRefusal):
            await _make_approval(factory, session, world, assignee=world.stranger.id, org_id=None)

    async def test_a_runner_in_no_organization_can_assign_only_themself(
        self, factory: async_sessionmaker[AsyncSession], session: AsyncSession, world: World
    ) -> None:
        ok = await _make_approval(
            factory, session, world, assignee=world.loner.id, runner=world.loner, org_id=None
        )
        assert ok.eligible_user_id == world.loner.id
        with pytest.raises(AssemblyRefusal):
            await _make_approval(
                factory,
                session,
                world,
                assignee=world.colleague.id,
                runner=world.loner,
                org_id=None,
            )

    async def test_a_literal_assignee_is_checked_when_the_blueprint_is_saved(
        self, session: AsyncSession, world: World
    ) -> None:
        def blueprint(assignee: str) -> dict[str, Any]:
            return {
                "nodes": [
                    {
                        "id": "human_1",
                        "type": "humanIntervention",
                        "data": {
                            "type": "HUMAN_INTERVENTION",
                            "label": "Sign-off",
                            "assignee": assignee,
                        },
                    }
                ],
                "edges": [],
            }

        await ensure_assignees_valid(
            session, blueprint(world.colleague.id), user=world.author, org_id=world.org
        )
        # Written as a username or an email, as the form invites.
        world.colleague.username = "colleague_1"
        await session.commit()
        for text in ("colleague_1", world.colleague.email):
            await ensure_assignees_valid(
                session, blueprint(text), user=world.author, org_id=world.org
            )
        # A template is checked when the run fills it in, not here.
        await ensure_assignees_valid(
            session, blueprint("{{manager_id}}"), user=world.author, org_id=world.org
        )
        with pytest.raises(AppError) as exc:
            await ensure_assignees_valid(
                session, blueprint(world.stranger.id), user=world.author, org_id=world.org
            )
        assert exc.value.error_code == "approval_assignee_not_in_org"
        assert exc.value.status_code == 400
        assert exc.value.detail_args == {"step": "Sign-off", "assignee": world.stranger.id}
        assert "Sign-off" in (exc.value.detail or "")


class TestRunApprovalListing:
    async def test_other_users_run_hides_unassigned(
        self, factory: async_sessionmaker[AsyncSession], session: AsyncSession, world: World
    ) -> None:
        approval = await _make_approval(factory, session, world)
        run = await session.get(WorkflowRun, approval.workflow_run_id)
        assert run is not None

        with patch("fim_one.web.api.workflows._get_accessible_workflow", AsyncMock()):
            seen_by_author = await list_run_approvals(
                run.workflow_id, run.id, current_user=world.author, db=session
            )
            seen_by_runner = await list_run_approvals(
                run.workflow_id, run.id, current_user=world.runner, db=session
            )
        assert _ids(seen_by_author) == set()
        assert _ids(seen_by_runner) == {approval.id}

    async def test_other_users_run_shows_what_is_assigned_to_me(
        self, factory: async_sessionmaker[AsyncSession], session: AsyncSession, world: World
    ) -> None:
        approval = await _make_approval(factory, session, world, assignee=world.colleague.id)
        run = await session.get(WorkflowRun, approval.workflow_run_id)
        assert run is not None
        with patch("fim_one.web.api.workflows._get_accessible_workflow", AsyncMock()):
            seen = await list_run_approvals(
                run.workflow_id, run.id, current_user=world.colleague, db=session
            )
        assert _ids(seen) == {approval.id}


class TestPublishingChecksAssigneesAgainstTheTargetOrganization:
    """A personal workflow's assignee was checked against the saver's organizations only."""

    @staticmethod
    async def _personal_workflow(session: AsyncSession, owner: User, assignee: str) -> Workflow:
        wf = Workflow(
            user_id=owner.id,
            org_id=None,
            name="wf",
            blueprint={
                "nodes": [
                    {
                        "id": "human_1",
                        "type": "humanIntervention",
                        "data": {
                            "type": "HUMAN_INTERVENTION",
                            "label": "Sign-off",
                            "assignee": assignee,
                        },
                    }
                ],
                "edges": [],
            },
        )
        session.add(wf)
        await session.commit()
        return wf

    async def test_an_assignee_outside_the_target_organization_refuses_the_publish(
        self, session: AsyncSession, world: World
    ) -> None:
        """Saved while personal, naming someone the author shares another organization with."""
        # The stranger shares an organization with the author, so the personal
        # workflow could be saved naming them.
        third = str(uuid.uuid4())
        session.add(
            Organization(id=third, name="Third", slug=f"t-{third[:6]}", owner_id=world.author.id)
        )
        await session.flush()
        for uid in (world.author.id, world.stranger.id):
            session.add(
                OrgMembership(id=str(uuid.uuid4()), org_id=third, user_id=uid, role="member")
            )
        await session.commit()
        wf = await self._personal_workflow(session, world.author, world.stranger.id)
        await ensure_assignees_valid(session, wf.blueprint, user=world.author, org_id=None)

        with pytest.raises(AppError) as exc:
            await publish_workflow(
                wf.id,
                PublishRequest(scope="org", org_id=world.org),
                current_user=world.author,
                db=session,
            )
        assert exc.value.error_code == "approval_assignee_not_in_org"
        assert exc.value.detail_args == {"step": "Sign-off", "assignee": world.stranger.id}
        await session.rollback()
        await session.refresh(wf)
        assert wf.org_id is None and wf.visibility != "org"

    async def test_an_assignee_in_the_target_organization_is_published(
        self, session: AsyncSession, world: World
    ) -> None:
        wf = await self._personal_workflow(session, world.author, world.colleague.id)
        resp = await publish_workflow(
            wf.id,
            PublishRequest(scope="org", org_id=world.org),
            current_user=world.author,
            db=session,
        )
        assert resp.data["org_id"] == world.org


class TestDeletingARunClosesItsApprovals:
    """A request names its run without a foreign key; it must not outlive the run as pending."""

    @staticmethod
    async def _finished_run_with_approval(
        factory: async_sessionmaker[AsyncSession], session: AsyncSession, world: World
    ) -> ConfirmationRequest:
        """The author's own run, failed, with the colleague's approval still open."""
        approval = await _make_approval(
            factory, session, world, assignee=world.colleague.id, runner=world.author
        )
        run = await session.get(WorkflowRun, approval.workflow_run_id)
        assert run is not None
        run.status = "failed"
        await session.commit()
        return approval

    @staticmethod
    async def _assert_closed(
        factory: async_sessionmaker[AsyncSession],
        session: AsyncSession,
        world: World,
        approval: ConfirmationRequest,
        *,
        run_deleted_here: bool = True,
    ) -> None:
        async with factory() as s:
            if run_deleted_here:
                assert await s.get(WorkflowRun, approval.workflow_run_id) is None
            row = await s.get(ConfirmationRequest, approval.id)
        assert row is not None
        assert row.status == "expired" and row.responded_at is not None
        assert approval.id not in _ids(
            await list_pending_approvals(current_user=world.colleague, db=session)
        )

    async def test_deleting_one_run(
        self, factory: async_sessionmaker[AsyncSession], session: AsyncSession, world: World
    ) -> None:
        approval = await self._finished_run_with_approval(factory, session, world)
        run = await session.get(WorkflowRun, approval.workflow_run_id)
        assert run is not None
        await delete_workflow_run(run.workflow_id, run.id, current_user=world.author, db=session)
        await self._assert_closed(factory, session, world, approval)

    async def test_clearing_a_workflows_runs(
        self, factory: async_sessionmaker[AsyncSession], session: AsyncSession, world: World
    ) -> None:
        approval = await self._finished_run_with_approval(factory, session, world)
        other = await _make_approval(factory, session, world, assignee=world.colleague.id)
        run = await session.get(WorkflowRun, approval.workflow_run_id)
        assert run is not None
        await clear_workflow_runs(run.workflow_id, current_user=world.author, db=session)
        await self._assert_closed(factory, session, world, approval)
        # A run that was not deleted keeps its approval open.
        async with factory() as s:
            kept = await s.get(ConfirmationRequest, other.id)
        assert kept is not None and kept.status == "pending"

    async def test_the_retention_cleanup(
        self, factory: async_sessionmaker[AsyncSession], session: AsyncSession, world: World
    ) -> None:
        """The path the admin "cleanup runs" action and the background job share."""
        from datetime import UTC, datetime, timedelta

        from fim_one.core.workflow.run_cleanup import WorkflowRunCleaner

        by_age = await self._finished_run_with_approval(factory, session, world)
        by_count = await self._finished_run_with_approval(factory, session, world)
        newest = await self._finished_run_with_approval(factory, session, world)
        old = await session.get(WorkflowRun, by_age.workflow_run_id)
        middle = await session.get(WorkflowRun, by_count.workflow_run_id)
        newer = await session.get(WorkflowRun, newest.workflow_run_id)
        assert old is not None and middle is not None and newer is not None
        old.created_at = datetime.now(UTC) - timedelta(days=90)
        # Two runs of one workflow, of which one may stay.
        middle.created_at = datetime.now(UTC) - timedelta(days=2)
        newer.workflow_id = middle.workflow_id
        await session.commit()

        async with factory() as fresh:
            await WorkflowRunCleaner(max_age_days=30, max_runs_per_workflow=1).cleanup(fresh)

        await self._assert_closed(factory, session, world, by_age)
        await self._assert_closed(factory, session, world, by_count)
        async with factory() as s:
            kept = await s.get(ConfirmationRequest, newest.id)
        assert kept is not None and kept.status == "pending"

    async def test_deleting_the_workflow(
        self, factory: async_sessionmaker[AsyncSession], session: AsyncSession, world: World
    ) -> None:
        approval = await _make_approval(factory, session, world, assignee=world.colleague.id)
        run = await session.get(WorkflowRun, approval.workflow_run_id)
        assert run is not None
        await delete_workflow(run.workflow_id, current_user=world.author, db=session)
        # The runs go by the database's cascade, which this SQLite file does not enforce.
        await self._assert_closed(factory, session, world, approval, run_deleted_here=False)

    async def test_deleting_the_user_the_run_belongs_to(
        self, factory: async_sessionmaker[AsyncSession], session: AsyncSession, world: World
    ) -> None:
        from fim_one.web.services.user_deletion import purge_user_data

        approval = await _make_approval(factory, session, world, assignee=world.colleague.id)
        await purge_user_data(session, world.runner.id)
        await session.commit()
        await self._assert_closed(factory, session, world, approval)

"""Who may read a workflow's runs, versions and run statistics.

Run records carry the runner's inputs and outputs. They are shown only to the
runner, the workflow author included: an author sees subscribers' runs only
as counts, durations and success or failure (decision 2026-11-04). Version
history carries the full blueprint and is author-only, matching the live
workflow response that strips the blueprint for non-authors.

Users in these tests:

- ``author``      owns the workflow (org-shared, published to org ``O1``)
- ``member``      same org, subscribed to the workflow
- ``market_sub``  another org, subscribed through the Market
- ``colleague``   same org, not subscribed
- ``stranger``    another org, not subscribed
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.core.workflow.run_service import RunRefusedError
from fim_one.db.base import Base
from fim_one.db.models.organization import Organization, OrgMembership
from fim_one.db.models.resource_subscription import ResourceSubscription
from fim_one.db.models.user import User
from fim_one.db.models.workflow import Workflow, WorkflowRun, WorkflowVersion
from fim_one.web.api import workflows as wf_api
from fim_one.web.exceptions import AppError
from fim_one.web.platform import MARKET_ORG_ID

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    import fim_one.db.models  # noqa: F401

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture()
async def session(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with factory() as s:
        yield s


@dataclass
class World:
    author: User
    member: User
    market_sub: User
    colleague: User
    stranger: User
    wf: Workflow
    runs: dict[str, WorkflowRun]  # user key -> that user's run
    version: WorkflowVersion


async def _user(session: AsyncSession, org_id: str | None) -> User:
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4()}@x.io")
    session.add(user)
    await session.flush()
    if org_id:
        session.add(OrgMembership(org_id=org_id, user_id=user.id, role="member"))
    return user


def _subscribe(session: AsyncSession, user: User, wf: Workflow, org_id: str) -> None:
    session.add(
        ResourceSubscription(
            user_id=user.id, resource_type="workflow", resource_id=wf.id, org_id=org_id
        )
    )


def _run(wf: Workflow, user: User, status: str, duration_ms: int) -> WorkflowRun:
    return WorkflowRun(
        workflow_id=wf.id,
        user_id=user.id,
        blueprint_snapshot={"nodes": [], "edges": []},
        inputs={"secret_input": f"input-of-{user.id}"},
        outputs={"secret_output": f"output-of-{user.id}"},
        node_results={"llm_1": {"status": status, "duration_ms": duration_ms}},
        status=status,
        duration_ms=duration_ms,
    )


@pytest.fixture()
async def world(session: AsyncSession) -> World:
    owner_seed = User(id=str(uuid.uuid4()), email="seed@x.io")
    session.add(owner_seed)
    await session.flush()
    o1 = Organization(name="O1", slug=f"o1-{uuid.uuid4()}", owner_id=owner_seed.id)
    o2 = Organization(name="O2", slug=f"o2-{uuid.uuid4()}", owner_id=owner_seed.id)
    session.add_all([o1, o2])
    await session.flush()

    author = await _user(session, o1.id)
    member = await _user(session, o1.id)
    market_sub = await _user(session, o2.id)
    colleague = await _user(session, o1.id)
    stranger = await _user(session, o2.id)

    wf = Workflow(
        user_id=author.id,
        name="shared",
        blueprint={"nodes": [{"id": "llm_1", "type": "llm", "data": {}}], "edges": []},
        visibility="org",
        org_id=o1.id,
        status="active",
    )
    session.add(wf)
    await session.flush()
    _subscribe(session, member, wf, o1.id)
    _subscribe(session, market_sub, wf, MARKET_ORG_ID)

    runs = {
        "author": _run(wf, author, "completed", 100),
        "member": _run(wf, member, "failed", 200),
        "market_sub": _run(wf, market_sub, "completed", 300),
    }
    session.add_all(runs.values())
    version = WorkflowVersion(
        workflow_id=wf.id, version_number=1, blueprint=wf.blueprint, created_by=author.id
    )
    session.add(version)
    await session.commit()
    return World(author, member, market_sub, colleague, stranger, wf, runs, version)


# ---------------------------------------------------------------------------
# Endpoint wrappers
# ---------------------------------------------------------------------------


async def _list_ids(w: World, user: User, session: AsyncSession) -> set[str]:
    resp = await wf_api.list_workflow_runs(
        w.wf.id, page=1, size=100, status=None, current_user=user, db=session
    )
    return {item["id"] for item in resp.items}


async def _export(w: World, user: User, session: AsyncSession) -> dict[str, Any]:
    import json

    resp = await wf_api.export_workflow_runs(
        w.wf.id, status=None, limit=100, current_user=user, db=session
    )
    payload: dict[str, Any] = json.loads(bytes(resp.body))
    return payload


async def _stats(w: World, user: User, session: AsyncSession) -> dict[str, Any]:
    resp = await wf_api.get_workflow_stats(w.wf.id, current_user=user, db=session)
    data: dict[str, Any] = resp.data
    return data


async def _analytics(w: World, user: User, session: AsyncSession) -> dict[str, Any]:
    resp = await wf_api.get_workflow_analytics(
        w.wf.id, days=30, current_user=user, db=session
    )
    data: dict[str, Any] = resp.data
    return data


async def _node_stats(w: World, user: User, session: AsyncSession) -> dict[str, Any]:
    resp = await wf_api.get_workflow_node_stats(
        w.wf.id, limit=100, current_user=user, db=session
    )
    data: dict[str, Any] = resp.data
    return data


# ---------------------------------------------------------------------------
# Run list / detail / export
# ---------------------------------------------------------------------------


class TestRunContentsOnlyForRunner:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("who", ["author", "member", "market_sub"])
    async def test_list_shows_only_own_runs(
        self, world: World, session: AsyncSession, who: str
    ) -> None:
        user = getattr(world, who)
        assert await _list_ids(world, user, session) == {world.runs[who].id}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("who", ["author", "member", "market_sub"])
    async def test_export_contains_only_own_runs(
        self, world: World, session: AsyncSession, who: str
    ) -> None:
        user = getattr(world, who)
        payload = await _export(world, user, session)
        assert [r["id"] for r in payload["runs"]] == [world.runs[who].id]
        assert all(
            r["inputs"]["secret_input"] == f"input-of-{user.id}" for r in payload["runs"]
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("who", "other"),
        [
            ("author", "member"),
            ("author", "market_sub"),
            ("member", "author"),
            ("member", "market_sub"),
            ("market_sub", "author"),
            ("market_sub", "member"),
        ],
    )
    async def test_detail_of_someone_elses_run_is_404(
        self, world: World, session: AsyncSession, who: str, other: str
    ) -> None:
        with pytest.raises(AppError) as exc:
            await wf_api.get_workflow_run(
                world.wf.id,
                world.runs[other].id,
                current_user=getattr(world, who),
                db=session,
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.parametrize("who", ["author", "member", "market_sub"])
    async def test_detail_of_own_run(
        self, world: World, session: AsyncSession, who: str
    ) -> None:
        resp = await wf_api.get_workflow_run(
            world.wf.id, world.runs[who].id, current_user=getattr(world, who), db=session
        )
        assert resp.data["id"] == world.runs[who].id


class TestUnsubscribedUsersSeeNothing:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("who", ["colleague", "stranger"])
    @pytest.mark.parametrize("endpoint", ["list", "export", "stats", "analytics", "detail"])
    async def test_404(
        self, world: World, session: AsyncSession, who: str, endpoint: str
    ) -> None:
        user = getattr(world, who)
        with pytest.raises(AppError) as exc:
            if endpoint == "list":
                await _list_ids(world, user, session)
            elif endpoint == "export":
                await _export(world, user, session)
            elif endpoint == "stats":
                await _stats(world, user, session)
            elif endpoint == "analytics":
                await _analytics(world, user, session)
            else:
                await wf_api.get_workflow_run(
                    world.wf.id, world.runs["author"].id, current_user=user, db=session
                )
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------


class TestAggregates:
    @pytest.mark.asyncio
    async def test_author_stats_count_every_run(
        self, world: World, session: AsyncSession
    ) -> None:
        stats = await _stats(world, world.author, session)
        assert stats["total_runs"] == 3
        assert stats["completed"] == 2
        assert stats["failed"] == 1
        assert stats["avg_duration_ms"] == 200  # (100 + 300) / 2

    @pytest.mark.asyncio
    async def test_author_analytics_count_every_run_without_contents(
        self, world: World, session: AsyncSession
    ) -> None:
        data = await _analytics(world, world.author, session)
        assert data["total_runs"] == 3
        flat = repr(data)
        assert "secret_input" not in flat and "secret_output" not in flat

    @pytest.mark.asyncio
    async def test_author_node_stats_aggregate_every_run(
        self, world: World, session: AsyncSession
    ) -> None:
        data = await _node_stats(world, world.author, session)
        assert data["runs_analyzed"] == 3

    @pytest.mark.asyncio
    @pytest.mark.parametrize("who", ["member", "market_sub"])
    async def test_subscriber_aggregates_cover_only_own_runs(
        self, world: World, session: AsyncSession, who: str
    ) -> None:
        user = getattr(world, who)
        assert (await _stats(world, user, session))["total_runs"] == 1
        assert (await _analytics(world, user, session))["total_runs"] == 1
        assert (await _node_stats(world, user, session))["runs_analyzed"] == 1


# ---------------------------------------------------------------------------
# Run deletion and cancellation
# ---------------------------------------------------------------------------


class TestRunMutations:
    @pytest.mark.asyncio
    async def test_author_cannot_delete_subscribers_run(
        self, world: World, session: AsyncSession
    ) -> None:
        with pytest.raises(AppError) as exc:
            await wf_api.delete_workflow_run(
                world.wf.id, world.runs["member"].id, current_user=world.author, db=session
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_clear_runs_keeps_subscribers_runs(
        self, world: World, session: AsyncSession
    ) -> None:
        resp = await wf_api.clear_workflow_runs(
            world.wf.id, current_user=world.author, db=session
        )
        assert resp.data["deleted_count"] == 1
        remaining = (
            await session.execute(
                select(WorkflowRun.user_id).where(WorkflowRun.workflow_id == world.wf.id)
            )
        ).scalars().all()
        assert set(remaining) == {world.member.id, world.market_sub.id}

    @pytest.mark.asyncio
    async def test_runner_can_cancel_own_run(
        self, world: World, session: AsyncSession
    ) -> None:
        run = world.runs["member"]
        run.status = "running"
        await session.commit()

        @asynccontextmanager
        async def _cs() -> AsyncIterator[AsyncSession]:
            async with AsyncSession(session.bind, expire_on_commit=False) as s:
                yield s

        # Not executing in this process: cancelled on its record.
        with patch("fim_one.db.create_session", _cs):
            resp = await wf_api.cancel_workflow_run(
                world.wf.id, run.id, current_user=world.member, db=session
            )
        assert resp.data["cancelled"] is True
        await session.refresh(run)
        assert run.status == "cancelled"

    @pytest.mark.asyncio
    async def test_subscriber_cannot_cancel_others_run(
        self, world: World, session: AsyncSession
    ) -> None:
        with pytest.raises(AppError) as exc:
            await wf_api.cancel_workflow_run(
                world.wf.id,
                world.runs["market_sub"].id,
                current_user=world.member,
                db=session,
            )
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Saving where the canvas is panned to is not an edit
# ---------------------------------------------------------------------------


class TestSavingTheViewIsNotAnEdit:
    @staticmethod
    def _blueprint(x: float, prompt: str = "hi") -> dict[str, Any]:
        return {
            "schema_version": 2,
            "nodes": [
                {"id": "start_1", "type": "start", "position": {"x": 0, "y": 0}, "data": {}},
                {
                    "id": "llm_1",
                    "type": "llm",
                    "position": {"x": 0, "y": 0},
                    "data": {"prompt_template": prompt},
                },
                {"id": "end_1", "type": "end", "position": {"x": 0, "y": 0}, "data": {}},
            ],
            "edges": [
                {"id": "e1", "source": "start_1", "target": "llm_1"},
                {"id": "e2", "source": "llm_1", "target": "end_1"},
            ],
            "viewport": {"x": x, "y": 0, "zoom": 1},
        }

    async def _save(self, world: World, session: AsyncSession, blueprint: dict[str, Any]) -> Any:
        from fim_one.web.schemas.workflow import WorkflowUpdate

        with patch(
            "fim_one.web.publish_review.get_org_requires_review", new=AsyncMock(return_value=True)
        ):
            return await wf_api.update_workflow(
                world.wf.id,
                WorkflowUpdate(blueprint=blueprint),
                current_user=world.author,
                db=session,
            )

    async def _versions(self, world: World, session: AsyncSession) -> int:
        from sqlalchemy import func

        from fim_one.db.models.workflow import WorkflowVersion

        return int(
            (
                await session.execute(
                    select(func.count()).where(WorkflowVersion.workflow_id == world.wf.id)
                )
            ).scalar_one()
        )

    @pytest.mark.asyncio
    async def test_a_pan_makes_no_version_and_keeps_the_workflow_published(
        self, world: World, session: AsyncSession
    ) -> None:
        await self._save(world, session, self._blueprint(0))
        world.wf.publish_status = "approved"
        await session.commit()
        versions = await self._versions(world, session)
        summary = world.wf.change_summary

        saved = await self._save(world, session, self._blueprint(250))

        assert saved.data["blueprint"]["viewport"]["x"] == 250  # the view is stored
        assert await self._versions(world, session) == versions
        assert saved.data["publish_status"] == "approved"
        assert "publish_status_reverted" not in saved.data
        assert saved.data["change_summary"] == summary

    @pytest.mark.asyncio
    async def test_an_edit_still_makes_a_version_and_goes_back_to_review(
        self, world: World, session: AsyncSession
    ) -> None:
        await self._save(world, session, self._blueprint(0))
        world.wf.publish_status = "approved"
        await session.commit()
        versions = await self._versions(world, session)

        saved = await self._save(world, session, self._blueprint(0, prompt="hello"))

        assert await self._versions(world, session) == versions + 1
        assert saved.data["publish_status"] == "pending_review"


# ---------------------------------------------------------------------------
# Version history (W3)
# ---------------------------------------------------------------------------


class TestVersionHistoryAuthorOnly:
    @pytest.mark.asyncio
    async def test_author_reads_versions(self, world: World, session: AsyncSession) -> None:
        listing = await wf_api.list_workflow_versions(
            world.wf.id, page=1, size=20, current_user=world.author, db=session
        )
        assert listing.total == 1
        one = await wf_api.get_workflow_version(
            world.wf.id, world.version.id, current_user=world.author, db=session
        )
        assert one.data["blueprint"]["nodes"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("who", ["member", "market_sub"])
    async def test_subscriber_gets_403(
        self, world: World, session: AsyncSession, who: str
    ) -> None:
        user = getattr(world, who)
        with pytest.raises(AppError) as exc:
            await wf_api.list_workflow_versions(
                world.wf.id, page=1, size=20, current_user=user, db=session
            )
        assert exc.value.status_code == 403
        with pytest.raises(AppError) as exc:
            await wf_api.get_workflow_version(
                world.wf.id, world.version.id, current_user=user, db=session
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize("who", ["colleague", "stranger"])
    async def test_unsubscribed_gets_404(
        self, world: World, session: AsyncSession, who: str
    ) -> None:
        with pytest.raises(AppError) as exc:
            await wf_api.get_workflow_version(
                world.wf.id, world.version.id, current_user=getattr(world, who), db=session
            )
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Author env vars (W4)
# ---------------------------------------------------------------------------


class TestAuthorEnvNotInjectedForOthers:
    def test_env_only_for_author(self) -> None:
        from fim_one.core.security.encryption import encrypt_credential
        from fim_one.core.assembly.principal import CallContext, provisional_principal
        from fim_one.core.workflow.run_service import author_env_vars, workflow_binding

        wf = Workflow(id="wf", user_id="author", name="x", blueprint={})
        wf.env_vars_blob = encrypt_credential({"API_TOKEN": "s3cret"})
        as_author, as_subscriber = (
            CallContext(
                principal=provisional_principal(user),
                binding=workflow_binding(wf),
                entry="workflow_node",
            )
            for user in ("author", "subscriber")
        )
        assert author_env_vars(wf, as_author) == {"API_TOKEN": "s3cret"}
        assert author_env_vars(wf, as_subscriber) == {}


# ---------------------------------------------------------------------------
# Single-node test: quota and usage (W9)
# ---------------------------------------------------------------------------


class _TokenSpendingExecutor:
    """Stands in for an LLM node: records 42 tokens and completes."""

    async def execute(self, node: Any, store: Any, context: Any) -> Any:
        from fim_one.core.workflow.types import NodeResult, NodeStatus

        await context.usage_tracker.record(
            {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42}
        )
        return NodeResult(node_id=node.id, status=NodeStatus.COMPLETED, output="ok")


class TestTestNodeQuotaAndUsage:
    @pytest.mark.asyncio
    async def test_over_quota_is_refused(
        self, world: World, session: AsyncSession
    ) -> None:
        from fim_one.web.schemas.workflow import NodeTestRequest

        with (
            patch(
                "fim_one.core.assembly.quota.AssemblyRunGate.check",
                new=AsyncMock(side_effect=RunRefusedError("token_quota_exceeded", 429)),
            ),
            pytest.raises(AppError) as exc,
        ):
            await wf_api.test_node(
                world.wf.id,
                NodeTestRequest(node_id="llm_1"),
                current_user=world.author,
                db=session,
            )
        assert exc.value.error_code == "token_quota_exceeded"

    @pytest.mark.asyncio
    async def test_usage_recorded_and_hidden_from_history(
        self,
        world: World,
        session: AsyncSession,
        factory: async_sessionmaker[AsyncSession],
    ) -> None:
        from fim_one.web.schemas.workflow import NodeTestRequest

        @asynccontextmanager
        async def _cs() -> AsyncIterator[AsyncSession]:
            async with factory() as s:
                yield s

        with (
            patch("fim_one.core.assembly.quota.AssemblyRunGate.check", new=AsyncMock()),
            patch(
                "fim_one.core.workflow.nodes.get_executor",
                return_value=_TokenSpendingExecutor(),
            ),
            patch("fim_one.db.create_session", _cs),
        ):
            resp = await wf_api.test_node(
                world.wf.id,
                NodeTestRequest(node_id="llm_1"),
                current_user=world.author,
                db=session,
            )
        assert resp.data["status"] == "completed"

        rows = (
            await session.execute(
                select(WorkflowRun).where(WorkflowRun.mode == wf_api.RUN_MODE_TEST_NODE)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].user_id == world.author.id
        assert rows[0].total_tokens == 42

        # The usage row counts toward quota but stays out of run history.
        assert await _list_ids(world, world.author, session) == {world.runs["author"].id}
        assert (await _stats(world, world.author, session))["total_runs"] == 3

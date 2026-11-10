"""Fixes from the review of the T10 branch.

1. A non-author's run reaches the author's webhook without run id, outputs
   or error.
2. The variables map and saved-blueprint validation are author only; a
   non-author's dry run reports only whether the workflow is valid.
3. (migration downgrade: see tests/test_workflow_blueprint_v2.py)
4. Single-node test rows (``mode="test_node"``) stay out of the personal
   dashboard and the admin metrics, as they stay out of run history.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.core.assembly.principal import Binding, CallContext, provisional_principal
from fim_one.core.workflow import run_service as rs
from fim_one.db.base import Base
from fim_one.db.models.organization import Organization, OrgMembership
from fim_one.db.models.resource_subscription import ResourceSubscription
from fim_one.db.models.user import User
from fim_one.db.models.workflow import Workflow, WorkflowRun
from fim_one.web.api import workflows as wf_api
from fim_one.web.exceptions import AppError
from fim_one.web.schemas.workflow import WorkflowRunRequest

BLUEPRINT = {
    "schema_version": 2,
    "nodes": [
        {"id": "start_1", "type": "start", "data": {}},
        {"id": "secret_step", "type": "llm", "data": {"prompt_template": "hi"}},
        {"id": "end_1", "type": "end", "data": {}},
    ],
    "edges": [
        {"id": "e1", "source": "start_1", "target": "secret_step"},
        {"id": "e2", "source": "secret_step", "target": "end_1"},
    ],
    "viewport": {"x": 0, "y": 0, "zoom": 1},
}


@pytest.fixture()
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
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


async def _shared_workflow(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[User, User, Workflow]:
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
            name="shared",
            blueprint=BLUEPRINT,
            visibility="org",
            org_id=org.id,
            status="active",
            webhook_url="https://hooks.example.com/author",
        )
        s.add(wf)
        await s.flush()
        s.add(
            ResourceSubscription(
                user_id=subscriber.id, resource_type="workflow", resource_id=wf.id, org_id=org.id
            )
        )
        await s.commit()
        return author, subscriber, wf


# ---------------------------------------------------------------------------
# 1. Webhook payload
# ---------------------------------------------------------------------------


class _Engine:
    def __init__(self, **_: Any) -> None:
        pass

    async def execute_streaming(self, *_: Any, **__: Any) -> AsyncIterator[tuple[str, Any]]:
        yield "run_completed", {
            "status": "completed",
            "outputs": {"answer": "private to the runner"},
            "total_tokens": 1,
        }


@pytest.fixture()
def sent_webhooks() -> Iterator[list[dict[str, Any]]]:
    payloads: list[dict[str, Any]] = []

    async def _deliver(_url: str, payload: dict[str, Any]) -> None:
        payloads.append(payload)

    with patch.object(rs, "deliver_webhook", _deliver):
        yield payloads


def _service() -> rs.WorkflowRunService:
    gate = MagicMock()
    gate.check = AsyncMock()
    return rs.WorkflowRunService(gate=gate, engine_factory=_Engine)


class TestWebhookPayload:
    @pytest.mark.asyncio
    async def test_non_author_run_is_redacted(
        self,
        factory: async_sessionmaker[AsyncSession],
        sent_webhooks: list[dict[str, Any]],
    ) -> None:
        _, subscriber, wf = await _shared_workflow(factory)
        started = await _service().start_run(
            wf, caller_id=subscriber.id, inputs={"q": "x"}, mode=rs.RunMode.INTERACTIVE
        )
        await started.wait()
        for _ in range(20):
            if sent_webhooks:
                break
            await asyncio.sleep(0.01)
        (payload,) = sent_webhooks
        assert set(payload) == {"event", "workflow_id", "status", "duration_ms"}
        assert payload["status"] == "completed"

    @pytest.mark.asyncio
    async def test_author_run_is_complete(
        self,
        factory: async_sessionmaker[AsyncSession],
        sent_webhooks: list[dict[str, Any]],
    ) -> None:
        author, _, wf = await _shared_workflow(factory)
        started = await _service().start_run(
            wf, caller_id=author.id, inputs={}, mode=rs.RunMode.INTERACTIVE
        )
        outcome = await started.wait()
        for _ in range(20):
            if sent_webhooks:
                break
            await asyncio.sleep(0.01)
        (payload,) = sent_webhooks
        assert payload["run_id"] == outcome.run_id
        assert payload["outputs"] == {"answer": "private to the runner"}

    @pytest.mark.asyncio
    async def test_schedule_payload_marks_trigger(
        self, sent_webhooks: list[dict[str, Any]]
    ) -> None:
        wf = MagicMock(id="w", webhook_url="https://hooks.example.com/x")
        principal = CallContext(
            principal=provisional_principal("a"),
            binding=Binding(kind="workflow", id="w", owner_id="a"),
            entry="schedule",
        )
        out = rs.RunOutcome(run_id="r", status="failed", error="boom")
        spawned: list[Any] = []
        with patch("fim_one.core.utils.spawn_background", spawned.append):
            rs.WorkflowRunService()._fire_webhook(wf, out, rs.RunMode.SCHEDULE, principal)
        await spawned[0]
        (payload,) = sent_webhooks
        assert payload["trigger"] == "scheduled"
        assert payload["error"] == "boom"


# ---------------------------------------------------------------------------
# 2. Blueprint skeleton
# ---------------------------------------------------------------------------


class TestBlueprintSkeletonAuthorOnly:
    @pytest.mark.asyncio
    async def test_variables_map(self, factory: async_sessionmaker[AsyncSession]) -> None:
        author, subscriber, wf = await _shared_workflow(factory)
        async with factory() as db:
            own = await wf_api.get_workflow_variables(wf.id, current_user=author, db=db)
            assert "secret_step" in own.data
            with pytest.raises(AppError) as exc:
                await wf_api.get_workflow_variables(wf.id, current_user=subscriber, db=db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_validate_saved(self, factory: async_sessionmaker[AsyncSession]) -> None:
        author, subscriber, wf = await _shared_workflow(factory)
        async with factory() as db:
            own = await wf_api.validate_workflow(wf.id, current_user=author, db=db)
            assert "secret_step" in own.data["topology_order"]
            with pytest.raises(AppError) as exc:
                await wf_api.validate_workflow(wf.id, current_user=subscriber, db=db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_dry_run(self, factory: async_sessionmaker[AsyncSession]) -> None:
        author, subscriber, wf = await _shared_workflow(factory)
        request = MagicMock()
        async with factory() as db:
            own = await wf_api.run_workflow(
                wf.id, WorkflowRunRequest(inputs={}, dry_run=True), request,
                current_user=author, db=db,
            )
            theirs = await wf_api.run_workflow(
                wf.id, WorkflowRunRequest(inputs={}, dry_run=True), request,
                current_user=subscriber, db=db,
            )
        own_data: dict[str, Any] = own.data  # type: ignore[attr-defined]
        their_data: dict[str, Any] = theirs.data  # type: ignore[attr-defined]
        assert [p["node_id"] for p in own_data["execution_plan"]][1] == "secret_step"
        assert their_data["valid"] is True
        assert their_data["execution_plan"] == []
        assert their_data["topology_order"] == []
        assert their_data["warnings"] == []
        assert "secret_step" not in repr(their_data)

    @pytest.mark.asyncio
    async def test_dry_run_errors_are_generic_for_others(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        _, subscriber, wf = await _shared_workflow(factory)
        async with factory() as db:
            row = await db.get(Workflow, wf.id)
            assert row is not None
            row.blueprint = {**BLUEPRINT, "edges": [{"id": "x", "source": "secret_step",
                                                    "target": "missing_node"}]}
            await db.commit()
            resp = await wf_api.run_workflow(
                wf.id, WorkflowRunRequest(inputs={}, dry_run=True), MagicMock(),
                current_user=subscriber, db=db,
            )
        data: dict[str, Any] = resp.data  # type: ignore[attr-defined]
        assert data["valid"] is False
        assert data["errors"] == ["The workflow blueprint is invalid"]


# ---------------------------------------------------------------------------
# 4. Dashboard and metrics
# ---------------------------------------------------------------------------


async def _runs_with_test_node(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[User, Workflow, WorkflowRun]:
    author, _, wf = await _shared_workflow(factory)
    now = datetime.now(UTC)
    async with factory() as s:
        real = WorkflowRun(
            workflow_id=wf.id, user_id=author.id, blueprint_snapshot={}, status="completed",
            duration_ms=100, created_at=now,
        )
        test_row = WorkflowRun(
            workflow_id=wf.id, user_id=author.id, blueprint_snapshot={}, status="failed",
            mode="test_node", duration_ms=5, total_tokens=40, created_at=now,
        )
        s.add_all([real, test_row])
        await s.commit()
        return author, wf, real


class TestTestNodeRowsHidden:
    @pytest.mark.asyncio
    async def test_dashboard(self, factory: async_sessionmaker[AsyncSession]) -> None:
        from fim_one.web.api.dashboard import get_dashboard_stats

        author, _, real = await _runs_with_test_node(factory)
        async with factory() as db:
            stats = await get_dashboard_stats(current_user=author, db=db)
        assert stats.workflow_runs_today == 1
        assert stats.workflow_success_rate == 100.0
        assert [r.id for r in stats.recent_workflow_runs] == [real.id]

    @pytest.mark.asyncio
    async def test_admin_metrics(self, factory: async_sessionmaker[AsyncSession]) -> None:
        from fim_one.web.api.metrics import get_metrics_overview, get_workflow_performance

        author, _wf, _ = await _runs_with_test_node(factory)
        async with factory() as db:
            overview = await get_metrics_overview(period="7d", _admin=author, db=db)
            perf = await get_workflow_performance(period="7d", _admin=author, db=db)
        runs = overview.data["workflow_runs"]
        assert runs["total"] == 1
        assert runs["success_rate"] == 1.0
        (row,) = perf.data["workflows"] if isinstance(perf.data, dict) else perf.data
        assert row["total_runs"] == 1

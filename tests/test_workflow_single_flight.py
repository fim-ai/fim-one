"""One trigger-key run at a time per workflow, also under concurrent requests.

The trigger entry point refuses while the workflow has a run in flight. The
check used to be a query followed, several awaits later, by the insert of
the new run, so requests that arrived together all passed the check and all
ran. The check and the insert are now one step
(``WorkflowRunService._insert_single_flight_run``).

Every request has a database connection of its own, as in a server: the
SQLite database is a file, and with ``FIM_TEST_PG_URL`` set the same tests
run against PostgreSQL too (in a schema of their own, dropped afterwards).

Two things make the check and the insert one step. Within one server
process a per-workflow lock does; across processes the row lock on the
workflow does, on PostgreSQL only. ``TestSeparateWorkers`` stands several
run services side by side, as separate worker processes would be, so that
only the row lock is left to serialise them.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fim_one.core.workflow import run_service as rs
from fim_one.core.workflow.rate_limiter import WorkflowRateLimiter
from fim_one.db import get_session
from fim_one.db.base import Base
from fim_one.db.models.user import User
from fim_one.db.models.workflow import Workflow, WorkflowRun
from fim_one.web.api import workflows as wf_api
from fim_one.web.exceptions import AppError
from fim_one.web.services.feature_flags import require_workflows_enabled

REQUESTS = 8
WORKERS = 6

BLUEPRINT: dict[str, Any] = {
    "schema_version": 2,
    "nodes": [
        {"id": "start_1", "type": "start", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "end_1", "type": "end", "position": {"x": 0, "y": 0}, "data": {}},
    ],
    "edges": [{"id": "e1", "source": "start_1", "target": "end_1"}],
    "viewport": {"x": 0, "y": 0, "zoom": 1},
}


class _HeldEngine:
    """Engine whose runs stay in flight until ``release`` is set."""

    started = 0
    release: ClassVar[asyncio.Event]

    def __init__(self, **_kwargs: Any) -> None:
        pass

    async def execute_streaming(
        self, _parsed: Any, _inputs: Any, context: Any = None
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        _HeldEngine.started += 1
        await _HeldEngine.release.wait()
        yield "run_completed", {"status": "completed", "outputs": {"ok": 1}, "total_tokens": 0}


def _new_service() -> rs.WorkflowRunService:
    # Limits out of the way: every refusal below is the single-flight one.
    return rs.WorkflowRunService(
        engine_factory=_HeldEngine,
        rate_limiter=WorkflowRateLimiter(max_runs_per_minute=1000, max_concurrent_runs=1000),
    )


@dataclass
class Rig:
    backend: str
    client: httpx.AsyncClient
    factory: async_sessionmaker[AsyncSession]
    service: rs.WorkflowRunService
    wf_id: str
    key: str

    async def trigger(self) -> httpx.Response:
        return await self.client.post(
            "/api/workflows/trigger",
            json={"inputs": {}},
            headers={"Authorization": f"Bearer {self.key}"},
        )

    async def runs(self) -> list[WorkflowRun]:
        async with self.factory() as s:
            return list((await s.execute(select(WorkflowRun))).scalars().all())


def _backends() -> list[Any]:
    return [
        pytest.param("sqlite", id="sqlite"),
        pytest.param(
            "postgresql",
            id="postgresql",
            marks=pytest.mark.skipif(
                not os.environ.get("FIM_TEST_PG_URL"), reason="FIM_TEST_PG_URL not set"
            ),
        ),
    ]


@pytest.fixture(params=_backends())
async def database(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[tuple[str, AsyncEngine]]:
    """An empty database with the application's tables, per test."""
    import fim_one.db.models  # noqa: F401

    backend: str = request.param
    if backend == "sqlite":
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'flight.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield backend, engine
        await engine.dispose()
        return

    url = make_url(os.environ["FIM_TEST_PG_URL"]).set(drivername="postgresql+asyncpg")
    schema = f"single_flight_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        url,
        # Room for every concurrent request to hold a connection of its own.
        pool_size=REQUESTS * 2 + WORKERS * 2,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield backend, engine
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest.fixture()
async def rig(database: tuple[str, AsyncEngine]) -> AsyncIterator[Rig]:
    backend, engine = database
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _cs() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    async def _session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    async def _enabled() -> None:
        return None

    async with factory() as s:
        owner = User(id=str(uuid.uuid4()), email="owner@x.io")
        s.add(owner)
        await s.flush()
        wf = Workflow(
            user_id=owner.id, name="hooked", blueprint=BLUEPRINT, status="active", is_active=True
        )
        s.add(wf)
        await s.commit()
        key = str(
            (await wf_api.generate_workflow_api_key(wf.id, current_user=owner, db=s)).data[
                "api_key"
            ]
        )

    app = FastAPI()
    app.include_router(wf_api.router)
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[require_workflows_enabled] = _enabled

    @app.exception_handler(AppError)
    async def _app_error(_request: Any, exc: AppError) -> Any:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "error_code": exc.error_code},
        )

    _HeldEngine.started = 0
    _HeldEngine.release = asyncio.Event()
    service = _new_service()
    with (
        patch("fim_one.db.create_session", _cs),
        patch.object(rs, "_default_service", service),
        patch("fim_one.web.api.chat._get_quota_status", new=AsyncMock(return_value=(0, 0))),
        patch(
            "fim_one.web.services.quota_enforcer.is_user_unentitled",
            new=AsyncMock(return_value=False),
        ),
    ):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            yield Rig(backend, client, factory, service, wf.id, key)


async def _until(condition: Any, timeout: float = 10.0) -> None:
    async def _poll() -> None:
        while not await condition():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), timeout=timeout)


@pytest.mark.asyncio
async def test_concurrent_triggers_start_exactly_one_run(rig: Rig) -> None:
    requests = [asyncio.create_task(rig.trigger()) for _ in range(REQUESTS)]

    async def _a_run_started() -> bool:
        return _HeldEngine.started >= 1

    await _until(_a_run_started)
    await asyncio.sleep(0.3)  # time for the other requests to start runs, if they could
    assert _HeldEngine.started == 1
    assert [r.status for r in await rig.runs()] == ["running"]

    # The losers have answered while the winner's run is still in flight.
    assert sum(t.done() for t in requests) == REQUESTS - 1

    _HeldEngine.release.set()
    responses = await asyncio.wait_for(asyncio.gather(*requests), timeout=10)

    ran = [r for r in responses if r.status_code == 200]
    refused = [r for r in responses if r.status_code != 200]
    assert len(ran) == 1
    assert ran[0].json()["data"]["status"] == "completed"
    assert len(refused) == REQUESTS - 1
    assert {r.status_code for r in refused} == {429}
    assert {r.json()["error_code"] for r in refused} == {"workflow_already_running"}

    # One run was recorded and no refused request left a slot held.
    (run,) = await rig.runs()
    assert (run.status, run.mode) == ("completed", "trigger")
    assert not any(rig.service.rate_limiter._active_runs.values())


@pytest.mark.asyncio
async def test_next_trigger_runs_once_the_first_has_finished(rig: Rig) -> None:
    _HeldEngine.release.set()
    first = await rig.trigger()
    second = await rig.trigger()
    assert (first.status_code, second.status_code) == (200, 200)
    assert len(await rig.runs()) == 2
    # The per-workflow lock is dropped once nobody is using it.
    assert len(rig.service._single_flight_locks) == 0


@pytest.mark.asyncio
async def test_trigger_is_refused_while_any_run_of_the_workflow_is_in_flight(rig: Rig) -> None:
    """Unchanged rule: a run started another way also blocks the trigger."""
    async with rig.factory() as s:
        wf = await s.get(Workflow, rig.wf_id)
        assert wf is not None
    by_hand = await rig.service.start_run(
        wf, caller_id=wf.user_id, inputs={}, mode=rs.RunMode.INTERACTIVE
    )
    refused = await rig.trigger()
    assert refused.status_code == 429
    assert refused.json()["error_code"] == "workflow_already_running"
    _HeldEngine.release.set()
    await by_hand.wait()
    assert (await rig.trigger()).status_code == 200


class TestSeparateWorkers:
    """Server processes do not share the in-process lock; the row lock on the
    workflow is what serialises them. Each run service here has its own
    locks and limiter, as each worker process has."""

    @pytest.mark.asyncio
    async def test_workers_racing_start_exactly_one_run(self, rig: Rig) -> None:
        if rig.backend != "postgresql":
            pytest.skip("SQLite has no row locks: one server process only")
        async with rig.factory() as s:
            wf = await s.get(Workflow, rig.wf_id)
            assert wf is not None
        workers = [_new_service() for _ in range(WORKERS)]

        async def _trigger(service: rs.WorkflowRunService) -> str:
            try:
                started = await service.start_run(
                    wf, caller_id=None, inputs={}, mode=rs.RunMode.TRIGGER, single_flight=True
                )
            except rs.RunRefusedError as exc:
                return exc.error_code
            await started.wait()
            return "ran"

        attempts = [asyncio.create_task(_trigger(w)) for w in workers]

        async def _a_run_started() -> bool:
            return _HeldEngine.started >= 1

        await _until(_a_run_started)
        await asyncio.sleep(0.3)  # time for the other workers to start runs, if they could
        assert _HeldEngine.started == 1
        assert [r.status for r in await rig.runs()] == ["running"]

        _HeldEngine.release.set()
        results = await asyncio.wait_for(asyncio.gather(*attempts), timeout=10)
        assert sorted(results) == ["ran"] + ["workflow_already_running"] * (WORKERS - 1)
        (run,) = await rig.runs()
        assert (run.status, run.mode) == ("completed", "trigger")
        for worker in workers:
            assert not any(worker.rate_limiter._active_runs.values())

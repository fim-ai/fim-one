# ruff: noqa: F811  (the harness fixtures are imported by name)
"""Workflow runs in the audit: one record per run admitted, one per refusal.

A run left a row in ``workflow_runs`` and nothing in the audit log, and the
run row goes with run retention, a deletion or the workflow. Every entry
(the Run button, a batch, a trigger key, a schedule, an agent's
``run_workflow`` tool, a single-step test) passes the run service, which
now records ``workflow.run_started`` for each run it admits and
``workflow.run_refused`` for each it refuses.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from itertools import pairwise
from typing import Any

import pytest

from fim_one.core.audit import refusals
from fim_one.db.models import User
from fim_one.db.models.workflow import Workflow, WorkflowRun
from tests.audit.harness import World, world  # noqa: F401


def _node(node_id: str, kind: str, type_name: str, **data: Any) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": kind,
        "position": {"x": 0, "y": 0},
        "data": {"type": type_name, **data},
    }


def _blueprint(*steps: dict[str, Any]) -> dict[str, Any]:
    """start -> the steps in a row -> end. No step calls a model."""
    nodes = [_node("start_1", "start", "START"), *steps, _node("end_1", "end", "END")]
    ids = [node["id"] for node in nodes]
    return {
        "schema_version": 2,
        "nodes": nodes,
        "edges": [{"id": f"{a}->{b}", "source": a, "target": b} for a, b in pairwise(ids)],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }


PLAIN = _blueprint()
WITH_A_BRANCH = _blueprint(
    _node(
        "cond_1",
        "conditionBranch",
        "CONDITION_BRANCH",
        conditions=[{"id": "c1", "expression": "True"}],
    )
)


@pytest.fixture(autouse=True)
def _fresh_throttle() -> Iterator[None]:
    refusals._throttle.reset()
    refusals._summary_pending.clear()
    yield
    refusals._throttle.reset()
    refusals._summary_pending.clear()


@pytest.fixture()
async def service(
    world: World,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Any]:
    """The run service as a server process has it, with its limits out of the way."""
    from fim_one.core.workflow import run_service as rs
    from fim_one.core.workflow.rate_limiter import WorkflowRateLimiter

    runs = rs.WorkflowRunService(
        rate_limiter=WorkflowRateLimiter(max_runs_per_minute=100_000, max_concurrent_runs=1000)
    )
    monkeypatch.setattr(rs, "_default_service", runs)
    monkeypatch.setattr("fim_one.core.assembly.sandbox._BASE", tmp_path / "run-sandboxes")
    await world.enable_module("workflows")
    yield runs
    for _ in range(200):
        if not runs.active_run_ids():
            break
        await asyncio.sleep(0.02)


async def _workflow(
    world: World,
    owner: User,
    blueprint: dict[str, Any] = PLAIN,
    **fields: Any,
) -> Workflow:
    workflow = Workflow(
        id=fields.pop("id", "wf-1"),
        user_id=owner.id,
        name=fields.pop("name", "nightly"),
        blueprint=blueprint,
        status="active",
        is_active=fields.pop("is_active", True),
        **fields,
    )
    await world.add(workflow)
    return workflow


async def _runs(world: World, mode: str | None = None) -> list[WorkflowRun]:
    from sqlalchemy import select

    async with world.factory() as db:
        query = select(WorkflowRun)
        if mode is not None:
            query = query.where(WorkflowRun.mode == mode)
        return list((await db.execute(query)).scalars().all())


class TestARunThatStarts:
    async def test_the_run_button(self, world: World, service: Any) -> None:
        alice = await world.user("alice")
        org = await world.org("acme", alice)
        await _workflow(world, alice, org_id=org.id)

        stream = await world.call("POST", "/api/workflows/wf-1/run", by=alice, json={"inputs": {}})

        assert "run_completed" in stream.text
        (run,) = await _runs(world)
        row = await world.one(
            "workflow.run_started", actor=alice, org=org, target=("workflow", "wf-1")
        )
        assert (row.workflow_run_id, row.detail, row.target_label) == (
            run.id,
            "interactive",
            "nightly",
        )
        assert (row.auth_method, row.credential_ref) == ("session", None)

    async def test_a_shared_workflow_is_recorded_as_the_person_who_ran_it(
        self,
        world: World,
        service: Any,
    ) -> None:
        author, bob = await world.user("author"), await world.user("bob")
        org = await world.org("acme", author, member=bob)
        await _workflow(world, author, org_id=org.id, visibility="org")
        from fim_one.db.models.resource_subscription import ResourceSubscription

        await world.add(
            ResourceSubscription(
                user_id=bob.id, resource_type="workflow", resource_id="wf-1", org_id=org.id
            )
        )

        await world.call("POST", "/api/workflows/wf-1/run", by=bob, json={"inputs": {}})

        await world.one("workflow.run_started", actor=bob, org=org, target=("workflow", "wf-1"))

    async def test_a_dry_run_starts_nothing_and_records_nothing(
        self,
        world: World,
        service: Any,
    ) -> None:
        alice = await world.user("alice")
        await _workflow(world, alice)

        await world.call(
            "POST", "/api/workflows/wf-1/run", by=alice, json={"inputs": {}, "dry_run": True}
        )

        assert await _runs(world) == []
        assert await world.events() == []

    async def test_a_batch_records_each_item(
        self,
        world: World,
        service: Any,
    ) -> None:
        alice = await world.user("alice")
        await _workflow(world, alice)

        await world.call(
            "POST", "/api/workflows/wf-1/batch-run", by=alice, json={"inputs": [{}, {}, {}]}
        )

        rows = await world.events("workflow.run_started")
        stored = await _runs(world, "batch")
        assert len(stored) == 3
        assert sorted(row.workflow_run_id for row in rows) == sorted(run.id for run in stored)
        assert {(row.actor_id, row.detail) for row in rows} == {(alice.id, "batch")}

    @pytest.mark.parametrize("legacy", [False, True])
    async def test_a_trigger_key_carries_its_prefix(
        self,
        world: World,
        service: Any,
        legacy: bool,
    ) -> None:
        author = await world.user("author")
        await _workflow(world, author)
        key = (await world.call("POST", "/api/workflows/wf-1/generate-api-key", by=author)).json()[
            "data"
        ]["api_key"]

        if legacy:
            answer = await world.call("POST", f"/api/workflows/trigger/{key}", json={"inputs": {}})
        else:
            answer = await world.call(
                "POST",
                "/api/workflows/trigger",
                json={"inputs": {}},
                headers={"Authorization": f"Bearer {key}"},
            )

        assert answer.json()["data"]["status"] == "completed"
        (row,) = await world.events("workflow.run_started")
        assert (row.actor_id, row.actor_kind, row.auth_method, row.credential_ref) == (
            author.id,
            "user",
            "trigger_key",
            key[:10],
        )
        assert (row.detail, row.workflow_run_id) == ("trigger", answer.json()["data"]["run_id"])
        world.assert_absent(row, key)

    async def test_a_schedule_is_the_system_acting_for_the_author(
        self,
        world: World,
        service: Any,
    ) -> None:
        from fim_one.core.workflow.scheduler import WorkflowScheduler

        author = await world.user("author")
        workflow = await _workflow(world, author, schedule_cron="* * * * *", schedule_enabled=True)

        await WorkflowScheduler()._execute_workflow(workflow)

        (run,) = await _runs(world, "schedule")
        (row,) = await world.events("workflow.run_started")
        assert (row.actor_id, row.actor_kind, row.auth_method, row.detail) == (
            author.id,
            "system",
            "schedule",
            "schedule",
        )
        assert row.workflow_run_id == run.id
        # Nobody sent a request.
        assert (row.ip_address, row.request_id) == (None, None)

    async def test_an_agents_run_workflow_tool(
        self,
        world: World,
        service: Any,
    ) -> None:
        from fim_one.core.tool.builtin.run_workflow import RunWorkflowTool

        alice = await world.user("alice")
        await _workflow(world, alice)

        answer = await RunWorkflowTool(["wf-1"], user_id=alice.id).run(name="nightly")
        assert "completed" in answer, answer

        (run,) = await _runs(world, "tool")
        (row,) = await world.events("workflow.run_started")
        assert (row.actor_id, row.detail, row.workflow_run_id) == (alice.id, "tool", run.id)

    async def test_a_single_step_test(self, world: World, service: Any) -> None:
        alice = await world.user("alice")
        await _workflow(world, alice, WITH_A_BRANCH)

        await world.call(
            "POST",
            "/api/workflows/wf-1/test-node",
            by=alice,
            json={"node_id": "cond_1", "variables": {}},
        )

        row = await world.one("workflow.run_started", actor=alice, target=("workflow", "wf-1"))
        # A test leaves no run record to point at.
        assert (row.detail, row.workflow_run_id) == ("test_node", None)
        assert await _runs(world) == []

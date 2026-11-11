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


EMPTY = {"schema_version": 2, "nodes": [], "edges": []}


async def _over_quota(world: World, user: User) -> None:
    """*user* has used more than the default quota of 50 tokens."""
    from fim_one.core.usage.ledger import UsageEntry, write_entries
    from fim_one.db.models import SystemSetting

    await world.add(SystemSetting(key="default_token_quota", value="50"))
    await write_entries(
        [
            UsageEntry(
                user_id=user.id,
                org_id=None,
                source_type="conversation",
                source_id="c-1",
                entry="chat",
                model="m",
                total_tokens=60,
            )
        ]
    )


class TestARunThatIsRefused:
    async def test_the_run_button_over_quota(self, world: World, service: Any) -> None:
        alice = await world.user("alice")
        org = await world.org("acme", alice)
        await _workflow(world, alice, org_id=org.id)
        await _over_quota(world, alice)

        answer = await world.call(
            "POST", "/api/workflows/wf-1/run", by=alice, json={"inputs": {}}, expect=429
        )

        code = answer.json()["error_code"]
        row = await world.one(
            "workflow.run_refused",
            actor=alice,
            org=org,
            target=("workflow", "wf-1"),
            outcome="denied",
        )
        assert (row.detail, row.workflow_run_id) == (code, None)
        assert await _runs(world) == []
        assert await world.events("workflow.run_started") == []

    async def test_a_retry_loop_leaves_one_record_and_a_count(
        self, world: World, service: Any
    ) -> None:
        alice = await world.user("alice")
        await _workflow(world, alice, EMPTY)

        for _ in range(5):
            await world.call(
                "POST", "/api/workflows/wf-1/run", by=alice, json={"inputs": {}}, expect=400
            )

        (row,) = await world.events("workflow.run_refused")
        assert row.detail == "blueprint_empty"
        assert await refusals.write_summary("workflow.run_refused")
        _, summary = await world.events("workflow.run_refused")
        assert (summary.actor_kind, summary.outcome) == ("system", "denied")
        assert summary.detail.startswith("4 more refusal(s)")

    async def test_no_slot_free_is_decided_as_the_run_starts(
        self, world: World, service: Any
    ) -> None:
        from fim_one.core.workflow.rate_limiter import WorkflowRateLimiter

        service.rate_limiter = WorkflowRateLimiter(max_runs_per_minute=0)
        alice = await world.user("alice")
        await _workflow(world, alice)

        await world.call(
            "POST", "/api/workflows/wf-1/run", by=alice, json={"inputs": {}}, expect=429
        )

        (row,) = await world.events("workflow.run_refused")
        assert row.detail == "workflow_rate_limited"
        assert await _runs(world) == []

    async def test_the_fault_in_a_blueprint_is_not_recorded(
        self, world: World, service: Any
    ) -> None:
        alice = await world.user("alice")
        broken = {"schema_version": 2, "nodes": [{"id": "s3cret_step", "type": "nope"}]}
        await _workflow(world, alice, broken)

        await world.call(
            "POST", "/api/workflows/wf-1/run", by=alice, json={"inputs": {}}, expect=400
        )

        (row,) = await world.events("workflow.run_refused")
        assert row.detail == "invalid_blueprint"
        world.assert_absent(row, "s3cret_step")

    async def test_a_batch_refused_whole_is_one_record(self, world: World, service: Any) -> None:
        alice = await world.user("alice")
        await _workflow(world, alice, EMPTY)

        await world.call(
            "POST",
            "/api/workflows/wf-1/batch-run",
            by=alice,
            json={"inputs": [{}, {}, {}]},
            expect=400,
        )

        row = await world.one(
            "workflow.run_refused", actor=alice, target=("workflow", "wf-1"), outcome="denied"
        )
        assert row.detail == "blueprint_empty"

    async def test_items_of_a_batch_not_started_are_one_record_per_reason(
        self, world: World, service: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WORKFLOW_HUMAN_INTERVENTION_ENABLED", "true")
        monkeypatch.setenv("WORKFLOW_MAX_WAITING_RUNS_PER_USER", "1")
        alice = await world.user("alice")
        approval = _node(
            "human_1",
            "humanIntervention",
            "HUMAN_INTERVENTION",
            title="Go on?",
            timeout_hours=1,
        )
        await _workflow(world, alice, _blueprint(approval))

        answer = await world.call(
            "POST",
            "/api/workflows/wf-1/batch-run",
            by=alice,
            json={"inputs": [{}, {}, {}, {}]},
        )

        statuses = [item["status"] for item in answer.json()["data"]["results"]]
        assert statuses.count("failed") == 3
        assert len(await world.events("workflow.run_started")) == 1
        (row,) = await world.events("workflow.run_refused")
        assert row.detail == "workflow_waiting_limit_reached: 3 of 4 items not started"

    async def test_a_trigger_key_of_a_workflow_switched_off(
        self, world: World, service: Any
    ) -> None:
        author = await world.user("author")
        await _workflow(world, author, is_active=False)
        key = (await world.call("POST", "/api/workflows/wf-1/generate-api-key", by=author)).json()[
            "data"
        ]["api_key"]

        await world.call(
            "POST",
            "/api/workflows/trigger",
            json={"inputs": {}},
            headers={"Authorization": f"Bearer {key}"},
            expect=403,
        )

        (row,) = await world.events("workflow.run_refused")
        assert (row.actor_id, row.auth_method, row.credential_ref, row.detail) == (
            author.id,
            "trigger_key",
            key[:10],
            "workflow_inactive",
        )
        world.assert_absent(row, key)

    async def test_a_trigger_key_whose_author_is_switched_off(
        self, world: World, service: Any
    ) -> None:
        from sqlalchemy import update

        author = await world.user("author")
        await _workflow(world, author)
        key = (await world.call("POST", "/api/workflows/wf-1/generate-api-key", by=author)).json()[
            "data"
        ]["api_key"]
        async with world.factory() as db:
            await db.execute(update(User).where(User.id == author.id).values(is_active=False))
            await db.commit()

        await world.call(
            "POST",
            "/api/workflows/trigger",
            json={"inputs": {}},
            headers={"Authorization": f"Bearer {key}"},
            expect=401,
        )

        (row,) = await world.events("workflow.run_refused")
        assert (row.actor_id, row.credential_ref, row.detail, row.outcome) == (
            author.id,
            key[:10],
            "workflow_owner_inactive",
            "denied",
        )
        # The key was valid: this is not an unknown key.
        assert await world.events("workflow.trigger_key_invalid") == []

    async def test_a_schedule_of_a_workflow_switched_off(self, world: World, service: Any) -> None:
        from fim_one.core.workflow.scheduler import WorkflowScheduler

        author = await world.user("author")
        workflow = await _workflow(
            world, author, is_active=False, schedule_cron="* * * * *", schedule_enabled=True
        )

        await WorkflowScheduler()._execute_workflow(workflow)

        (row,) = await world.events("workflow.run_refused")
        assert (row.actor_id, row.actor_kind, row.detail, row.ip_address) == (
            author.id,
            "system",
            "workflow_inactive",
            None,
        )

    async def test_an_agents_tool_refused_a_workflow_that_waits(
        self, world: World, service: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fim_one.core.tool.builtin.run_workflow import RunWorkflowTool

        monkeypatch.setenv("WORKFLOW_HUMAN_INTERVENTION_ENABLED", "true")
        alice = await world.user("alice")
        approval = _node("human_1", "humanIntervention", "HUMAN_INTERVENTION", title="Go on?")
        await _workflow(world, alice, _blueprint(approval))

        answer = await RunWorkflowTool(["wf-1"], user_id=alice.id).run(name="nightly")

        assert answer.startswith("[Error]")
        (row,) = await world.events("workflow.run_refused")
        assert (row.actor_id, row.detail) == (alice.id, "workflow_has_approval_step")

    async def test_a_single_step_test_over_quota(self, world: World, service: Any) -> None:
        alice = await world.user("alice")
        await _workflow(world, alice, WITH_A_BRANCH)
        await _over_quota(world, alice)

        await world.call(
            "POST",
            "/api/workflows/wf-1/test-node",
            by=alice,
            json={"node_id": "cond_1", "variables": {}},
            expect=429,
        )

        (row,) = await world.events("workflow.run_refused")
        assert row.detail == "token_quota_exceeded"
        assert await world.events("workflow.run_started") == []

    async def test_a_run_refused_when_it_would_continue(self, world: World, service: Any) -> None:
        """A trigger-key run waits for an approval; meanwhile the workflow is
        switched off. Continuing it is refused, and recorded against the run."""
        author = await world.user("author")
        await _workflow(world, author, is_active=False)
        await world.add(
            WorkflowRun(
                id="run-1",
                workflow_id="wf-1",
                user_id=author.id,
                blueprint_snapshot=PLAIN,
                inputs={},
                status="waiting",
                mode="trigger",
            )
        )

        assert await service.resume_run("run-1") is None

        (run,) = await _runs(world)
        assert run.status == "failed"
        (row,) = await world.events("workflow.run_refused")
        assert (row.workflow_run_id, row.detail, row.credential_ref) == (
            "run-1",
            "workflow_inactive",
            None,
        )
        # Not the request that made the decision.
        assert row.ip_address is None

    async def test_a_record_that_cannot_be_written_still_refuses(
        self, world: World, service: Any
    ) -> None:
        from unittest.mock import patch

        alice = await world.user("alice")
        await _workflow(world, alice, EMPTY)

        def _broken() -> Any:
            raise RuntimeError("audit database unavailable")

        with patch("fim_one.core.audit.writer._own_session", _broken):
            answer = await world.call(
                "POST", "/api/workflows/wf-1/run", by=alice, json={"inputs": {}}, expect=400
            )

        assert answer.json()["error_code"] == "blueprint_empty"


class TestARunThatIsCancelled:
    async def _waiting_run(self, world: World, owner: User, org: Any = None) -> None:
        await _workflow(world, owner, org_id=org.id if org else None)
        await world.add(
            WorkflowRun(
                id="run-1",
                workflow_id="wf-1",
                user_id=owner.id,
                blueprint_snapshot=PLAIN,
                inputs={},
                status="waiting",
                mode="interactive",
            )
        )

    async def test_a_waiting_run(self, world: World, service: Any) -> None:
        alice = await world.user("alice")
        org = await world.org("acme", alice)
        await self._waiting_run(world, alice, org)

        await world.call("POST", "/api/workflows/wf-1/runs/run-1/cancel", by=alice)

        row = await world.one(
            "workflow.run_cancelled", actor=alice, org=org, target=("workflow", "wf-1")
        )
        assert (row.workflow_run_id, row.detail) == ("run-1", "was waiting")

    async def test_the_author_cancelling_someone_elses_run(
        self, world: World, service: Any
    ) -> None:
        author, bob = await world.user("author"), await world.user("bob")
        await _workflow(world, author)
        await world.add(
            WorkflowRun(
                id="run-1",
                workflow_id="wf-1",
                user_id=bob.id,
                blueprint_snapshot=PLAIN,
                inputs={},
                status="running",
                mode="interactive",
            )
        )

        await world.call("POST", "/api/workflows/wf-1/runs/run-1/cancel", by=author)

        row = await world.one("workflow.run_cancelled", actor=author, target=("workflow", "wf-1"))
        assert (row.workflow_run_id, row.detail) == ("run-1", "was running")

    async def test_a_run_that_had_ended_is_not_cancelled_and_not_recorded(
        self, world: World, service: Any
    ) -> None:
        from sqlalchemy import update

        alice = await world.user("alice")
        await self._waiting_run(world, alice)
        async with world.factory() as db:
            await db.execute(
                update(WorkflowRun).where(WorkflowRun.id == "run-1").values(status="completed")
            )
            await db.commit()

        await world.call(
            "POST", "/api/workflows/wf-1/runs/run-1/cancel", by=alice, expect=(400, 409)
        )

        assert await world.events("workflow.run_cancelled") == []


class TestARunThePlatformCloses:
    async def test_a_run_whose_process_stopped(self, world: World, service: Any) -> None:
        from datetime import UTC, datetime, timedelta

        from fim_one.core.workflow.run_recovery import WorkflowRunSupervisor

        alice = await world.user("alice")
        org = await world.org("acme", alice)
        await _workflow(world, alice, org_id=org.id)
        long_ago = datetime.now(UTC) - timedelta(hours=1)
        await world.add(
            WorkflowRun(
                id="run-1",
                workflow_id="wf-1",
                user_id=alice.id,
                blueprint_snapshot=PLAIN,
                inputs={},
                status="running",
                mode="interactive",
                worker_id="a-process-that-died",
                started_at=long_ago,
                heartbeat_at=long_ago,
            )
        )

        report = await WorkflowRunSupervisor(service, stale_seconds=90).sweep()

        assert report.closed == ["run-1"]
        (row,) = await world.events("workflow.run_closed")
        assert (row.actor_kind, row.actor_id, row.org_id, row.outcome) == (
            "system",
            None,
            org.id,
            "failed",
        )
        assert (row.target_type, row.target_id, row.workflow_run_id) == (
            "workflow",
            "wf-1",
            "run-1",
        )
        assert row.detail == "stale_heartbeat"

    async def test_a_waiting_run_that_cannot_be_read_back(self, world: World, service: Any) -> None:
        alice = await world.user("alice")
        await _workflow(world, alice)
        await world.add(
            WorkflowRun(
                id="run-1",
                workflow_id="wf-1",
                user_id=alice.id,
                blueprint_snapshot=PLAIN,
                inputs={},
                status="waiting",
                mode="interactive",
                suspend_state={"not": "a sealed state"},
            )
        )

        assert await service.resume_run("run-1") is None

        (row,) = await world.events("workflow.run_closed")
        assert (row.workflow_run_id, row.detail) == ("run-1", "not_resumable")
        # Refused and closed are one event each, not both.
        assert await world.events("workflow.run_refused") == []

    async def test_a_refused_continuation_is_one_record(self, world: World, service: Any) -> None:
        author = await world.user("author")
        await _workflow(world, author, is_active=False)
        await world.add(
            WorkflowRun(
                id="run-1",
                workflow_id="wf-1",
                user_id=author.id,
                blueprint_snapshot=PLAIN,
                inputs={},
                status="waiting",
                mode="trigger",
            )
        )

        await service.resume_run("run-1")

        assert [row.action for row in await world.events()] == ["workflow.run_refused"]

    async def test_a_run_that_ends_with_an_approval_still_open(
        self, world: World, service: Any
    ) -> None:
        """Its open requests are closed with it; whoever was asked to decide
        no longer sees them, and the record says so."""
        from fim_one.db.models.channel import ConfirmationRequest

        alice = await world.user("alice")
        await _workflow(world, alice)
        await world.add(
            WorkflowRun(
                id="run-1",
                workflow_id="wf-1",
                user_id=alice.id,
                blueprint_snapshot=PLAIN,
                inputs={},
                status="running",
                mode="interactive",
            ),
        )
        await world.add(
            ConfirmationRequest(
                id="req-1",
                kind="workflow_approval",
                workflow_run_id="run-1",
                user_id=alice.id,
                status="pending",
                payload={},
            )
        )
        from fim_one.core.workflow.run_service import RunOutcome

        await service._persist(RunOutcome(run_id="run-1", status="failed"), started_at=None)

        (row,) = await world.events("workflow.run_closed")
        assert (row.workflow_run_id, row.detail, row.actor_kind) == (
            "run-1",
            "ended failed; 1 open approval request(s) closed",
            "system",
        )

    async def test_a_run_that_ends_with_nothing_open_records_nothing(
        self, world: World, service: Any
    ) -> None:
        alice = await world.user("alice")
        await _workflow(world, alice)

        await world.call("POST", "/api/workflows/wf-1/run", by=alice, json={"inputs": {}})

        assert await world.events("workflow.run_closed") == []

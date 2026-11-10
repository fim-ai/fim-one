"""Workflow trigger keys and schedules: every change leaves a record.

Generating and revoking a trigger key was a log line; setting and clearing
a schedule left nothing. A trigger key lets anyone who holds it start runs
billed to the author, and a schedule starts them unattended.
"""

from __future__ import annotations

import hashlib

import pytest

from fim_one.db.models.workflow import Workflow
from tests.audit.harness import World, world  # noqa: F401


@pytest.fixture()
async def nightly(world: World) -> Workflow:
    await world.enable_module("workflows")
    alice = await world.user("alice")
    workflow = Workflow(id="wf-1", user_id=alice.id, name="nightly", blueprint={"nodes": []})
    await world.add(workflow)
    workflow.owner = alice  # type: ignore[attr-defined]
    return workflow


class TestTriggerKeys:
    async def test_trigger_key_audited(self, world: World, nightly: Workflow) -> None:
        alice = nightly.owner  # type: ignore[attr-defined]

        created = (
            await world.call("POST", "/api/workflows/wf-1/generate-api-key", by=alice)
        ).json()["data"]
        key = created["api_key"]

        row = await world.one(
            "workflow.trigger_key_generate", actor=alice, target=("workflow", "wf-1")
        )
        assert (row.target_label, row.detail) == ("nightly", f"prefix={key[:10]}")
        world.assert_absent(row, key, hashlib.sha256(key.encode()).hexdigest())

    async def test_generating_over_an_existing_key_says_which_it_replaces(
        self, world: World, nightly: Workflow
    ) -> None:
        alice = nightly.owner  # type: ignore[attr-defined]

        first = (
            await world.call("POST", "/api/workflows/wf-1/generate-api-key", by=alice)
        ).json()["data"]["api_key"]
        second = (
            await world.call("POST", "/api/workflows/wf-1/generate-api-key", by=alice)
        ).json()["data"]["api_key"]

        _, rotated = await world.events("workflow.trigger_key_generate")
        assert rotated.detail == f"prefix={second[:10]}; replaces prefix={first[:10]}"
        world.assert_absent(rotated, first, second)

    async def test_revoking(self, world: World, nightly: Workflow) -> None:
        alice = nightly.owner  # type: ignore[attr-defined]
        key = (
            await world.call("POST", "/api/workflows/wf-1/generate-api-key", by=alice)
        ).json()["data"]["api_key"]

        await world.call("DELETE", "/api/workflows/wf-1/api-key", by=alice)

        row = await world.one(
            "workflow.trigger_key_revoke", actor=alice, target=("workflow", "wf-1")
        )
        assert row.detail == f"prefix={key[:10]}"
        world.assert_absent(row, key)

    async def test_someone_elses_workflow_is_not_found_and_nothing_is_recorded(
        self, world: World, nightly: Workflow
    ) -> None:
        mallory = await world.user("mallory")

        await world.call(
            "POST", "/api/workflows/wf-1/generate-api-key", by=mallory, expect=(403, 404)
        )

        assert await world.events() == []


class TestSchedules:
    async def test_setting_one(self, world: World, nightly: Workflow) -> None:
        alice = nightly.owner  # type: ignore[attr-defined]

        await world.call(
            "PUT",
            "/api/workflows/wf-1/schedule",
            by=alice,
            json={
                "cron": "0 9 * * MON-FRI",
                "enabled": True,
                "timezone": "Asia/Shanghai",
                "inputs": {"customer": "ACME-CONFIDENTIAL-INPUT"},
            },
        )

        row = await world.one(
            "workflow.schedule_update",
            actor=alice,
            target=("workflow", "wf-1"),
            changes={
                "cron": {"from": None, "to": "0 9 * * MON-FRI"},
                "enabled": {"from": False, "to": True},
                "timezone": {"from": "UTC", "to": "Asia/Shanghai"},
                # The inputs are the author's data: changed, not what.
                "inputs": {"from": None, "to": "(changed)"},
            },
        )
        world.assert_absent(row, "ACME-CONFIDENTIAL-INPUT")

    async def test_pausing_one(self, world: World, nightly: Workflow) -> None:
        alice = nightly.owner  # type: ignore[attr-defined]
        body = {"cron": "*/15 * * * *", "enabled": True, "timezone": "UTC"}
        await world.call("PUT", "/api/workflows/wf-1/schedule", by=alice, json=body)

        await world.call(
            "PUT", "/api/workflows/wf-1/schedule", by=alice, json={**body, "enabled": False}
        )

        _, paused = await world.events("workflow.schedule_update")
        assert paused.changes == {"enabled": {"from": True, "to": False}}

    async def test_clearing_one(self, world: World, nightly: Workflow) -> None:
        alice = nightly.owner  # type: ignore[attr-defined]
        await world.call(
            "PUT",
            "/api/workflows/wf-1/schedule",
            by=alice,
            json={"cron": "*/15 * * * *", "enabled": True, "timezone": "UTC"},
        )

        await world.call("DELETE", "/api/workflows/wf-1/schedule", by=alice)

        await world.one(
            "workflow.schedule_delete",
            actor=alice,
            target=("workflow", "wf-1"),
            changes={
                "cron": {"from": "*/15 * * * *", "to": None},
                "enabled": {"from": True, "to": False},
            },
        )

    async def test_a_refused_schedule_records_nothing(
        self, world: World, nightly: Workflow
    ) -> None:
        alice = nightly.owner  # type: ignore[attr-defined]

        await world.call(
            "PUT", "/api/workflows/wf-1/schedule", by=alice, json={"enabled": True}, expect=400
        )

        assert await world.events() == []

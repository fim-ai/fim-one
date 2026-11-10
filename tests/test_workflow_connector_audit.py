"""The workflow connector step: credentials, refusals and the call record.

The step resolves the runner's credential and records its call through the
assembly layer, the same way a call made from chat is. These tests run the
step against a seeded database; only the outgoing HTTP request is faked.

- Every call leaves a ``ConnectorCallLog`` row (audit P0#3c).
- An action flagged "requires confirmation" does not run unattended (P0#3b).
- A connector the runner cannot see is refused (PR-1.3).
- A runner with no credential of their own, on a connector whose owner
  switched fallback off, is refused; the request used to go out with no
  credential at all (B7).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.workflow.nodes import ConnectorExecutor
from fim_one.core.workflow.types import (
    ExecutionContext,
    NodeResult,
    NodeStatus,
    NodeType,
    WorkflowNodeDef,
)
from fim_one.core.workflow.variable_store import VariableStore
from fim_one.db.base import Base
from fim_one.db.models.connector_call_log import ConnectorCallLog
from tests.assembly import matrix as m

pytestmark = pytest.mark.timeout(60)


@pytest.fixture()
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'node.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _run(scene: m.Scene, resource: str, action: str) -> NodeResult:
    node = WorkflowNodeDef(
        id="conn_1",
        type=NodeType.CONNECTOR,
        data={
            "connector_id": scene.ids[resource],
            "action_id": scene.actions[resource][action],
            "parameters": {},
        },
    )
    ctx = ExecutionContext(run_id="run-1", user_id=scene.actor, workflow_id="wf-1")
    return await ConnectorExecutor().execute(node, VariableStore(), ctx)


async def _records(factory: async_sessionmaker[AsyncSession]) -> list[Any]:
    async with factory() as s:
        return list((await s.execute(select(ConnectorCallLog))).scalars().all())


async def test_connector_call_is_audited(factory: async_sessionmaker[AsyncSession]) -> None:
    scene = await m.seed_scene(factory, m.SceneKey("owner", True, True, False))
    with m.outside_world(factory) as out:
        result = await _run(scene, "api_bearer", "ping")

    assert result.status == NodeStatus.COMPLETED
    assert out.http[-1]["authorization"] == f"Bearer {m.OWNER_TOKEN}"
    (log,) = await _records(factory)
    assert log.connector_id == scene.ids["api_bearer"]
    assert log.action_id == scene.actions["api_bearer"]["ping"]
    assert log.user_id == scene.actor
    assert log.conversation_id is None  # no conversation in workflow context
    assert log.agent_id is None
    assert log.success is True


async def test_confirmation_required_action_fails_closed(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """A requires_confirmation action must not run unattended in a workflow."""
    scene = await m.seed_scene(factory, m.SceneKey("owner", True, True, False))
    with m.outside_world(factory) as out:
        result = await _run(scene, "api_bearer", "wipe")

    assert result.status == NodeStatus.FAILED
    assert "confirmation" in (result.error or "").lower()
    # Must short-circuit: no call, no audit log.
    assert out.http == []
    assert await _records(factory) == []


async def test_connector_not_visible_to_runner_is_blocked(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """A shared workflow cannot reach a connector by hardcoding its id."""
    scene = await m.seed_scene(factory, m.SceneKey("outsider", True, True, False))
    with m.outside_world(factory) as out:
        result = await _run(scene, "api_bearer", "ping")

    assert result.status == NodeStatus.FAILED
    assert "not found or not accessible" in (result.error or "")
    assert out.http == []


async def test_runner_without_a_credential_is_refused_when_fallback_is_off(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """B7: the request used to be sent with no credential."""
    scene = await m.seed_scene(factory, m.SceneKey("subscriber", False, True, False))
    with m.outside_world(factory) as out:
        result = await _run(scene, "api_bearer", "ping")

    assert result.status == NodeStatus.FAILED
    assert "requires your own credentials" in (result.error or "")
    assert out.http == []
    assert await _records(factory) == []


@pytest.mark.parametrize(
    ("key", "token"),
    [
        (m.SceneKey("subscriber", False, True, True), m.PERSONAL_TOKEN),
        (m.SceneKey("subscriber", True, True, False), m.OWNER_TOKEN),
    ],
    ids=["own-credential", "fallback-on"],
)
async def test_runner_calls_with_their_own_credential_or_a_borrowed_one(
    factory: async_sessionmaker[AsyncSession], key: m.SceneKey, token: str
) -> None:
    scene = await m.seed_scene(factory, key)
    with m.outside_world(factory) as out:
        result = await _run(scene, "api_bearer", "ping")

    assert result.status == NodeStatus.COMPLETED
    assert out.http[-1]["authorization"] == f"Bearer {token}"


async def test_a_connector_that_takes_no_credential_runs_for_any_runner_who_sees_it(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    scene = await m.seed_scene(factory, m.SceneKey("subscriber", False, True, False))
    with m.outside_world(factory) as out:
        result = await _run(scene, "api_none", "ping")

    assert result.status == NodeStatus.COMPLETED
    assert out.http[-1]["authorization"] is None


async def test_an_agent_steps_connector_call_is_recorded_as_a_workflow_call(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """It was recorded as a chat call, and with no run to trace it back to."""
    from fim_one.core.workflow.nodes import AgentExecutor

    scene = await m.seed_scene(factory, m.SceneKey("owner", True, True, False))
    node = WorkflowNodeDef(
        id="agent_1",
        type=NodeType.AGENT,
        data={"agent_id": scene.carrier_agent_id, "prompt_template": "go"},
    )
    ctx = ExecutionContext(run_id="run-1", user_id=scene.actor, workflow_id="wf-1")
    with m.outside_world(factory) as out:
        result = await AgentExecutor().execute(node, VariableStore(), ctx)
        assert result.status == NodeStatus.COMPLETED, result.error
        await out.agents_built[-1]["tools"].get("connector").run(
            subcommand="execute",
            connector=scene.names["api_bearer"],
            action="ping",
            parameters={},
        )

    (log,) = await _records(factory)
    assert (log.entry, log.agent_id) == ("workflow_node", scene.carrier_agent_id)
    assert log.workflow_run_id == "run-1"

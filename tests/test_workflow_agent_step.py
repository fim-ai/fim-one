"""B16 / N6 / N7: a workflow Agent step runs the agent, or fails.

The step used to resolve the agent by the runner's view and carry on when
that failed: a bare fast model, every built-in tool and no hooks. An agent
that did resolve ran without its MCP servers. The step now gets its runtime
from the agent runtime port, like a chat with the agent: the agent is judged
by the workflow author's view, an agent that is not usable fails the step,
its MCP servers are connected, and its calls are recorded under the run.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.assembly.principal import Binding, CallContext, provisional_principal
from fim_one.core.workflow import run_service as rs
from fim_one.core.workflow.nodes import AgentExecutor
from fim_one.core.workflow.types import (
    ExecutionContext,
    NodeResult,
    NodeStatus,
    NodeType,
    WorkflowNodeDef,
)
from fim_one.core.workflow.variable_store import VariableStore
from fim_one.db.base import Base
from fim_one.db.models.agent import Agent
from fim_one.db.models.connector_call_log import ConnectorCallLog
from fim_one.db.models.workflow import Workflow

from .assembly import matrix as m

pytestmark = pytest.mark.timeout(60)


@pytest.fixture()
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'agent_step.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _context(scene: m.Scene, *, runner: str | None = None, run_id: str = "run-1") -> ExecutionContext:
    """What the run service gives a run of the owner's workflow by *runner*."""
    runner = runner or scene.actor
    return ExecutionContext(
        run_id=run_id,
        user_id=runner,
        workflow_id=scene.ids["workflow"],
        call_context=CallContext(
            principal=provisional_principal(runner),
            binding=Binding(
                kind="workflow", id=scene.ids["workflow"], owner_id=scene.users["owner"]
            ),
            entry="workflow_node",
            workflow_run_id=run_id,
        ),
    )


async def _run(agent_id: str | None, context: ExecutionContext) -> NodeResult:
    data: dict[str, Any] = {"prompt_template": "go"}
    if agent_id is not None:
        data["agent_id"] = agent_id
    node = WorkflowNodeDef(id="agent_1", type=NodeType.AGENT, data=data)
    return await AgentExecutor().execute(node, VariableStore(), context)


def _names(tools: Any) -> set[str]:
    return {t.name for t in tools.list_tools()}


class TestAnAgentThatCannotBeUsedFailsTheStep:
    async def test_an_agent_the_author_can_no_longer_see(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """It ran a bare model with the built-in tools and no hooks."""
        scene = await m.seed_scene(factory, m.SceneKey("owner", True, True, False))
        async with factory() as s:
            agent = await s.get(Agent, scene.ids["agent"])
            assert agent is not None
            agent.user_id = scene.users["outsider"]  # handed over; no longer shared
            agent.visibility = "private"
            await s.commit()

        with m.outside_world(factory) as out:
            result = await _run(scene.ids["agent"], _context(scene))

        assert result.status == NodeStatus.FAILED
        assert "not found or not accessible" in (result.error or "")
        assert out.agents_built == []

    async def test_an_agent_its_owner_switched_off(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """A switched-off agent kept running in workflows."""
        scene = await m.seed_scene(factory, m.SceneKey("owner", True, False, False))
        with m.outside_world(factory) as out:
            result = await _run(scene.ids["agent"], _context(scene))

        assert result.status == NodeStatus.FAILED
        assert "is disabled" in (result.error or "")
        assert out.agents_built == []

    async def test_an_agent_id_that_does_not_exist(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await m.seed_scene(factory, m.SceneKey("owner", True, True, False))
        with m.outside_world(factory) as out:
            result = await _run("no-such-agent", _context(scene))

        assert result.status == NodeStatus.FAILED
        assert out.agents_built == []

    async def test_a_run_with_no_identity(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await m.seed_scene(factory, m.SceneKey("owner", True, True, False))
        with m.outside_world(factory) as out:
            result = await _run(
                scene.carrier_agent_id,
                ExecutionContext(run_id="r", user_id="", workflow_id="w"),
            )

        assert result.status == NodeStatus.FAILED
        assert "no user identity" in (result.error or "")
        assert out.agents_built == []


class TestTheAgentIsJudgedByTheWorkflowAuthor:
    async def test_a_runner_who_never_subscribed_to_the_agent_still_runs_it(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """The workflow is the delegation: its author put the agent in it."""
        scene = await m.seed_scene(factory, m.SceneKey("member", True, True, False))
        with m.outside_world(factory) as out:
            result = await _run(scene.ids["agent"], _context(scene))

        assert result.status == NodeStatus.COMPLETED, result.error
        assert out.agents_built[-1]["extra_instructions"] == m.AGENT_MARKER
        assert out.agents_built[-1]["user_id"] == scene.actor

    async def test_the_runners_own_subscription_does_not_stand_in_for_the_authors(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await m.seed_scene(factory, m.SceneKey("subscriber", True, True, False))
        stranger = scene.users["outsider"]  # an author with no access to the agent
        context = _context(scene)
        assert context.call_context is not None and context.call_context.binding is not None
        context.call_context = CallContext(
            principal=context.call_context.principal,
            binding=Binding(kind="workflow", id="wf-of-a-stranger", owner_id=stranger),
            entry="workflow_node",
        )
        with m.outside_world(factory) as out:
            result = await _run(scene.ids["agent"], context)

        assert result.status == NodeStatus.FAILED
        assert out.agents_built == []


class TestTheAgentsBoundResources:
    async def test_its_mcp_servers_are_connected_and_released(
        self, factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scene = await m.seed_scene(factory, m.SceneKey("subscriber", True, True, True))
        released: list[bool] = []
        with m.outside_world(factory) as out:
            from fim_one.core import mcp as mcp_mod

            async def _release(_self: Any) -> None:
                released.append(True)

            monkeypatch.setattr(mcp_mod.MCPClient, "disconnect_all", _release)
            result = await _run(scene.carrier_agent_id, _context(scene))

            assert result.status == NodeStatus.COMPLETED, result.error
            built = out.agents_built[-1]
            assert {"mcp", "connector", "kb_retrieve"} <= _names(built["tools"])
            # Opened with the runner's own credential, not the owner's.
            assert out.mcp_connects[scene.names["mcp_stdio"]]["env"] == m.PERSONAL_ENV
            assert out.mcp_connects[scene.names["mcp_http"]]["headers"] == m.PERSONAL_HEADERS
            assert [h.name for h in built["hook_registry"].list_hooks()] == ["approval_gate"]
        assert released == [True]

    async def test_its_calls_are_recorded_under_the_run_and_the_step(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await m.seed_scene(factory, m.SceneKey("owner", True, True, False))
        with m.outside_world(factory) as out:
            result = await _run(scene.carrier_agent_id, _context(scene, run_id="run-42"))
            assert result.status == NodeStatus.COMPLETED, result.error
            tools = out.agents_built[-1]["tools"]
            await tools.get("connector").run(
                subcommand="execute",
                connector=scene.names["api_bearer"],
                action="ping",
                parameters={},
            )
            await tools.get("mcp").run(
                subcommand="call", server=scene.names["mcp_http"], tool="echo", parameters={}
            )

        async with factory() as s:
            rows = (await s.execute(select(ConnectorCallLog))).scalars().all()
        assert len(rows) == 2
        for row in rows:
            assert (row.entry, row.workflow_run_id, row.agent_id) == (
                "workflow_node",
                "run-42",
                scene.carrier_agent_id,
            )
            assert row.user_id == scene.actor


class TestAStepWithNoAgent:
    async def test_it_runs_a_model_with_built_in_tools_and_nothing_of_the_users(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await m.seed_scene(factory, m.SceneKey("owner", True, True, False))
        with m.outside_world(factory) as out:
            result = await _run(None, _context(scene))

        assert result.status == NodeStatus.COMPLETED, result.error
        built = out.agents_built[-1]
        names = _names(built["tools"])
        # No agent was selected to bind connectors or MCP servers to.
        assert not names & {"connector", "database", "mcp", "call_agent", "run_workflow"}
        assert out.mcp_connects == {}
        assert {"calculator", "web_fetch"} <= names
        assert built["extra_instructions"] is None and built["agent_id"] is None
        assert [h.name for h in built["hook_registry"].list_hooks()] == ["approval_gate"]


class TestTheRunServiceGivesStepsTheirContext:
    """Every entry point's run carries who runs it, through whose workflow."""

    @pytest.mark.parametrize(
        ("mode", "runs_as", "entry"),
        [
            (rs.RunMode.INTERACTIVE, "subscriber", "workflow_node"),
            (rs.RunMode.BATCH, "subscriber", "workflow_node"),
            (rs.RunMode.TOOL, "subscriber", "run_workflow"),
            (rs.RunMode.TRIGGER, "owner", "trigger"),
            (rs.RunMode.SCHEDULE, "owner", "schedule"),
        ],
    )
    async def test_start_run(
        self,
        factory: async_sessionmaker[AsyncSession],
        mode: rs.RunMode,
        runs_as: str,
        entry: str,
    ) -> None:
        scene = await m.seed_scene(factory, m.SceneKey("subscriber", True, True, False))
        seen: list[Any] = []

        class _Engine:
            def __init__(self, **_kwargs: Any) -> None:
                pass

            async def execute_streaming(
                self, _parsed: Any, _inputs: Any, context: Any = None
            ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
                seen.append(context)
                yield "run_completed", {"status": "completed", "outputs": {}, "total_tokens": 0}

        with m.outside_world(factory):
            async with factory() as s:
                wf = await s.get(Workflow, scene.ids["workflow"])
                assert wf is not None
            service = rs.WorkflowRunService(engine_factory=_Engine)
            outcome = await service.run_to_completion(
                wf, caller_id=scene.users["subscriber"], inputs={}, mode=mode
            )

        (context,) = seen
        ctx = context.call_context
        assert ctx.principal.user_id == scene.users[runs_as] == context.user_id
        assert (ctx.binding.kind, ctx.binding.id, ctx.binding.owner_id) == (
            "workflow",
            wf.id,
            scene.users["owner"],
        )
        assert ctx.binding.org_id == wf.org_id
        assert (ctx.entry, ctx.workflow_run_id) == (entry, outcome.run_id)


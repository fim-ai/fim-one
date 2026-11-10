"""The agent runtime port (``core/assembly/runtime.py``) and its web implementation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.assembly import runtime
from fim_one.core.assembly.errors import AssemblyRefusal
from fim_one.core.assembly.principal import CallContext, provisional_principal
from fim_one.db.base import Base
from fim_one.web.services import agent_runtime
from fim_one.web.services.agent_runtime import WebAgentRuntimeFactory

from .assembly import matrix as m

pytestmark = pytest.mark.timeout(60)


@pytest.fixture()
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _ctx(user_id: str, entry: Any = "sub_agent") -> CallContext:
    return CallContext(principal=provisional_principal(user_id), binding=None, entry=entry)


def _names(tools: Any) -> set[str]:
    return {t.name for t in tools.list_tools()}


class TestPort:
    def test_nothing_is_built_with_no_factory_registered(self) -> None:
        runtime.set_agent_runtime_factory(None)
        with pytest.raises(RuntimeError, match="No agent runtime factory"):
            runtime.get_agent_runtime_factory()

    def test_the_app_registers_the_web_factory(self) -> None:
        from fim_one.web.services.assembly_ports import register_assembly_ports

        runtime.set_agent_runtime_factory(None)
        register_assembly_ports()
        assert isinstance(runtime.get_agent_runtime_factory(), WebAgentRuntimeFactory)


class TestBuild:
    """``build`` is the chat stages back to back."""

    async def test_an_agents_runtime_has_its_tools_connected_and_its_gate(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await m.seed_scene(factory, m.SceneKey("subscriber", True, True, False))
        with m.outside_world(factory) as out:
            built = await runtime.get_agent_runtime_factory().build(
                _ctx(scene.actor), scene.carrier_agent_id
            )
            try:
                # Connector and knowledge-base tools, and the MCP servers connected.
                assert {"connector", "mcp", "kb_retrieve"} <= _names(built.tools)
                # Fallback is on: all of them, a credential in the address or not.
                assert set(out.mcp_connects) == {scene.names[r] for r in m.MCP_SERVERS}
                assert built.mcp_client is not None
                assert [h.name for h in built.hooks.list_hooks()] == ["approval_gate"]
                assert built.instructions == "carrier"
                assert built.agent is not None
                assert built.agent["agent_id"] == scene.carrier_agent_id
                # The agent is the binding its resources are used through;
                # the entry and the principal are the caller's.
                assert built.ctx.binding is not None
                assert (built.ctx.binding.kind, built.ctx.binding.owner_id) == (
                    "agent",
                    scene.users["owner"],
                )
                assert built.ctx.principal.user_id == scene.actor
                assert built.ctx.entry == "sub_agent"
                assert built.ctx.agent_id == scene.carrier_agent_id
            finally:
                await built.aclose()
            assert built.mcp_client is None

    async def test_it_is_what_the_chat_stages_give(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await m.seed_scene(factory, m.SceneKey("owner", True, True, True))
        with m.outside_world(factory):
            ctx = _ctx(scene.actor, "chat")
            built = await WebAgentRuntimeFactory().build(ctx, scene.carrier_agent_id)

            cfg = await agent_runtime.resolve_agent(ctx, scene.carrier_agent_id)
            staged = await agent_runtime.resolve_toolset(
                agent_runtime.call_context(ctx.principal, cfg), cfg
            )
            await agent_runtime.connect_mcp(staged, cfg)
            hooks = await agent_runtime.build_hooks(cfg)

        assert built.agent == cfg
        assert _names(built.tools) == _names(staged)
        assert [h.name for h in built.hooks.list_hooks()] == [h.name for h in hooks.list_hooks()]

    @pytest.mark.parametrize(
        ("role", "switch_off", "code"),
        [("outsider", False, "resource_not_found"), ("owner", True, "resource_inactive")],
    )
    async def test_an_agent_the_caller_cannot_use_is_a_refusal(
        self,
        factory: async_sessionmaker[AsyncSession],
        role: str,
        switch_off: bool,
        code: str,
    ) -> None:
        scene = await m.seed_scene(factory, m.SceneKey(role, True, not switch_off, False))
        with m.outside_world(factory) as out:
            with pytest.raises(AssemblyRefusal) as refused:
                await WebAgentRuntimeFactory().build(_ctx(scene.actor), scene.ids["agent"])
        assert refused.value.error_code == code
        assert out.mcp_connects == {}

    @pytest.mark.parametrize("named", [True, False])
    async def test_a_context_with_no_acting_user_is_refused(
        self, factory: async_sessionmaker[AsyncSession], named: bool
    ) -> None:
        """With no user to check for, the agent was read without any check."""
        from fim_one.db.models.conversation import Conversation

        # An agent the outsider cannot use, named or as a conversation's own.
        scene = await m.seed_scene(factory, m.SceneKey("outsider", True, True, False))
        async with factory() as s:
            s.add(
                Conversation(
                    id="conv-of-owner",
                    user_id=scene.users["owner"],
                    title="t",
                    mode="react",
                    agent_id=scene.ids["agent"],
                )
            )
            await s.commit()
        agent_id = scene.ids["agent"] if named else None
        conversation_id = None if named else "conv-of-owner"
        with m.outside_world(factory) as out:
            with pytest.raises(AssemblyRefusal) as refused:
                await agent_runtime.resolve_agent(
                    _ctx(""), agent_id, conversation_id=conversation_id
                )
            assert (refused.value.error_code, refused.value.status_code) == (
                "authentication_required",
                401,
            )
            with pytest.raises(AssemblyRefusal):
                await WebAgentRuntimeFactory().build(
                    _ctx(""), agent_id, conversation_id=conversation_id
                )
        assert out.mcp_connects == {}

    async def test_hooks_that_cannot_be_built_leave_no_connection_open(
        self, factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scene = await m.seed_scene(factory, m.SceneKey("owner", True, True, False))
        closed: list[bool] = []

        async def _boom(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError("hooks unavailable")

        monkeypatch.setattr(agent_runtime, "build_hooks", _boom)
        with m.outside_world(factory):
            from fim_one.core import mcp as mcp_mod

            original = mcp_mod.MCPClient.disconnect_all

            async def _closing(self: Any) -> None:
                closed.append(True)
                await original(self)

            monkeypatch.setattr(mcp_mod.MCPClient, "disconnect_all", _closing)
            with pytest.raises(RuntimeError, match="hooks unavailable"):
                await WebAgentRuntimeFactory().build(_ctx(scene.actor), scene.carrier_agent_id)
        assert closed == [True]


class TestADelegatedAgent:
    """B17 / N7: ``call_agent`` runs the delegate with the tools a chat with it has."""

    async def test_its_mcp_servers_are_connected_for_the_run_and_released_after(
        self, factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scene = await m.seed_scene(factory, m.SceneKey("subscriber", True, True, False))
        released: list[bool] = []
        with m.outside_world(factory) as out:
            from fim_one.core import mcp as mcp_mod

            async def _release(_self: Any) -> None:
                released.append(True)

            monkeypatch.setattr(mcp_mod.MCPClient, "disconnect_all", _release)
            ctx = _ctx(scene.actor, "chat_noagent")
            tools = await agent_runtime.resolve_toolset(ctx, None)
            call_agent = tools.get("call_agent")
            assert call_agent is not None
            # The catalogue names agents; it carries none of their configuration.
            assert set(call_agent._agents[scene.carrier_agent_id]) == {"id", "name", "description"}

            answer = await call_agent.run(agent_id=scene.carrier_agent_id, task="go")

            assert answer == "done"
            delegate = out.agents_built[-1]
            assert {"mcp", "connector", "kb_retrieve"} <= _names(delegate["tools"])
            assert set(out.mcp_connects) == {scene.names[r] for r in m.MCP_SERVERS}
            # The subscriber has no credential of their own: the owner allowed fallback.
            assert out.mcp_connects[scene.names["mcp_http"]]["headers"] == m.OWNER_HEADERS
            assert delegate["agent_id"] == scene.carrier_agent_id
            assert delegate["user_id"] == scene.actor
            assert [h.name for h in delegate["hook_registry"].list_hooks()] == ["approval_gate"]
        assert released == [True]

    async def test_an_agent_switched_off_after_the_catalogue_was_built_is_not_run(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        from fim_one.db.models.agent import Agent

        scene = await m.seed_scene(factory, m.SceneKey("subscriber", True, True, False))
        with m.outside_world(factory) as out:
            tools = await agent_runtime.resolve_toolset(_ctx(scene.actor, "chat_noagent"), None)
            async with factory() as s:
                agent = await s.get(Agent, scene.carrier_agent_id)
                assert agent is not None
                agent.is_active = False
                await s.commit()

            answer = await tools.get("call_agent").run(agent_id=scene.carrier_agent_id, task="go")

        assert "is not available" in answer
        assert out.agents_built == []


class TestCallContext:
    def test_an_agent_with_an_owner_is_the_binding(self) -> None:
        ctx = agent_runtime.call_context(
            provisional_principal("u1"),
            {"agent_id": "a1", "owner_user_id": "o1", "org_id": "org"},
            conversation_id="c1",
        )
        assert ctx.binding is not None
        assert (ctx.binding.id, ctx.binding.owner_id, ctx.binding.org_id) == ("a1", "o1", "org")
        assert (ctx.entry, ctx.agent_id, ctx.conversation_id) == ("chat", "a1", "c1")
        assert ctx.subject_id == "o1"

    def test_no_agent_is_the_principal_using_resources_directly(self) -> None:
        ctx = agent_runtime.call_context(provisional_principal("u1"), None)
        assert ctx.binding is None and ctx.entry == "chat_noagent" and ctx.subject_id == "u1"

    def test_a_nested_use_keeps_the_run_it_belongs_to(self) -> None:
        base = CallContext(
            principal=provisional_principal("u1"),
            binding=None,
            entry="workflow_node",
            workflow_run_id="run-1",
            node_id="n1",
        )
        ctx = agent_runtime.call_context(
            base.principal, {"agent_id": "a1", "owner_user_id": "o1"}, base=base
        )
        assert (ctx.entry, ctx.workflow_run_id, ctx.node_id) == ("workflow_node", "run-1", "n1")
        assert ctx.binding is not None and ctx.binding.kind == "agent"
        assert ctx.agent_id == "a1"


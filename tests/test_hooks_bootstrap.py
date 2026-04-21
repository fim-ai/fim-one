"""Tests for :mod:`fim_one.web.hooks_bootstrap`.

Covers:

- Empty / missing / malformed ``model_config_json`` → empty HookRegistry
- Recognised hook names → populated HookRegistry
- Unknown hook names → skipped with a warning, never raise
- Non-string / whitespace entries → skipped
- End-to-end integration with :class:`ReActAgent` + :class:`FeishuGateHook`
  via mocked LLM and Feishu send.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.web.models  # noqa: F401 — ensures ORM classes are registered
from fim_one.core.agent.hooks import HookPoint, HookRegistry
from fim_one.core.channels import ChannelSendResult
from fim_one.db.base import Base
from fim_one.web.hooks_bootstrap import (
    HOOK_FACTORIES,
    build_hook_registry_for_agent,
)
from fim_one.web.models.agent import Agent
from fim_one.web.models.channel import Channel, ConfirmationRequest
from fim_one.web.models.organization import Organization
from fim_one.web.models.user import User

# ---------------------------------------------------------------------------
# Shared fixtures (mirror tests/test_feishu_gate_hook.py)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stable_cred_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import fim_one.core.security.encryption as enc

    monkeypatch.setenv(
        "CREDENTIAL_ENCRYPTION_KEY", "test-bootstrap-key-abcdefghijklmnop"
    )
    enc._CREDENTIAL_KEY_RAW = "test-bootstrap-key-abcdefghijklmnop"
    enc._cred_fernet_instance = None


@pytest.fixture()
async def engine() -> AsyncIterator[Any]:
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture()
async def session_factory(
    engine: Any,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture()
async def seed(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, Any]:
    async with session_factory() as db:
        user = User(
            id=str(uuid.uuid4()),
            username="ops",
            email="ops@test.com",
            is_admin=False,
        )
        db.add(user)
        org = Organization(
            id=str(uuid.uuid4()),
            name="BootstrapCo",
            slug=f"bootstrap-{uuid.uuid4().hex[:6]}",
            owner_id=user.id,
        )
        db.add(org)
        channel = Channel(
            id=str(uuid.uuid4()),
            name="Feishu",
            type="feishu",
            org_id=org.id,
            created_by=user.id,
            config={
                "app_id": "cli_x",
                "app_secret": "s",
                "chat_id": "oc_group",
            },
        )
        db.add(channel)
        await db.commit()
    return {"user_id": user.id, "org_id": org.id, "channel_id": channel.id}


# ---------------------------------------------------------------------------
# Task 4 — build_hook_registry_for_agent unit tests
# ---------------------------------------------------------------------------


class TestBuildHookRegistryForAgent:
    """Behavioural contract for the bootstrap function."""

    @pytest.mark.asyncio
    async def test_empty_when_model_config_json_is_none(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        agent = SimpleNamespace(model_config_json=None)
        registry = await build_hook_registry_for_agent(agent, session_factory)

        assert isinstance(registry, HookRegistry)
        assert len(registry) == 0

    @pytest.mark.asyncio
    async def test_empty_when_hooks_key_missing(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        agent = SimpleNamespace(model_config_json={"model": "gpt-4o"})
        registry = await build_hook_registry_for_agent(agent, session_factory)

        assert len(registry) == 0

    @pytest.mark.asyncio
    async def test_populated_with_feishu_gate(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        agent = SimpleNamespace(
            model_config_json={"hooks": {"class_hooks": ["feishu_gate"]}}
        )
        registry = await build_hook_registry_for_agent(agent, session_factory)

        assert len(registry) == 1
        pre_hooks = registry.list_hooks(HookPoint.PRE_TOOL_USE)
        assert len(pre_hooks) == 1
        assert pre_hooks[0].name == "feishu_gate"

    @pytest.mark.asyncio
    async def test_unknown_hook_is_skipped_with_warning(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        agent = SimpleNamespace(
            model_config_json={
                "hooks": {"class_hooks": ["feishu_gate", "ghost_hook"]}
            }
        )

        with caplog.at_level(logging.WARNING, logger="fim_one.web.hooks_bootstrap"):
            registry = await build_hook_registry_for_agent(agent, session_factory)

        assert len(registry) == 1
        assert any("ghost_hook" in rec.message for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_malformed_hooks_block_is_skipped(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        agent = SimpleNamespace(model_config_json={"hooks": "not-a-dict"})
        with caplog.at_level(logging.WARNING, logger="fim_one.web.hooks_bootstrap"):
            registry = await build_hook_registry_for_agent(agent, session_factory)

        assert len(registry) == 0

    @pytest.mark.asyncio
    async def test_malformed_class_hooks_list_is_skipped(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        agent = SimpleNamespace(
            model_config_json={"hooks": {"class_hooks": "feishu_gate"}}
        )
        with caplog.at_level(logging.WARNING, logger="fim_one.web.hooks_bootstrap"):
            registry = await build_hook_registry_for_agent(agent, session_factory)

        assert len(registry) == 0

    @pytest.mark.asyncio
    async def test_non_string_entry_is_skipped(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        agent = SimpleNamespace(
            model_config_json={
                "hooks": {"class_hooks": [None, 42, "  ", "feishu_gate"]}
            }
        )
        registry = await build_hook_registry_for_agent(agent, session_factory)

        assert len(registry) == 1  # only feishu_gate survives

    @pytest.mark.asyncio
    async def test_class_hooks_null_is_tolerated(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        agent = SimpleNamespace(
            model_config_json={"hooks": {"class_hooks": None}}
        )
        registry = await build_hook_registry_for_agent(agent, session_factory)
        assert len(registry) == 0

    @pytest.mark.asyncio
    async def test_model_config_json_as_non_dict_is_tolerated(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        agent = SimpleNamespace(model_config_json="oops")
        registry = await build_hook_registry_for_agent(agent, session_factory)
        assert len(registry) == 0

    def test_factory_registry_exposes_feishu_gate(self) -> None:
        assert "feishu_gate" in HOOK_FACTORIES


# ---------------------------------------------------------------------------
# Task 4 — integration: ReActAgent + FeishuGateHook end-to-end
# ---------------------------------------------------------------------------


class _MockLLM:
    """Minimal BaseLLM shim that returns a single hard-coded tool_call.

    We only exercise the pre-tool-use hook path — no real token generation
    is needed.  The test drives ``execute_action`` directly.
    """

    def __init__(self) -> None:
        self.abilities: dict[str, Any] = {"tool_call": False}
        self.model = "mock-test"
        self.context_size = 8192


class _FakeConnectorTool:
    """Mimics the subset of :class:`ConnectorToolAdapter` we care about."""

    name = "demo__sensitive_action"
    description = "Fake connector action that requires confirmation."
    category = "connector"

    def __init__(self) -> None:
        self.called_with: dict[str, Any] | None = None

    @property
    def requires_confirmation(self) -> bool:
        return True

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def display_name(self) -> str:
        return "Demo: Sensitive Action"

    async def run(self, **kwargs: Any) -> str:
        self.called_with = kwargs
        return "ok"


@pytest.mark.asyncio
async def test_integration_feishu_gate_blocks_then_approves(
    session_factory: async_sessionmaker[AsyncSession],
    seed: dict[str, Any],
) -> None:
    """End-to-end: connector tool + feishu_gate registered + approval flow.

    The mocked Feishu card send succeeds; a background task flips the
    :class:`ConfirmationRequest` row to ``approved``.  The hook must
    allow the tool call, and the tool must execute.
    """
    from fim_one.core.tool.connector.adapter import (  # noqa: F401 — real adapter import check
        ConnectorToolAdapter,
    )
    from fim_one.core.tool.registry import ToolRegistry

    # --- Build registry via the bootstrap with a FeishuGate-only config ---
    agent_shim = SimpleNamespace(
        model_config_json={"hooks": {"class_hooks": ["feishu_gate"]}}
    )
    registry = await build_hook_registry_for_agent(agent_shim, session_factory)
    assert len(registry) == 1

    # --- Build ReActAgent with just the mock connector tool ---
    from fim_one.core.agent.react import ReActAgent

    tools = ToolRegistry()
    fake_tool = _FakeConnectorTool()
    tools.register(fake_tool)

    # Swap in a more tolerant ``isinstance`` check: our helper reads
    # ``requires_confirmation`` off ``ConnectorToolAdapter`` directly, so we
    # patch the import in react.py to use our fake class.  (Python duck-types
    # the attribute access regardless.)
    # Seed the Agent ORM row so FeishuGateHook can look up its
    # confirmation routing fields.
    async with session_factory() as db:
        db.add(
            Agent(
                id="agent-int-1",
                user_id=seed["user_id"],
                org_id=seed["org_id"],
                name="Integration Agent",
                confirmation_mode="auto",
            )
        )
        await db.commit()

    agent = ReActAgent(
        llm=_MockLLM(),
        tools=tools,
        hook_registry=registry,
        agent_id="agent-int-1",
        org_id=seed["org_id"],
        user_id=seed["user_id"],
    )

    # --- Mock Feishu send + arrange an approve-after-delay background task ---
    send_mock = AsyncMock(return_value=ChannelSendResult(ok=True))

    async def _approve_after_delay() -> None:
        # Poll until the hook has written the pending row, then flip it.
        for _ in range(100):
            await asyncio.sleep(0.05)
            async with session_factory() as db:
                row = (
                    await db.execute(
                        select(ConfirmationRequest).where(
                            ConfirmationRequest.status == "pending"
                        )
                    )
                ).scalar_one_or_none()
                if row is not None:
                    row.status = "approved"
                    await db.commit()
                    return

    # Shorten the hook's poll interval so the test runs fast.  The bootstrap
    # creates a hook with the default timeout/interval — fine for production,
    # too slow for unit tests.  Rebuild the registry with a fast hook; the
    # default-timeout path is covered by TestBuildHookRegistryForAgent above.
    from fim_one.core.hooks import create_feishu_gate_hook

    registry2 = HookRegistry()
    registry2.register(
        create_feishu_gate_hook(
            session_factory=session_factory,
            timeout_seconds=5,
            poll_interval_seconds=0.05,
        ).as_hook()
    )
    agent._hook_registry = registry2

    # --- Drive the hook via execute_action (the DAG path) ---
    from fim_one.core.agent.types import Action

    action = Action(
        type="tool_call",
        reasoning="",
        tool_name=fake_tool.name,
        tool_args={"amount": 42},
    )

    with patch(
        "fim_one.core.channels.feishu.FeishuChannel.send_interactive_card",
        new=send_mock,
    ):
        approver_task = asyncio.create_task(_approve_after_delay())
        step = await agent._execute_tool_call(action)
        await approver_task

    # Tool was actually called (hook let it through after approval).
    assert fake_tool.called_with == {"amount": 42}
    # No error surfaced on the StepResult.
    assert step.error is None
    # Card delivered to the seeded Feishu chat_id.
    send_mock.assert_awaited_once()
    assert send_mock.await_args is not None
    assert send_mock.await_args.args[0] == "oc_group"

    # The row is flipped to approved.
    async with session_factory() as db:
        row = (
            await db.execute(select(ConfirmationRequest))
        ).scalar_one()
        assert row.status == "approved"
        assert row.org_id == seed["org_id"]


@pytest.mark.asyncio
async def test_integration_metadata_carries_requires_confirmation(
    session_factory: async_sessionmaker[AsyncSession],
    seed: dict[str, Any],
) -> None:
    """Regression guard: HookContext.metadata MUST include the flag.

    Without ``requires_confirmation`` in ``metadata`` the gate short-
    circuits and every call slips through — the exact bug called out in
    the audit.
    """
    from fim_one.core.agent.hooks import Hook, HookContext, HookPoint
    from fim_one.core.agent.react import ReActAgent
    from fim_one.core.agent.types import Action
    from fim_one.core.tool.registry import ToolRegistry

    captured: dict[str, Any] = {}

    async def _spy(ctx: HookContext) -> Any:
        from fim_one.core.agent.hooks import HookResult

        captured["metadata"] = dict(ctx.metadata or {})
        captured["org_id"] = ctx.metadata.get("org_id") if ctx.metadata else None
        captured["agent_id"] = ctx.agent_id
        captured["user_id"] = ctx.user_id
        return HookResult(allow=True)

    spy_hook = Hook(
        name="spy",
        hook_point=HookPoint.PRE_TOOL_USE,
        handler=_spy,
    )
    registry = HookRegistry()
    registry.register(spy_hook)

    tools = ToolRegistry()
    fake_tool = _FakeConnectorTool()
    tools.register(fake_tool)

    agent = ReActAgent(
        llm=_MockLLM(),
        tools=tools,
        hook_registry=registry,
        agent_id="agent-int-2",
        org_id=seed["org_id"],
        user_id=seed["user_id"],
    )

    action = Action(
        type="tool_call",
        reasoning="",
        tool_name=fake_tool.name,
        tool_args={"x": 1},
    )
    await agent._execute_tool_call(action)

    assert captured["metadata"].get("requires_confirmation") is True
    assert captured["org_id"] == seed["org_id"]
    assert captured["agent_id"] == "agent-int-2"
    assert captured["user_id"] == seed["user_id"]


@pytest.mark.asyncio
async def test_integration_non_connector_tool_has_requires_confirmation_false(
    session_factory: async_sessionmaker[AsyncSession],
    seed: dict[str, Any],
) -> None:
    """Built-in tools MUST NOT trip the feishu gate."""
    from fim_one.core.agent.hooks import Hook, HookContext, HookPoint, HookResult
    from fim_one.core.agent.react import ReActAgent
    from fim_one.core.agent.types import Action
    from fim_one.core.tool.base import BaseTool
    from fim_one.core.tool.registry import ToolRegistry

    class _BuiltinTool(BaseTool):
        name = "builtin_noop"
        description = "A harmless builtin."

        @property
        def parameters_schema(self) -> dict[str, Any]:
            return {"type": "object", "properties": {}, "required": []}

        async def run(self, **kwargs: Any) -> str:
            return "noop"

    captured: dict[str, Any] = {}

    async def _spy(ctx: HookContext) -> HookResult:
        captured["metadata"] = dict(ctx.metadata or {})
        return HookResult(allow=True)

    registry = HookRegistry()
    registry.register(
        Hook(name="spy", hook_point=HookPoint.PRE_TOOL_USE, handler=_spy)
    )
    tools = ToolRegistry()
    tools.register(_BuiltinTool())

    agent = ReActAgent(
        llm=_MockLLM(),
        tools=tools,
        hook_registry=registry,
        agent_id="agent-int-3",
        org_id=seed["org_id"],
        user_id=seed["user_id"],
    )

    action = Action(
        type="tool_call",
        reasoning="",
        tool_name="builtin_noop",
        tool_args={},
    )
    await agent._execute_tool_call(action)

    assert captured["metadata"].get("requires_confirmation") is False

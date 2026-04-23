"""Tests for FeishuGateHook — end-to-end confirmation flow with mocks."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.web.models  # noqa: F401
from fim_one.core.agent.hooks import HookContext, HookPoint
from fim_one.core.channels import ChannelSendResult
from fim_one.core.hooks import FeishuGateHook, create_feishu_gate_hook
from fim_one.db.base import Base
from fim_one.web.models.agent import Agent
from fim_one.web.models.channel import Channel, ConfirmationRequest
from fim_one.web.models.organization import Organization
from fim_one.web.models.user import User


@pytest.fixture(autouse=True)
def _stable_cred_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import fim_one.core.security.encryption as enc

    monkeypatch.setenv(
        "CREDENTIAL_ENCRYPTION_KEY", "test-gate-hook-key-abcdefghijklmnop"
    )
    enc._CREDENTIAL_KEY_RAW = "test-gate-hook-key-abcdefghijklmnop"
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
async def session_factory(engine: Any) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed_agent(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user_id: str,
    org_id: str | None,
    agent_id: str = "agent-1",
    confirmation_mode: str = "auto",
    require_confirmation_for_all: bool = False,
    approval_channel_id: str | None = None,
    approver_scope: str = "initiator",
) -> str:
    """Insert an Agent row with the given confirmation routing fields."""
    async with session_factory() as db:
        db.add(
            Agent(
                id=agent_id,
                user_id=user_id,
                org_id=org_id,
                name="Test Agent",
                confirmation_mode=confirmation_mode,
                require_confirmation_for_all=require_confirmation_for_all,
                approval_channel_id=approval_channel_id,
                confirmation_approver_scope=approver_scope,
            )
        )
        await db.commit()
    return agent_id


@pytest.fixture()
async def seed(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, Any]:
    """Baseline seed: user + org + active feishu channel + agent(mode=auto)."""
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
            name="DemoCo",
            slug=f"democo-{uuid.uuid4().hex[:6]}",
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

    agent_id = await _seed_agent(
        session_factory, user_id=user.id, org_id=org.id
    )
    return {
        "user_id": user.id,
        "org_id": org.id,
        "channel_id": channel.id,
        "agent_id": agent_id,
    }


def _make_context(
    *,
    org_id: str | None,
    user_id: str,
    agent_id: str = "agent-1",
    requires: bool = True,
) -> HookContext:
    metadata: dict[str, Any] = {"requires_confirmation": requires}
    if org_id is not None:
        metadata["org_id"] = org_id
    return HookContext(
        hook_point=HookPoint.PRE_TOOL_USE,
        tool_name="oa__purchase_pay",
        tool_args={"vendor": "X", "amount": 500},
        agent_id=agent_id,
        user_id=user_id,
        metadata=metadata,
    )


class TestShouldTrigger:
    @pytest.mark.asyncio
    async def test_skips_when_not_required(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        seed: dict[str, Any],
    ) -> None:
        hook = create_feishu_gate_hook(session_factory=session_factory)
        ctx = _make_context(
            org_id=seed["org_id"],
            user_id=seed["user_id"],
            agent_id=seed["agent_id"],
            requires=False,
        )
        result = await hook.execute(ctx)
        assert result.allow is True
        assert result.error is None


class TestGateFlow:
    @pytest.mark.asyncio
    async def test_approve_flow(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        seed: dict[str, Any],
    ) -> None:
        hook = create_feishu_gate_hook(
            session_factory=session_factory,
            timeout_seconds=5,
            poll_interval_seconds=0.05,
        )
        ctx = _make_context(
            org_id=seed["org_id"],
            user_id=seed["user_id"],
            agent_id=seed["agent_id"],
        )

        # Mock the send call — assert it was invoked with the group chat id.
        send_mock = AsyncMock(return_value=ChannelSendResult(ok=True))

        async def _approve_after_delay() -> None:
            await asyncio.sleep(0.2)
            async with session_factory() as db:
                row = (
                    await db.execute(
                        select(ConfirmationRequest).where(
                            ConfirmationRequest.status == "pending"
                        )
                    )
                ).scalar_one()
                row.status = "approved"
                await db.commit()

        with patch(
            "fim_one.core.channels.feishu.FeishuChannel.send_interactive_card",
            new=send_mock,
        ):
            approver = asyncio.create_task(_approve_after_delay())
            result = await hook.execute(ctx)
            await approver

        assert result.allow is True
        # Card sent to the org's chat_id.
        send_mock.assert_awaited_once()
        call_args = send_mock.await_args
        assert call_args is not None
        assert call_args.args[0] == "oc_group"
        # DB row exists with status=approved and mode=channel.
        async with session_factory() as db:
            row = (
                await db.execute(select(ConfirmationRequest))
            ).scalar_one()
            assert row.status == "approved"
            assert row.mode == "channel"
            assert row.payload is not None
            assert row.payload["tool_name"] == "oa__purchase_pay"

    @pytest.mark.asyncio
    async def test_reject_flow(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        seed: dict[str, Any],
    ) -> None:
        hook = create_feishu_gate_hook(
            session_factory=session_factory,
            timeout_seconds=5,
            poll_interval_seconds=0.05,
        )
        ctx = _make_context(
            org_id=seed["org_id"],
            user_id=seed["user_id"],
            agent_id=seed["agent_id"],
        )

        async def _reject() -> None:
            await asyncio.sleep(0.15)
            async with session_factory() as db:
                row = (
                    await db.execute(
                        select(ConfirmationRequest).where(
                            ConfirmationRequest.status == "pending"
                        )
                    )
                ).scalar_one()
                row.status = "rejected"
                await db.commit()

        with patch(
            "fim_one.core.channels.feishu.FeishuChannel.send_interactive_card",
            new=AsyncMock(return_value=ChannelSendResult(ok=True)),
        ):
            rejecter = asyncio.create_task(_reject())
            result = await hook.execute(ctx)
            await rejecter

        assert result.allow is False
        assert "rejected" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_timeout_marks_expired(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        seed: dict[str, Any],
    ) -> None:
        hook = create_feishu_gate_hook(
            session_factory=session_factory,
            timeout_seconds=0,  # immediate timeout after first poll
            poll_interval_seconds=0.01,
        )
        ctx = _make_context(
            org_id=seed["org_id"],
            user_id=seed["user_id"],
            agent_id=seed["agent_id"],
        )

        with patch(
            "fim_one.core.channels.feishu.FeishuChannel.send_interactive_card",
            new=AsyncMock(return_value=ChannelSendResult(ok=True)),
        ):
            result = await hook.execute(ctx)

        assert result.allow is False
        assert "timed out" in (result.error or "").lower()
        async with session_factory() as db:
            row = (
                await db.execute(select(ConfirmationRequest))
            ).scalar_one()
            assert row.status == "expired"

    @pytest.mark.asyncio
    async def test_no_channel_falls_back_to_inline_in_auto_mode(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        seed: dict[str, Any],
    ) -> None:
        """Auto mode with no active channel → inline (not an error)."""
        async with session_factory() as db:
            row = (
                await db.execute(
                    select(Channel).where(Channel.id == seed["channel_id"])
                )
            ).scalar_one()
            row.is_active = False
            await db.commit()

        hook = create_feishu_gate_hook(
            session_factory=session_factory,
            timeout_seconds=5,
            poll_interval_seconds=0.02,
        )
        ctx = _make_context(
            org_id=seed["org_id"],
            user_id=seed["user_id"],
            agent_id=seed["agent_id"],
        )

        send_mock = AsyncMock(return_value=ChannelSendResult(ok=True))

        async def _approve_after_delay() -> None:
            await asyncio.sleep(0.1)
            async with session_factory() as db:
                row2 = (
                    await db.execute(
                        select(ConfirmationRequest).where(
                            ConfirmationRequest.status == "pending"
                        )
                    )
                ).scalar_one()
                row2.status = "approved"
                await db.commit()

        with patch(
            "fim_one.core.channels.feishu.FeishuChannel.send_interactive_card",
            new=send_mock,
        ):
            approver = asyncio.create_task(_approve_after_delay())
            result = await hook.execute(ctx)
            await approver

        assert result.allow is True
        send_mock.assert_not_awaited()
        async with session_factory() as db:
            row2 = (
                await db.execute(select(ConfirmationRequest))
            ).scalar_one()
            assert row2.mode == "inline"
            assert row2.channel_id is None

    @pytest.mark.asyncio
    async def test_missing_agent_id_skips_gate(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        # Bound-agent-less sessions (pure model chat, quick tasks) have no
        # per-agent confirmation policy to enforce, so the gate must let
        # the tool through instead of hard-blocking with a cryptic error.
        hook = create_feishu_gate_hook(session_factory=session_factory)
        ctx = HookContext(
            hook_point=HookPoint.PRE_TOOL_USE,
            tool_name="x__y",
            metadata={"requires_confirmation": True},
        )
        result = await hook.execute(ctx)
        assert result.allow is True
        assert result.error is None
        assert any("no agent context" in s for s in result.side_effects)

    @pytest.mark.asyncio
    async def test_send_failure_blocks(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        seed: dict[str, Any],
    ) -> None:
        hook = create_feishu_gate_hook(session_factory=session_factory)
        ctx = _make_context(
            org_id=seed["org_id"],
            user_id=seed["user_id"],
            agent_id=seed["agent_id"],
        )
        with patch(
            "fim_one.core.channels.feishu.FeishuChannel.send_interactive_card",
            new=AsyncMock(
                return_value=ChannelSendResult(ok=False, error="chat not found")
            ),
        ):
            result = await hook.execute(ctx)
        assert result.allow is False
        assert "chat not found" in (result.error or "")


class TestInlineMode:
    """Cases specific to the inline (no-channel-card) delivery path."""

    @pytest.mark.asyncio
    async def test_inline_mode_no_channel_created(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """inline_only agent with zero channels → no card sent, mode='inline'."""
        # Seed: user + org + agent(inline_only).  Deliberately NO channels.
        async with session_factory() as db:
            user = User(
                id=str(uuid.uuid4()),
                username="alice",
                email="alice@test.com",
                is_admin=False,
            )
            db.add(user)
            org = Organization(
                id=str(uuid.uuid4()),
                name="InlineCo",
                slug=f"inlineco-{uuid.uuid4().hex[:6]}",
                owner_id=user.id,
            )
            db.add(org)
            await db.commit()
        await _seed_agent(
            session_factory,
            user_id=user.id,
            org_id=org.id,
            agent_id="agent-inline",
            confirmation_mode="inline_only",
        )

        hook = create_feishu_gate_hook(
            session_factory=session_factory,
            timeout_seconds=5,
            poll_interval_seconds=0.02,
        )
        ctx = _make_context(
            org_id=org.id,
            user_id=user.id,
            agent_id="agent-inline",
        )

        send_mock = AsyncMock(return_value=ChannelSendResult(ok=True))

        async def _approve_after_delay() -> None:
            for _ in range(200):
                await asyncio.sleep(0.02)
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

        with patch(
            "fim_one.core.channels.feishu.FeishuChannel.send_interactive_card",
            new=send_mock,
        ):
            approver = asyncio.create_task(_approve_after_delay())
            result = await hook.execute(ctx)
            await approver

        assert result.allow is True
        send_mock.assert_not_awaited()

        async with session_factory() as db:
            row = (await db.execute(select(ConfirmationRequest))).scalar_one()
            assert row.mode == "inline"
            assert row.channel_id is None
            assert row.status == "approved"
            # Approver scope defaults to "initiator" → user_id stamped.
            assert row.approver_user_id == user.id

    @pytest.mark.asyncio
    async def test_channel_only_fails_without_channel(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """channel_only + no channel → HookResult(allow=False)."""
        async with session_factory() as db:
            user = User(
                id=str(uuid.uuid4()),
                username="bob",
                email="bob@test.com",
                is_admin=False,
            )
            db.add(user)
            org = Organization(
                id=str(uuid.uuid4()),
                name="NoChannelCo",
                slug=f"nochan-{uuid.uuid4().hex[:6]}",
                owner_id=user.id,
            )
            db.add(org)
            await db.commit()
        await _seed_agent(
            session_factory,
            user_id=user.id,
            org_id=org.id,
            agent_id="agent-channel-only",
            confirmation_mode="channel_only",
        )

        hook = create_feishu_gate_hook(session_factory=session_factory)
        ctx = _make_context(
            org_id=org.id,
            user_id=user.id,
            agent_id="agent-channel-only",
        )
        result = await hook.execute(ctx)

        assert result.allow is False
        assert "channel_only" in (result.error or "").lower()
        # No row should have been created for a pre-flight failure.
        async with session_factory() as db:
            rows = (await db.execute(select(ConfirmationRequest))).all()
            assert rows == []

    @pytest.mark.asyncio
    async def test_require_all_flag_forces_confirmation(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """require_confirmation_for_all=True + action.requires=False → still gated."""
        async with session_factory() as db:
            user = User(
                id=str(uuid.uuid4()),
                username="carol",
                email="carol@test.com",
                is_admin=False,
            )
            db.add(user)
            org = Organization(
                id=str(uuid.uuid4()),
                name="StrictCo",
                slug=f"strict-{uuid.uuid4().hex[:6]}",
                owner_id=user.id,
            )
            db.add(org)
            await db.commit()
        await _seed_agent(
            session_factory,
            user_id=user.id,
            org_id=org.id,
            agent_id="agent-strict",
            confirmation_mode="inline_only",
            require_confirmation_for_all=True,
        )

        hook = create_feishu_gate_hook(
            session_factory=session_factory,
            timeout_seconds=5,
            poll_interval_seconds=0.02,
        )
        # requires=False on the context — the agent's require_all flag
        # should still force a gate.
        ctx = _make_context(
            org_id=org.id,
            user_id=user.id,
            agent_id="agent-strict",
            requires=False,
        )

        async def _approve_after_delay() -> None:
            for _ in range(200):
                await asyncio.sleep(0.02)
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

        approver = asyncio.create_task(_approve_after_delay())
        result = await hook.execute(ctx)
        await approver

        assert result.allow is True
        async with session_factory() as db:
            row = (await db.execute(select(ConfirmationRequest))).scalar_one()
            assert row.status == "approved"
            assert row.mode == "inline"

    @pytest.mark.asyncio
    async def test_inline_listener_fires(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Registered listener receives the freshly-committed row."""
        from fim_one.core.hooks import (
            set_inline_confirmation_listener,
        )

        async with session_factory() as db:
            user = User(
                id=str(uuid.uuid4()),
                username="dave",
                email="dave@test.com",
                is_admin=False,
            )
            db.add(user)
            await db.commit()
        await _seed_agent(
            session_factory,
            user_id=user.id,
            org_id=None,  # personal agent (no org) → inline naturally
            agent_id="agent-listener",
            confirmation_mode="inline_only",
        )

        captured: list[str] = []

        async def _listener(req: Any) -> None:
            captured.append(req.id)

        set_inline_confirmation_listener(_listener)
        try:
            hook = create_feishu_gate_hook(
                session_factory=session_factory,
                timeout_seconds=5,
                poll_interval_seconds=0.02,
            )
            ctx = _make_context(
                org_id=None,
                user_id=user.id,
                agent_id="agent-listener",
            )

            async def _approve_after_delay() -> None:
                for _ in range(200):
                    await asyncio.sleep(0.02)
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

            approver = asyncio.create_task(_approve_after_delay())
            result = await hook.execute(ctx)
            await approver
        finally:
            set_inline_confirmation_listener(None)

        assert result.allow is True
        assert len(captured) == 1


class TestAsHook:
    """as_hook() adapter should produce a Hook compatible with HookRegistry."""

    @pytest.mark.asyncio
    async def test_as_hook_integrates_with_registry(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        from fim_one.core.agent.hooks import HookRegistry

        hook = create_feishu_gate_hook(session_factory=session_factory)
        registry = HookRegistry()
        registry.register(hook.as_hook())

        assert len(registry) == 1
        listed = registry.list_hooks(HookPoint.PRE_TOOL_USE)
        assert listed[0].name == "feishu_gate"
        assert listed[0].priority == 10

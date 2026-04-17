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
    return {"user_id": user.id, "org_id": org.id, "channel_id": channel.id}


def _make_context(
    *, org_id: str, user_id: str, requires: bool = True
) -> HookContext:
    return HookContext(
        hook_point=HookPoint.PRE_TOOL_USE,
        tool_name="oa__purchase_pay",
        tool_args={"vendor": "X", "amount": 500},
        agent_id="agent-1",
        user_id=user_id,
        metadata={
            "requires_confirmation": requires,
            "org_id": org_id,
        },
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
            org_id=seed["org_id"], user_id=seed["user_id"], requires=False
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
            org_id=seed["org_id"], user_id=seed["user_id"]
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
        # DB row exists with status=approved.
        async with session_factory() as db:
            row = (
                await db.execute(select(ConfirmationRequest))
            ).scalar_one()
            assert row.status == "approved"
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
            org_id=seed["org_id"], user_id=seed["user_id"]
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
            org_id=seed["org_id"], user_id=seed["user_id"]
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
    async def test_no_channel_blocks(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        seed: dict[str, Any],
    ) -> None:
        async with session_factory() as db:
            row = (
                await db.execute(
                    select(Channel).where(Channel.id == seed["channel_id"])
                )
            ).scalar_one()
            row.is_active = False
            await db.commit()

        hook = create_feishu_gate_hook(session_factory=session_factory)
        ctx = _make_context(
            org_id=seed["org_id"], user_id=seed["user_id"]
        )
        result = await hook.execute(ctx)
        assert result.allow is False
        assert "no active Feishu channel" in (result.error or "")

    @pytest.mark.asyncio
    async def test_missing_org_id_blocks(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        hook = create_feishu_gate_hook(session_factory=session_factory)
        ctx = HookContext(
            hook_point=HookPoint.PRE_TOOL_USE,
            tool_name="x__y",
            metadata={"requires_confirmation": True},
        )
        result = await hook.execute(ctx)
        assert result.allow is False
        assert "org_id" in (result.error or "")

    @pytest.mark.asyncio
    async def test_send_failure_blocks(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        seed: dict[str, Any],
    ) -> None:
        hook = create_feishu_gate_hook(session_factory=session_factory)
        ctx = _make_context(
            org_id=seed["org_id"], user_id=seed["user_id"]
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

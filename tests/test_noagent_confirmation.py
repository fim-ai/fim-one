"""B9 / N2: in a chat with no agent, a flagged action is confirmed by the person chatting.

The gate used to step aside when the turn had no agent, so a connector
action marked "requires confirmation" ran at once, in exactly the chats
that auto-attach every connector the user can see. Now it asks inline, the
request is decided by the user chatting, and the card reaches the chat by
its conversation id.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.agent.hooks import HookContext, HookPoint
from fim_one.core.assembly.confirmation import ApprovalGateHook, ConfirmationService
from fim_one.core.assembly.principal import AuthMethod, resolve_principal
from fim_one.core.hooks import set_inline_confirmation_listener
from fim_one.db.base import Base
from fim_one.db.models.channel import ConfirmationRequest
from fim_one.db.models.user import User

pytestmark = pytest.mark.timeout(30)


@pytest.fixture()
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'noagent.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.fixture()
async def users(factory: async_sessionmaker[AsyncSession]) -> dict[str, str]:
    chatter, bystander = str(uuid.uuid4()), str(uuid.uuid4())
    async with factory() as db:
        db.add(User(id=chatter, username="chatter", email="c@x.io"))
        db.add(User(id=bystander, username="bystander", email="b@x.io"))
        await db.commit()
    return {"chatter": chatter, "bystander": bystander}


def _context(user_id: str, *, requires: bool = True) -> HookContext:
    return HookContext(
        hook_point=HookPoint.PRE_TOOL_USE,
        tool_name="connector",
        tool_args={"subcommand": "execute", "connector": "crm", "action": "wipe"},
        agent_id=None,
        user_id=user_id,
        metadata={"requires_confirmation": requires, "tool_call_id": "tc-1"},
    )


def _hook(factory: async_sessionmaker[AsyncSession], **kw: Any) -> ApprovalGateHook:
    return ApprovalGateHook(
        session_factory=factory, timeout_seconds=5, poll_interval_seconds=0.02, **kw
    )


async def _pending(factory: async_sessionmaker[AsyncSession]) -> ConfirmationRequest:
    for _ in range(200):
        async with factory() as db:
            row = (
                await db.execute(
                    select(ConfirmationRequest).where(ConfirmationRequest.status == "pending")
                )
            ).scalar_one_or_none()
        if row is not None:
            return row
        await asyncio.sleep(0.01)
    raise AssertionError("no request was created")


class TestNoAgentChat:
    async def test_a_flagged_action_waits_for_the_chatter(
        self, factory: async_sessionmaker[AsyncSession], users: dict[str, str]
    ) -> None:
        seen: list[Any] = []

        async def _listener(req: Any) -> None:
            seen.append(req)

        set_inline_confirmation_listener(_listener)
        try:
            hook = _hook(factory, conversation_id="conv-7")
            task = asyncio.create_task(hook.execute(_context(users["chatter"])))
            row = await _pending(factory)
        finally:
            set_inline_confirmation_listener(None)

        # Recorded for this chat, decided by the person chatting.
        assert row.agent_id is None and row.org_id is None
        assert row.mode == "inline" and row.channel_id is None
        assert row.user_id == users["chatter"]
        assert (row.eligible_scope, row.eligible_user_id) == ("initiator", users["chatter"])
        assert row.conversation_id == "conv-7"
        assert row.payload["conversation_id"] == "conv-7"
        assert row.payload["tool_name"] == "connector"
        assert row.tool_call_id == "tc-1"
        assert [r.id for r in seen] == [row.id]
        assert not task.done()

        service = ConfirmationService(factory)
        async with factory() as db:
            bystander = await resolve_principal(
                db, user_id=users["bystander"], auth=AuthMethod.SESSION
            )
            chatter = await resolve_principal(db, user_id=users["chatter"], auth=AuthMethod.SESSION)
            assert await service.may_decide(db, row, bystander) is False
            await service.decide(db, confirmation_id=row.id, actor=chatter, decision="approve")

        result = await task
        assert result.allow is True
        assert any("approved (inline" in s for s in result.side_effects)

    async def test_a_rejected_action_is_blocked(
        self, factory: async_sessionmaker[AsyncSession], users: dict[str, str]
    ) -> None:
        hook = _hook(factory, conversation_id="conv-7")
        task = asyncio.create_task(hook.execute(_context(users["chatter"])))
        row = await _pending(factory)
        async with factory() as db:
            chatter = await resolve_principal(db, user_id=users["chatter"], auth=AuthMethod.SESSION)
            await ConfirmationService(factory).decide(
                db, confirmation_id=row.id, actor=chatter, decision="reject"
            )
        result = await task
        assert result.allow is False
        assert "rejected" in (result.error or "").lower()

    async def test_nobody_answering_blocks_the_action(
        self, factory: async_sessionmaker[AsyncSession], users: dict[str, str]
    ) -> None:
        hook = ApprovalGateHook(
            session_factory=factory, timeout_seconds=0, poll_interval_seconds=0.01
        )
        result = await hook.execute(_context(users["chatter"]))
        assert result.allow is False
        assert "timed out" in (result.error or "")
        async with factory() as db:
            row = (await db.execute(select(ConfirmationRequest))).scalar_one()
        assert row.status == "expired"

    async def test_an_unflagged_action_passes_without_a_request(
        self, factory: async_sessionmaker[AsyncSession], users: dict[str, str]
    ) -> None:
        result = await _hook(factory).execute(_context(users["chatter"], requires=False))
        assert result.allow is True
        async with factory() as db:
            assert (await db.execute(select(ConfirmationRequest))).scalars().all() == []

    async def test_the_card_is_routed_to_the_conversation(
        self, factory: async_sessionmaker[AsyncSession], users: dict[str, str]
    ) -> None:
        """The web bridge queues the event under the chat's own scope."""
        from fim_one.web import confirmation_sse

        await confirmation_sse.clear_all_queues()
        confirmation_sse.register_confirmation_bridge()
        try:
            hook = _hook(factory, conversation_id="conv-9")
            task = asyncio.create_task(hook.execute(_context(users["chatter"])))
            row = await _pending(factory)
            queue = confirmation_sse.queue_for(
                confirmation_sse.scope_for(None, "conv-9"), users["chatter"]
            )
            event = await asyncio.wait_for(queue.get(), timeout=2)
        finally:
            confirmation_sse.unregister_confirmation_bridge()
            await confirmation_sse.clear_all_queues()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert event["type"] == "awaiting_confirmation"
        assert event["confirmation_id"] == row.id
        assert event["agent_id"] == ""
        assert event["mode"] == "inline"
        assert event["approver_scope"] == "initiator"


async def test_the_bootstrap_hands_the_conversation_to_the_gate(
    factory: async_sessionmaker[AsyncSession], users: dict[str, str]
) -> None:
    """chat.py builds the registry per turn; the conversation reaches the gate."""
    from types import SimpleNamespace

    from fim_one.web.hooks_bootstrap import build_hook_registry_for_agent

    registry = await build_hook_registry_for_agent(
        SimpleNamespace(model_config_json=None), factory, conversation_id="conv-11"
    )
    task = asyncio.create_task(registry.run_pre_tool(_context(users["chatter"])))
    row = await _pending(factory)
    assert row.conversation_id == "conv-11"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

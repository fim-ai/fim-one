"""The confirmation service: asking, delivering, waiting.

Who may decide, and deciding, are covered further down as the phase adds
them. External channels are faked; the database is a real SQLite file.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.assembly.confirmation import (
    ConfirmationService,
    ConfirmationSpec,
    confirmation_required,
)
from fim_one.core.assembly.errors import AssemblyRefusal
from fim_one.core.assembly.principal import Binding, provisional_principal
from fim_one.core.channels import ApprovalCarrier, ChannelSendResult, FeishuChannel
from fim_one.core.channels.resolve import resolve_org_channel
from fim_one.core.hooks import set_inline_confirmation_listener
from fim_one.db.base import Base
from fim_one.db.models.agent import Agent
from fim_one.db.models.channel import Channel, ConfirmationRequest
from fim_one.db.models.organization import Organization, OrgMembership
from fim_one.db.models.user import User

pytestmark = pytest.mark.timeout(60)


@pytest.fixture(autouse=True)
def _stable_cred_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import fim_one.core.security.encryption as enc

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "unit-test-key-c1p3")
    enc._fernet_cache = None  # type: ignore[attr-defined]


@pytest.fixture()
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'svc.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _create_session() -> AsyncIterator[AsyncSession]:
        async with maker() as s:
            yield s

    with patch("fim_one.db.create_session", _create_session):
        yield maker
    await engine.dispose()


@pytest.fixture()
async def world(factory: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    """An owner with an organization and a Feishu channel, a member, an outsider."""
    owner, member, outsider = (str(uuid.uuid4()) for _ in range(3))
    org_id, other_org = str(uuid.uuid4()), str(uuid.uuid4())
    async with factory() as db:
        for uid, name in ((owner, "owner"), (member, "member"), (outsider, "outsider")):
            db.add(User(id=uid, username=name, email=f"{name}@x.io"))
        db.add(Organization(id=org_id, name="Org", slug=f"org-{org_id[:6]}", owner_id=owner))
        db.add(
            Organization(id=other_org, name="Other", slug=f"o-{other_org[:6]}", owner_id=outsider)
        )
        await db.flush()
        for uid in (owner, member):
            db.add(OrgMembership(id=str(uuid.uuid4()), org_id=org_id, user_id=uid, role="member"))
        db.add(
            OrgMembership(id=str(uuid.uuid4()), org_id=other_org, user_id=outsider, role="owner")
        )
        channel = Channel(
            id=str(uuid.uuid4()),
            name="审批群",
            type="feishu",
            org_id=org_id,
            created_by=owner,
            config={"app_id": "cli", "app_secret": "s", "chat_id": "oc_group"},
        )
        db.add(channel)
        foreign = Channel(
            id=str(uuid.uuid4()),
            name="elsewhere",
            type="feishu",
            org_id=other_org,
            created_by=outsider,
            config={"app_id": "cli", "app_secret": "s", "chat_id": "oc_other"},
        )
        db.add(foreign)
        db.add(Agent(id="agent-1", user_id=owner, org_id=org_id, name="A"))
        await db.commit()
    return {
        "owner": owner,
        "member": member,
        "outsider": outsider,
        "org": org_id,
        "other_org": other_org,
        "channel": channel.id,
        "foreign_channel": foreign.id,
    }


def _spec(world: dict[str, Any], **overrides: Any) -> ConfirmationSpec:
    base: dict[str, Any] = dict(
        kind="confirmation",
        initiator=provisional_principal(world["member"]),
        binding=Binding(kind="agent", id="agent-1", owner_id=world["owner"], org_id=world["org"]),
        org_id=world["org"],
        title="Approval Required",
        summary=None,
        payload={"tool_name": "oa__pay", "tool_args": {"amount": 5}},
        eligible_scope="initiator",
        eligible_user_id=world["member"],
        delivery="auto",
        channel_id=None,
        timeout_seconds=5,
    )
    base.update(overrides)
    base["description"] = base.pop("summary")
    return ConfirmationSpec(**base)


async def _row(factory: async_sessionmaker[AsyncSession], row_id: str) -> ConfirmationRequest:
    async with factory() as db:
        return (
            await db.execute(select(ConfirmationRequest).where(ConfirmationRequest.id == row_id))
        ).scalar_one()


# ---------------------------------------------------------------------------
# Asking and delivering
# ---------------------------------------------------------------------------


def test_confirmation_required_is_either_flag() -> None:
    assert confirmation_required(tool_flag=True, agent_require_all=False)
    assert confirmation_required(tool_flag=False, agent_require_all=True)
    assert not confirmation_required(tool_flag=False, agent_require_all=False)


class TestRequest:
    async def test_inline_when_no_channel_is_bound(
        self, factory: async_sessionmaker[AsyncSession], world: dict[str, Any]
    ) -> None:
        seen: list[Any] = []

        async def _listener(req: Any) -> None:
            seen.append(req)

        set_inline_confirmation_listener(_listener)
        try:
            row = await ConfirmationService(factory).request(_spec(world))
        finally:
            set_inline_confirmation_listener(None)

        assert row.mode == "inline" and row.channel_id is None and row.status == "pending"
        assert row.kind == "confirmation"
        assert row.agent_id == "agent-1"
        assert row.user_id == world["member"]
        assert row.org_id == world["org"]
        assert row.payload["tool_name"] == "oa__pay"
        assert row.payload["mode"] == "inline" and row.payload["approver_scope"] == "initiator"
        assert row.title == "Approval Required"
        expires = (
            row.expires_at.replace(tzinfo=UTC) if row.expires_at.tzinfo is None else row.expires_at
        )
        assert timedelta(seconds=3) < expires - datetime.now(UTC) <= timedelta(seconds=5)
        assert [r.id for r in seen] == [row.id]

    async def test_channel_when_one_is_bound(
        self, factory: async_sessionmaker[AsyncSession], world: dict[str, Any]
    ) -> None:
        send = AsyncMock(return_value=ChannelSendResult(ok=True))
        with patch.object(FeishuChannel, "send_interactive_card", send):
            row = await ConfirmationService(factory).request(
                _spec(world, channel_id=world["channel"], summary="Please look")
            )

        assert row.mode == "channel" and row.channel_id == world["channel"]
        assert row.payload["channel_label"] == "审批群"
        send.assert_awaited_once()
        assert send.await_args is not None
        chat_id, card = send.await_args.args
        assert chat_id == "oc_group"
        text = str(card)
        assert row.id in text and "Please look" in text and "oa__pay" in text
        assert "the user who started this run" in text

    async def test_inline_only_ignores_the_bound_channel(
        self, factory: async_sessionmaker[AsyncSession], world: dict[str, Any]
    ) -> None:
        send = AsyncMock(return_value=ChannelSendResult(ok=True))
        with patch.object(FeishuChannel, "send_interactive_card", send):
            row = await ConfirmationService(factory).request(
                _spec(world, channel_id=world["channel"], delivery="inline_only")
            )
        assert row.mode == "inline"
        send.assert_not_awaited()

    async def test_channel_only_without_a_channel_is_refused(
        self, factory: async_sessionmaker[AsyncSession], world: dict[str, Any]
    ) -> None:
        with pytest.raises(AssemblyRefusal) as exc:
            await ConfirmationService(factory).request(_spec(world, delivery="channel_only"))
        assert exc.value.error_code == "confirmation_undeliverable"
        assert "channel_only" in exc.value.detail
        async with factory() as db:
            assert (await db.execute(select(ConfirmationRequest))).scalars().all() == []

    async def test_a_channel_outside_the_binding_organization_is_not_used(
        self, factory: async_sessionmaker[AsyncSession], world: dict[str, Any]
    ) -> None:
        send = AsyncMock(return_value=ChannelSendResult(ok=True))
        with patch.object(FeishuChannel, "send_interactive_card", send):
            row = await ConfirmationService(factory).request(
                _spec(world, channel_id=world["foreign_channel"])
            )
        assert row.mode == "inline"
        send.assert_not_awaited()

    async def test_a_failed_send_is_a_refusal_naming_the_row(
        self, factory: async_sessionmaker[AsyncSession], world: dict[str, Any]
    ) -> None:
        send = AsyncMock(return_value=ChannelSendResult(ok=False, error="chat not found"))
        with (
            patch.object(FeishuChannel, "send_interactive_card", send),
            pytest.raises(AssemblyRefusal) as exc,
        ):
            await ConfirmationService(factory).request(_spec(world, channel_id=world["channel"]))
        assert exc.value.error_code == "confirmation_undeliverable"
        assert "chat not found" in exc.value.detail
        assert exc.value.detail_args is not None
        row = await _row(factory, exc.value.detail_args["confirmation_id"])
        assert row.mode == "channel"

    async def test_a_channel_with_no_target_chat_is_refused(
        self, factory: async_sessionmaker[AsyncSession], world: dict[str, Any]
    ) -> None:
        async with factory() as db:
            ch = await db.get(Channel, world["channel"])
            assert ch is not None
            ch.config = {"app_id": "cli", "app_secret": "s"}
            await db.commit()
        with pytest.raises(AssemblyRefusal) as exc:
            await ConfirmationService(factory).request(
                _spec(world, channel_id=world["channel"], delivery="channel_only")
            )
        assert "chat_id" in exc.value.detail

    async def test_a_channel_that_cannot_carry_approvals_is_treated_as_unbound(
        self, factory: async_sessionmaker[AsyncSession], world: dict[str, Any]
    ) -> None:
        class _Plain:
            def __init__(self, _config: dict[str, Any]) -> None:
                pass

        with patch("fim_one.core.assembly.confirmation.build_channel", lambda *_a: _Plain({})):
            row = await ConfirmationService(factory).request(
                _spec(world, channel_id=world["channel"])
            )
            assert row.mode == "inline"
            with pytest.raises(AssemblyRefusal):
                await ConfirmationService(factory).request(
                    _spec(world, channel_id=world["channel"], delivery="channel_only")
                )

    async def test_no_organization_means_null_not_empty(
        self, factory: async_sessionmaker[AsyncSession], world: dict[str, Any]
    ) -> None:
        row = await ConfirmationService(factory).request(
            _spec(world, org_id="", binding=None, agent_id="agent-1")
        )
        assert row.org_id is None
        assert row.agent_id == "agent-1"


# ---------------------------------------------------------------------------
# Waiting
# ---------------------------------------------------------------------------


class TestWait:
    async def _pending(
        self, factory: async_sessionmaker[AsyncSession], world: dict[str, Any], **kw: Any
    ) -> ConfirmationRequest:
        row: ConfirmationRequest = await ConfirmationService(factory).request(_spec(world, **kw))
        return row

    @pytest.mark.parametrize(
        ("status", "outcome"),
        [("approved", "approve"), ("rejected", "reject"), ("expired", "expired")],
    )
    async def test_returns_the_decision(
        self,
        factory: async_sessionmaker[AsyncSession],
        world: dict[str, Any],
        status: str,
        outcome: str,
    ) -> None:
        row = await self._pending(factory, world)
        service = ConfirmationService(factory)
        task = asyncio.create_task(service.wait(row.id, poll_seconds=0.02))
        await asyncio.sleep(0.05)
        async with factory() as db:
            stored = await db.get(ConfirmationRequest, row.id)
            assert stored is not None
            stored.status = status
            await db.commit()
        assert await task == outcome

    async def test_marks_the_row_expired_at_its_deadline(
        self, factory: async_sessionmaker[AsyncSession], world: dict[str, Any]
    ) -> None:
        row = await self._pending(factory, world, timeout_seconds=0)
        assert await ConfirmationService(factory).wait(row.id, poll_seconds=0.01) == "expired"
        stored = await _row(factory, row.id)
        assert stored.status == "expired" and stored.responded_at is not None

    async def test_a_deleted_row_counts_as_expired(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        assert await ConfirmationService(factory).wait("missing", poll_seconds=0.01) == "expired"


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


class TestResolveOrgChannel:
    async def test_the_agents_organization_wins(
        self, factory: async_sessionmaker[AsyncSession], world: dict[str, Any]
    ) -> None:
        async with factory() as db:
            own = await resolve_org_channel(
                db, channel_id=world["channel"], org_id=world["org"], owner_id=world["owner"]
            )
            foreign = await resolve_org_channel(
                db,
                channel_id=world["foreign_channel"],
                org_id=world["org"],
                owner_id=world["owner"],
            )
        assert own is not None and own.id == world["channel"]
        assert foreign is None

    async def test_without_an_organization_the_owners_memberships_decide(
        self, factory: async_sessionmaker[AsyncSession], world: dict[str, Any]
    ) -> None:
        async with factory() as db:
            own = await resolve_org_channel(
                db, channel_id=world["channel"], org_id=None, owner_id=world["member"]
            )
            foreign = await resolve_org_channel(
                db, channel_id=world["foreign_channel"], org_id=None, owner_id=world["member"]
            )
            nobody = await resolve_org_channel(
                db, channel_id=world["channel"], org_id=None, owner_id=None
            )
        assert own is not None and foreign is None and nobody is None

    async def test_a_disabled_channel_is_not_resolved(
        self, factory: async_sessionmaker[AsyncSession], world: dict[str, Any]
    ) -> None:
        async with factory() as db:
            ch = await db.get(Channel, world["channel"])
            assert ch is not None
            ch.is_active = False
            await db.commit()
            assert (
                await resolve_org_channel(
                    db, channel_id=world["channel"], org_id=world["org"], owner_id=None
                )
                is None
            )


class TestFeishuCarrier:
    def test_feishu_is_an_approval_carrier(self) -> None:
        channel = FeishuChannel({"app_id": "a", "app_secret": "s", "chat_id": "oc_1"})
        assert isinstance(channel, ApprovalCarrier)
        assert channel.approval_target() == "oc_1"
        assert FeishuChannel({"app_id": "a", "app_secret": "s"}).approval_target() is None

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            (
                {
                    "action": {"value": {"confirmation_id": "c1", "decision": "approve"}},
                    "open_id": "ou_1",
                },
                ("c1", "approve", "ou_1"),
            ),
            (
                {
                    "event": {
                        "action": {"value": {"confirmation_id": "c2", "action": "deny"}},
                        "operator": {"open_id": "ou_2"},
                    }
                },
                ("c2", "reject", "ou_2"),
            ),
            (
                {"action": {"value": {"confirmation_id": "c3", "decision": "maybe"}}},
                ("c3", None, None),
            ),
        ],
    )
    def test_parse_decision(self, body: dict[str, Any], expected: tuple[Any, ...]) -> None:
        channel = FeishuChannel({"app_id": "a", "app_secret": "s"})
        decision = channel.parse_decision(body)
        assert decision is not None
        assert (decision.confirmation_id, decision.decision, decision.actor) == expected

    def test_parse_decision_is_none_for_other_bodies(self) -> None:
        channel = FeishuChannel({"app_id": "a", "app_secret": "s"})
        assert channel.parse_decision({"type": "url_verification", "challenge": "x"}) is None
        assert channel.parse_decision({}) is None

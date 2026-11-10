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
        # In the words of the agent editor, not the stored value.
        assert '"Channel only"' in exc.value.detail
        assert "approval channel" in exc.value.detail
        assert "channel_only" not in exc.value.detail
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


# ---------------------------------------------------------------------------
# Who may decide: one table, three entries
# ---------------------------------------------------------------------------

# (scope, actor) -> may decide. ``initiator`` is the member who asked;
# ``owner`` owns the agent; ``admin`` is a platform administrator outside
# the organization.
DECIDERS = ("initiator", "owner", "member2", "outsider", "admin")
MAY_DECIDE: dict[tuple[str, str], bool] = {
    ("initiator", "initiator"): True,
    ("initiator", "owner"): False,
    ("initiator", "member2"): False,
    ("initiator", "outsider"): False,
    ("initiator", "admin"): True,
    ("agent_owner", "initiator"): False,
    ("agent_owner", "owner"): True,
    ("agent_owner", "member2"): False,
    ("agent_owner", "outsider"): False,
    ("agent_owner", "admin"): True,
    ("org_members", "initiator"): True,
    ("org_members", "owner"): True,
    ("org_members", "member2"): True,
    ("org_members", "outsider"): False,
    ("org_members", "admin"): True,
    ("assignee", "initiator"): False,
    ("assignee", "owner"): False,
    ("assignee", "member2"): True,  # the assignee
    ("assignee", "outsider"): False,
    ("assignee", "admin"): True,
}
SIGNING_KEY = "signing-key"


@pytest.fixture()
async def deciders(
    factory: async_sessionmaker[AsyncSession], world: dict[str, Any]
) -> dict[str, Any]:
    """A second member, an administrator, Feishu bindings and a signed channel."""
    member2, admin = str(uuid.uuid4()), str(uuid.uuid4())
    async with factory() as db:
        db.add(User(id=member2, username="member2", email="m2@x.io"))
        db.add(User(id=admin, username="admin", email="admin@x.io", is_admin=True))
        await db.flush()
        db.add(
            OrgMembership(id=str(uuid.uuid4()), org_id=world["org"], user_id=member2, role="member")
        )
        from fim_one.db.models.oauth_binding import UserOAuthBinding

        users = {
            "initiator": world["member"],
            "owner": world["owner"],
            "member2": member2,
            "outsider": world["outsider"],
            "admin": admin,
        }
        for name, uid in users.items():
            db.add(UserOAuthBinding(user_id=uid, provider="feishu", oauth_id=f"ou_{name}"))
        signed = Channel(
            id=str(uuid.uuid4()),
            name="signed",
            type="feishu",
            org_id=world["org"],
            created_by=world["owner"],
            config={
                "app_id": "cli",
                "app_secret": "s",
                "chat_id": "oc_signed",
                "encrypt_key": SIGNING_KEY,
            },
        )
        db.add(signed)
        await db.commit()
    return {**world, "users": users, "signed_channel": signed.id}


def _eligible_user(scope: str, d: dict[str, Any]) -> str | None:
    return {
        "initiator": d["users"]["initiator"],
        "agent_owner": d["users"]["owner"],
        "assignee": d["users"]["member2"],
    }.get(scope)


async def _request_with_scope(
    factory: async_sessionmaker[AsyncSession], d: dict[str, Any], scope: str, **kw: Any
) -> Any:
    spec = _spec(
        d,
        initiator=provisional_principal(d["users"]["initiator"]),
        eligible_scope=scope,
        eligible_user_id=_eligible_user(scope, d),
        **kw,
    )
    return await ConfirmationService(factory).request(spec)


async def _principal(factory: async_sessionmaker[AsyncSession], user_id: str) -> Any:
    from fim_one.core.assembly.principal import AuthMethod, resolve_principal

    async with factory() as db:
        return await resolve_principal(db, user_id=user_id, auth=AuthMethod.SESSION)


@pytest.fixture()
async def api(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[Any]:
    """Both routers that decide confirmations, on the test engine."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from fim_one.db import get_session
    from fim_one.web.api.channels import router as channels_router
    from fim_one.web.api.confirmations import router as confirmations_router

    app = FastAPI()
    app.include_router(confirmations_router)
    app.include_router(channels_router)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


def _bearer(user_id: str) -> dict[str, str]:
    from fim_one.web.auth import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id, f'{user_id}@x.io')}"}


def _signed(payload: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    import hashlib
    import json

    body = json.dumps(payload).encode()
    ts, nonce = "1700000000", "n0nce"
    digest = hashlib.sha256()
    for part in (ts.encode(), nonce.encode(), SIGNING_KEY.encode(), body):
        digest.update(part)
    return body, {
        "X-Lark-Request-Timestamp": ts,
        "X-Lark-Request-Nonce": nonce,
        "X-Lark-Signature": digest.hexdigest(),
        "Content-Type": "application/json",
    }


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-c1p3-long-enough-for-hs256")


class TestMayDecideTable:
    @pytest.mark.parametrize(("scope", "actor"), sorted(MAY_DECIDE), ids=lambda v: str(v))
    async def test_the_rule_itself(
        self,
        factory: async_sessionmaker[AsyncSession],
        deciders: dict[str, Any],
        scope: str,
        actor: str,
    ) -> None:
        row = await _request_with_scope(factory, deciders, scope)
        principal = await _principal(factory, deciders["users"][actor])
        async with factory() as db:
            assert (
                await ConfirmationService(factory).may_decide(db, row, principal)
                is MAY_DECIDE[(scope, actor)]
            )

    @pytest.mark.parametrize(("scope", "actor"), sorted(MAY_DECIDE), ids=lambda v: str(v))
    async def test_through_the_portal(
        self,
        factory: async_sessionmaker[AsyncSession],
        deciders: dict[str, Any],
        api: Any,
        scope: str,
        actor: str,
    ) -> None:
        row = await _request_with_scope(factory, deciders, scope)
        headers = _bearer(deciders["users"][actor])

        seen = await api.get(f"/api/confirmations/{row.id}", headers=headers)
        resp = await api.post(
            f"/api/confirmations/{row.id}/respond", json={"decision": "approve"}, headers=headers
        )

        allowed = MAY_DECIDE[(scope, actor)]
        assert seen.status_code == (200 if allowed else 403), seen.text
        assert resp.status_code == (200 if allowed else 403), resp.text
        stored = await _row(factory, row.id)
        if allowed:
            assert stored.status == "approved"
            assert stored.decided_by_user_id == deciders["users"][actor]
            assert stored.approver_user_id == deciders["users"][actor]
        else:
            # (The bare router app renders HTTPException without error_code.)
            assert "not authorised" in resp.json()["detail"]
            assert stored.status == "pending" and stored.decided_by_user_id is None

    @pytest.mark.parametrize(("scope", "actor"), sorted(MAY_DECIDE), ids=lambda v: str(v))
    async def test_through_the_channel_callback(
        self,
        factory: async_sessionmaker[AsyncSession],
        deciders: dict[str, Any],
        api: Any,
        scope: str,
        actor: str,
    ) -> None:
        send = AsyncMock(return_value=ChannelSendResult(ok=True))
        with patch.object(FeishuChannel, "send_interactive_card", send):
            row = await _request_with_scope(
                factory, deciders, scope, channel_id=deciders["signed_channel"]
            )
        assert row.mode == "channel"

        body, headers = _signed(
            {
                "action": {"value": {"confirmation_id": row.id, "decision": "reject"}},
                "open_id": f"ou_{actor}",
            }
        )
        resp = await api.post(
            f"/api/channels/{deciders['signed_channel']}/callback", content=body, headers=headers
        )

        assert resp.status_code == 200, resp.text
        stored = await _row(factory, row.id)
        if MAY_DECIDE[(scope, actor)]:
            assert stored.status == "rejected"
            assert stored.decided_by_user_id == deciders["users"][actor]
            assert stored.responded_by_open_id == f"ou_{actor}"
            assert "card" in resp.json()
        else:
            assert stored.status == "pending"
            assert resp.json()["toast"]["type"] == "error"
            assert "card" not in resp.json()


class TestDecide:
    async def test_the_row_not_the_agents_current_setting_decides(
        self, factory: async_sessionmaker[AsyncSession], deciders: dict[str, Any]
    ) -> None:
        """B11: changing the agent after the request does not move it."""
        row = await _request_with_scope(factory, deciders, "initiator")
        async with factory() as db:
            agent = await db.get(Agent, "agent-1")
            assert agent is not None
            agent.confirmation_approver_scope = "agent_owner"
            await db.commit()

        service = ConfirmationService(factory)
        initiator = await _principal(factory, deciders["users"]["initiator"])
        owner = await _principal(factory, deciders["users"]["owner"])
        async with factory() as db:
            assert await service.may_decide(db, row, owner) is False
            decided = await service.decide(
                db, confirmation_id=row.id, actor=initiator, decision="approve", note="go"
            )
        assert decided.status == "approved"
        assert decided.decision_note == "go"
        assert decided.decided_by_user_id == initiator.user_id

    async def test_a_row_without_a_scope_follows_the_agents_setting(
        self, factory: async_sessionmaker[AsyncSession], deciders: dict[str, Any]
    ) -> None:
        """Rows written before the scope was recorded: the earlier rule."""
        row = await _request_with_scope(factory, deciders, "initiator")
        async with factory() as db:
            stored = await db.get(ConfirmationRequest, row.id)
            assert stored is not None
            stored.eligible_scope = None
            stored.eligible_user_id = None
            agent = await db.get(Agent, "agent-1")
            assert agent is not None
            agent.confirmation_approver_scope = "agent_owner"
            await db.commit()

        service = ConfirmationService(factory)
        initiator = await _principal(factory, deciders["users"]["initiator"])
        owner = await _principal(factory, deciders["users"]["owner"])
        async with factory() as db:
            legacy = await db.get(ConfirmationRequest, row.id)
            assert await service.may_decide(db, legacy, initiator) is False
            assert await service.may_decide(db, legacy, owner) is True

    async def test_second_decision_loses(
        self, factory: async_sessionmaker[AsyncSession], deciders: dict[str, Any]
    ) -> None:
        row = await _request_with_scope(factory, deciders, "org_members")
        service = ConfirmationService(factory)
        first = await _principal(factory, deciders["users"]["initiator"])
        second = await _principal(factory, deciders["users"]["member2"])
        async with factory() as db:
            await service.decide(db, confirmation_id=row.id, actor=first, decision="approve")
            with pytest.raises(AssemblyRefusal) as exc:
                await service.decide(db, confirmation_id=row.id, actor=second, decision="reject")
        assert exc.value.error_code == "confirmation_already_decided"
        assert exc.value.detail_args == {"status": "approved"}

    async def test_on_decided_is_told_once_about_each_decision(
        self, factory: async_sessionmaker[AsyncSession], deciders: dict[str, Any]
    ) -> None:
        """The hook a suspended workflow run is continued from (T10 P5)."""
        row = await _request_with_scope(factory, deciders, "org_members")
        service = ConfirmationService(factory)
        first = await _principal(factory, deciders["users"]["initiator"])
        second = await _principal(factory, deciders["users"]["member2"])
        told: list[tuple[str, str]] = []

        async def remember(decided: Any) -> None:
            told.append((decided.id, decided.status))

        def broken(_decided: Any) -> None:
            raise RuntimeError("a listener's failure is not the decider's")

        removers = [
            ConfirmationService.on_decided(broken),
            ConfirmationService.on_decided(remember),
        ]
        try:
            async with factory() as db:
                await service.decide(db, confirmation_id=row.id, actor=first, decision="reject")
                with pytest.raises(AssemblyRefusal):
                    await service.decide(
                        db, confirmation_id=row.id, actor=second, decision="approve"
                    )
            assert told == [(row.id, "rejected")]
        finally:
            for remove in removers:
                remove()

        other = await _request_with_scope(factory, deciders, "org_members")
        async with factory() as db:
            await service.decide(db, confirmation_id=other.id, actor=first, decision="approve")
        assert told == [(row.id, "rejected")]  # removed: no longer told

    async def test_a_request_past_its_deadline_cannot_be_decided(
        self, factory: async_sessionmaker[AsyncSession], deciders: dict[str, Any]
    ) -> None:
        """Before the sweep has run, a late click still finds the request closed."""
        row = await _request_with_scope(factory, deciders, "initiator")
        async with factory() as db:
            stored = await db.get(ConfirmationRequest, row.id)
            assert stored is not None
            stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()

        service = ConfirmationService(factory)
        initiator = await _principal(factory, deciders["users"]["initiator"])
        async with factory() as db:
            with pytest.raises(AssemblyRefusal) as exc:
                await service.decide(
                    db, confirmation_id=row.id, actor=initiator, decision="approve"
                )
        assert exc.value.error_code == "confirmation_already_decided"
        assert exc.value.detail_args == {"status": "expired"}
        after = await _row(factory, row.id)
        assert after.status == "expired"
        assert after.decided_by_user_id is None

    async def test_a_request_with_no_deadline_can_still_be_decided(
        self, factory: async_sessionmaker[AsyncSession], deciders: dict[str, Any]
    ) -> None:
        """Rows written before the deadline column existed have none."""
        row = await _request_with_scope(factory, deciders, "initiator")
        async with factory() as db:
            stored = await db.get(ConfirmationRequest, row.id)
            assert stored is not None
            stored.expires_at = None
            await db.commit()
        initiator = await _principal(factory, deciders["users"]["initiator"])
        async with factory() as db:
            decided = await ConfirmationService(factory).decide(
                db, confirmation_id=row.id, actor=initiator, decision="approve"
            )
        assert decided.status == "approved"

    async def test_unknown_and_question_rows(
        self, factory: async_sessionmaker[AsyncSession], deciders: dict[str, Any]
    ) -> None:
        service = ConfirmationService(factory)
        actor = await _principal(factory, deciders["users"]["admin"])
        question = await service.request(
            _spec(deciders, kind="user_question", payload={"questions": []})
        )
        async with factory() as db:
            for missing in ("nope", question.id):
                with pytest.raises(AssemblyRefusal) as exc:
                    await service.decide(
                        db, confirmation_id=missing, actor=actor, decision="approve"
                    )
                assert exc.value.error_code == "confirmation_not_found"

    async def test_pending_for_lists_what_the_actor_may_decide(
        self, factory: async_sessionmaker[AsyncSession], deciders: dict[str, Any]
    ) -> None:
        service = ConfirmationService(factory)
        mine = await _request_with_scope(factory, deciders, "initiator")
        org = await _request_with_scope(factory, deciders, "org_members")
        assigned = await _request_with_scope(factory, deciders, "assignee")
        decided = await _request_with_scope(factory, deciders, "initiator")
        initiator = await _principal(factory, deciders["users"]["initiator"])
        member2 = await _principal(factory, deciders["users"]["member2"])
        admin = await _principal(factory, deciders["users"]["admin"])
        async with factory() as db:
            await service.decide(
                db, confirmation_id=decided.id, actor=initiator, decision="approve"
            )
            assert {r.id for r in await service.pending_for(db, initiator)} == {mine.id, org.id}
            assert {r.id for r in await service.pending_for(db, member2)} == {org.id, assigned.id}
            # An administrator may decide anything, but is not shown everything.
            assert await service.pending_for(db, admin) == []
            assert await service.pending_for(db, initiator, kind="workflow_approval") == []


class TestCallbackChannelBinding:
    """B12: a channel callback decides only requests that channel carried."""

    async def _foreign_signed_channel(
        self, factory: async_sessionmaker[AsyncSession], d: dict[str, Any]
    ) -> str:
        """A signed channel of another organization, with the same signing key."""
        channel = Channel(
            id=str(uuid.uuid4()),
            name="rival",
            type="feishu",
            org_id=d["other_org"],
            created_by=d["outsider"],
            config={
                "app_id": "cli2",
                "app_secret": "s2",
                "chat_id": "oc_rival",
                "encrypt_key": SIGNING_KEY,
            },
        )
        async with factory() as db:
            db.add(channel)
            await db.commit()
        return channel.id

    async def test_another_channels_callback_cannot_decide_it(
        self, factory: async_sessionmaker[AsyncSession], deciders: dict[str, Any], api: Any
    ) -> None:
        send = AsyncMock(return_value=ChannelSendResult(ok=True))
        with patch.object(FeishuChannel, "send_interactive_card", send):
            row = await _request_with_scope(
                factory, deciders, "initiator", channel_id=deciders["signed_channel"]
            )
        rival = await self._foreign_signed_channel(factory, deciders)

        # The rival's administrator knows the request id and the initiator's
        # open_id, and signs a click with their own channel's key.
        body, headers = _signed(
            {
                "action": {"value": {"confirmation_id": row.id, "decision": "approve"}},
                "open_id": "ou_initiator",
            }
        )
        resp = await api.post(f"/api/channels/{rival}/callback", content=body, headers=headers)

        assert resp.status_code == 200, resp.text
        assert resp.json()["toast"]["type"] == "error"
        assert "card" not in resp.json()
        assert (await _row(factory, row.id)).status == "pending"

        # The channel that carried it still can.
        resp = await api.post(
            f"/api/channels/{deciders['signed_channel']}/callback", content=body, headers=headers
        )
        assert "card" in resp.json()
        assert (await _row(factory, row.id)).status == "approved"

    async def test_an_inline_request_is_not_decidable_from_any_channel(
        self, factory: async_sessionmaker[AsyncSession], deciders: dict[str, Any], api: Any
    ) -> None:
        row = await _request_with_scope(factory, deciders, "initiator")
        assert row.channel_id is None
        body, headers = _signed(
            {
                "action": {"value": {"confirmation_id": row.id, "decision": "approve"}},
                "open_id": "ou_initiator",
            }
        )
        resp = await api.post(
            f"/api/channels/{deciders['signed_channel']}/callback", content=body, headers=headers
        )
        assert resp.json()["toast"]["type"] == "error"
        assert (await _row(factory, row.id)).status == "pending"

    async def test_the_service_refuses_it_as_not_found(
        self, factory: async_sessionmaker[AsyncSession], deciders: dict[str, Any]
    ) -> None:
        row = await _request_with_scope(factory, deciders, "initiator")
        actor = await _principal(factory, deciders["users"]["initiator"])
        async with factory() as db:
            with pytest.raises(AssemblyRefusal) as exc:
                await ConfirmationService(factory).decide(
                    db,
                    confirmation_id=row.id,
                    actor=actor,
                    decision="approve",
                    via_channel_id=deciders["signed_channel"],
                )
        assert exc.value.error_code == "confirmation_not_found"

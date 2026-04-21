"""Tests for the inline-confirmation respond endpoint and SSE bridge.

Covers:

* Scope enforcement (initiator / agent_owner / org_members).
* 404 / 409 terminal responses.
* Feishu callback path stamps ``approver_user_id=None`` but still
  updates ``responded_at`` via the shared helper.
* SSE listener bridge pushes the frozen-contract dict onto the right
  (agent_id, user_id) queue.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.web.models  # noqa: F401
from fim_one.db.base import Base
from fim_one.db import get_session
from fim_one.web.api.confirmations import router as confirmations_router
from fim_one.web.auth import create_access_token
from fim_one.web.models.agent import Agent
from fim_one.web.models.channel import ConfirmationRequest
from fim_one.web.models.organization import Organization, OrgMembership
from fim_one.web.models.user import User


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stable_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    import fim_one.core.security.encryption as enc

    monkeypatch.setenv(
        "CREDENTIAL_ENCRYPTION_KEY", "test-cnf-respond-key-0123456789ab"
    )
    enc._CREDENTIAL_KEY_RAW = "test-cnf-respond-key-0123456789ab"
    enc._cred_fernet_instance = None
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-respond-01")


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
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(confirmations_router)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _mk_user(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    username: str,
    is_admin: bool = False,
) -> User:
    async with session_factory() as db:
        user = User(
            id=str(uuid.uuid4()),
            username=username,
            email=f"{username}@test.com",
            is_admin=is_admin,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


async def _mk_org(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    owner: User,
    extra_members: list[User] | None = None,
) -> Organization:
    async with session_factory() as db:
        org = Organization(
            id=str(uuid.uuid4()),
            name="Acme",
            slug=f"acme-{uuid.uuid4().hex[:6]}",
            owner_id=owner.id,
        )
        db.add(org)
        db.add(
            OrgMembership(
                id=str(uuid.uuid4()),
                org_id=org.id,
                user_id=owner.id,
                role="owner",
            )
        )
        for m in extra_members or []:
            db.add(
                OrgMembership(
                    id=str(uuid.uuid4()),
                    org_id=org.id,
                    user_id=m.id,
                    role="member",
                )
            )
        await db.commit()
        await db.refresh(org)
        return org


async def _mk_agent(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    owner: User,
    org: Organization | None,
    scope: str,
) -> Agent:
    async with session_factory() as db:
        ag = Agent(
            id=str(uuid.uuid4()),
            user_id=owner.id,
            org_id=org.id if org else None,
            name="Test Agent",
            confirmation_approver_scope=scope,
            confirmation_mode="inline_only",
        )
        db.add(ag)
        await db.commit()
        await db.refresh(ag)
        return ag


async def _mk_request(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    agent: Agent,
    initiator: User,
    status_: str = "pending",
    mode: str = "inline",
) -> ConfirmationRequest:
    async with session_factory() as db:
        req = ConfirmationRequest(
            id=str(uuid.uuid4()),
            agent_id=agent.id,
            user_id=initiator.id,
            org_id=agent.org_id or "",
            mode=mode,
            status=status_,
            channel_id=None,
            payload={"tool_name": "jira.createIssue", "tool_args": {"x": 1}},
        )
        db.add(req)
        await db.commit()
        await db.refresh(req)
        return req


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(user.id, user.email or "")
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Respond endpoint — scope checks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_respond_approve_initiator_scope(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alice = await _mk_user(session_factory, username="alice")
    org = await _mk_org(session_factory, owner=alice)
    agent = await _mk_agent(
        session_factory, owner=alice, org=org, scope="initiator"
    )
    req = await _mk_request(session_factory, agent=agent, initiator=alice)

    resp = await client.post(
        f"/api/confirmations/{req.id}/respond",
        json={"decision": "approve"},
        headers=_bearer(alice),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "approved"
    assert body["confirmation_id"] == req.id
    assert body["decided_at"]  # non-empty ISO string

    # Row stamped with approver_user_id = alice.
    async with session_factory() as db:
        row = (
            await db.execute(
                select(ConfirmationRequest).where(
                    ConfirmationRequest.id == req.id
                )
            )
        ).scalar_one()
        assert row.status == "approved"
        assert row.approver_user_id == alice.id
        assert row.responded_at is not None


@pytest.mark.asyncio
async def test_respond_wrong_user_scope_initiator(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alice = await _mk_user(session_factory, username="alice")
    bob = await _mk_user(session_factory, username="bob")
    org = await _mk_org(session_factory, owner=alice, extra_members=[bob])
    agent = await _mk_agent(
        session_factory, owner=alice, org=org, scope="initiator"
    )
    req = await _mk_request(session_factory, agent=agent, initiator=alice)

    # Bob (in org) tries to approve — initiator scope forbids.
    resp = await client.post(
        f"/api/confirmations/{req.id}/respond",
        json={"decision": "approve"},
        headers=_bearer(bob),
    )
    assert resp.status_code == 403, resp.text

    # Row still pending.
    async with session_factory() as db:
        row = (
            await db.execute(
                select(ConfirmationRequest).where(
                    ConfirmationRequest.id == req.id
                )
            )
        ).scalar_one()
        assert row.status == "pending"
        assert row.approver_user_id is None


@pytest.mark.asyncio
async def test_respond_agent_owner_scope(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alice = await _mk_user(session_factory, username="alice")  # owner
    bob = await _mk_user(session_factory, username="bob")  # initiator
    org = await _mk_org(session_factory, owner=alice, extra_members=[bob])
    agent = await _mk_agent(
        session_factory, owner=alice, org=org, scope="agent_owner"
    )
    # Bob runs the agent and triggers a confirmation (shared agent scenario).
    req = await _mk_request(session_factory, agent=agent, initiator=bob)

    # Bob tries to approve — agent_owner scope denies.
    resp = await client.post(
        f"/api/confirmations/{req.id}/respond",
        json={"decision": "approve"},
        headers=_bearer(bob),
    )
    assert resp.status_code == 403

    # Alice (agent owner) approves — succeeds.
    resp2 = await client.post(
        f"/api/confirmations/{req.id}/respond",
        json={"decision": "approve"},
        headers=_bearer(alice),
    )
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_respond_org_members_scope(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alice = await _mk_user(session_factory, username="alice")
    bob = await _mk_user(session_factory, username="bob")
    carol = await _mk_user(session_factory, username="carol")  # NOT in org
    org = await _mk_org(session_factory, owner=alice, extra_members=[bob])
    agent = await _mk_agent(
        session_factory, owner=alice, org=org, scope="org_members"
    )
    req = await _mk_request(session_factory, agent=agent, initiator=alice)

    # Carol — outside org → denied.
    resp = await client.post(
        f"/api/confirmations/{req.id}/respond",
        json={"decision": "reject"},
        headers=_bearer(carol),
    )
    assert resp.status_code == 403

    # Bob — in org → allowed.
    resp2 = await client.post(
        f"/api/confirmations/{req.id}/respond",
        json={"decision": "reject", "reason": "not today"},
        headers=_bearer(bob),
    )
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["status"] == "rejected"


@pytest.mark.asyncio
async def test_respond_already_decided(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alice = await _mk_user(session_factory, username="alice")
    org = await _mk_org(session_factory, owner=alice)
    agent = await _mk_agent(
        session_factory, owner=alice, org=org, scope="initiator"
    )
    req = await _mk_request(
        session_factory, agent=agent, initiator=alice, status_="approved"
    )

    resp = await client.post(
        f"/api/confirmations/{req.id}/respond",
        json={"decision": "reject"},
        headers=_bearer(alice),
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_respond_not_found(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alice = await _mk_user(session_factory, username="alice")
    resp = await client.post(
        "/api/confirmations/does-not-exist/respond",
        json={"decision": "approve"},
        headers=_bearer(alice),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Feishu callback path still works through the shared update helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_confirmation_decision_feishu_path(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Feishu callback stamps responded_by_open_id + responded_at, leaves
    approver_user_id NULL (no user mapping yet)."""
    from fim_one.web.api.confirmations import apply_confirmation_decision

    alice = await _mk_user(session_factory, username="alice")
    org = await _mk_org(session_factory, owner=alice)
    agent = await _mk_agent(
        session_factory, owner=alice, org=org, scope="initiator"
    )
    req = await _mk_request(
        session_factory, agent=agent, initiator=alice, mode="channel"
    )

    async with session_factory() as db:
        final_status, newly_applied, payload = await apply_confirmation_decision(
            db,
            confirmation_id=req.id,
            decision="approve",
            approver_user_id=None,
            responded_by_open_id="ou_abcdef",
        )
    assert final_status == "approved"
    assert newly_applied is True
    assert payload and payload.get("tool_name") == "jira.createIssue"

    async with session_factory() as db:
        row = (
            await db.execute(
                select(ConfirmationRequest).where(
                    ConfirmationRequest.id == req.id
                )
            )
        ).scalar_one()
        assert row.responded_by_open_id == "ou_abcdef"
        assert row.approver_user_id is None
        assert row.responded_at is not None


# ---------------------------------------------------------------------------
# SSE listener bridge — frozen contract check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_emits_awaiting_confirmation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Installing the bridge + firing emit_inline_confirmation should
    put a dict matching the FROZEN wire contract on the right queue."""
    from fim_one.core.hooks import emit_inline_confirmation
    from fim_one.web.confirmation_sse import (
        clear_all_queues,
        queue_for,
        register_confirmation_bridge,
        unregister_confirmation_bridge,
    )

    await clear_all_queues()
    register_confirmation_bridge()
    try:
        alice = await _mk_user(session_factory, username="alice")
        org = await _mk_org(session_factory, owner=alice)
        agent = await _mk_agent(
            session_factory, owner=alice, org=org, scope="initiator"
        )
        req = await _mk_request(session_factory, agent=agent, initiator=alice)

        await emit_inline_confirmation(req)

        q = queue_for(agent.id, alice.id)
        event = await asyncio.wait_for(q.get(), timeout=1.0)

        # Frozen contract keys.
        assert set(event.keys()) >= {
            "type",
            "confirmation_id",
            "tool_name",
            "arguments",
            "timeout_at",
            "agent_id",
        }
        assert event["type"] == "awaiting_confirmation"
        assert event["confirmation_id"] == req.id
        assert event["tool_name"] == "jira.createIssue"
        assert event["arguments"] == {"x": 1}
        assert event["agent_id"] == agent.id
        # timeout_at is a parseable ISO-8601 UTC string.
        parsed = datetime.fromisoformat(event["timeout_at"])
        assert parsed.tzinfo is not None
        assert parsed.astimezone(timezone.utc) > datetime.now(timezone.utc)
    finally:
        unregister_confirmation_bridge()
        await clear_all_queues()

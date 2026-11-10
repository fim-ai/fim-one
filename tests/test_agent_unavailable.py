"""B10 / N3: an agent that is no longer usable is an error, not a silent downgrade.

A conversation bound to an agent its user can no longer use (unpublished,
subscription removed, switched off; for the owner too) used to carry on
as a chat with no agent: every connector and MCP server the user can see,
and no confirmation gate. The chat endpoints now refuse the turn with
``agent_unavailable`` (409) and tell the user to start a new conversation.
An agent named explicitly that cannot be used is refused with the
resource's own code (``resource_not_found`` 404, ``resource_inactive`` 409).
"""

from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import fim_one.db.models  # noqa: F401
from fim_one.core.assembly.errors import AssemblyRefusal
from fim_one.db import create_session, init_db, shutdown_db
from fim_one.db.base import Base
from fim_one.db.models.agent import Agent
from fim_one.db.models.conversation import Conversation
from fim_one.db.models.organization import Organization, OrgMembership
from fim_one.db.models.resource_subscription import ResourceSubscription
from fim_one.db.models.user import User
from fim_one.web import create_app
from fim_one.web.api.chat import _resolve_agent_config
from fim_one.web.auth import create_access_token

pytestmark = pytest.mark.timeout(60)


@pytest.fixture()
async def db_env(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """The global engine against a temporary SQLite file, as the app uses it."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-agent-unavailable-long-enough")
    await init_db()
    from fim_one.db import engine as engine_mod

    assert engine_mod._engine is not None
    async with engine_mod._engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield
    finally:
        await shutdown_db()
        os.unlink(path)


class World:
    def __init__(self) -> None:
        self.org = str(uuid.uuid4())
        self.owner = str(uuid.uuid4())
        self.subscriber = str(uuid.uuid4())
        self.agent = str(uuid.uuid4())


@pytest.fixture()
async def world(db_env: None) -> World:
    """An owner's published agent, and a subscriber in the same organization."""
    w = World()
    async with create_session() as db:
        db.add(User(id=w.owner, email="o@x.io", username="owner", password_hash="x"))
        db.add(User(id=w.subscriber, email="s@x.io", username="sub", password_hash="x"))
        db.add(Organization(id=w.org, name="Org", slug=f"org-{w.org[:6]}", owner_id=w.owner))
        await db.flush()
        for uid in (w.owner, w.subscriber):
            db.add(OrgMembership(id=str(uuid.uuid4()), org_id=w.org, user_id=uid, role="member"))
        db.add(
            Agent(
                id=w.agent,
                user_id=w.owner,
                org_id=w.org,
                name="Shared",
                visibility="org",
                status="published",
                is_active=True,
            )
        )
        db.add(
            ResourceSubscription(
                user_id=w.subscriber,
                resource_type="agent",
                resource_id=w.agent,
                org_id=w.org,
            )
        )
        await db.commit()
    return w


async def _conversation(user_id: str, agent_id: str | None) -> str:
    conv_id = str(uuid.uuid4())
    async with create_session() as db:
        db.add(
            Conversation(id=conv_id, user_id=user_id, title="t", mode="react", agent_id=agent_id)
        )
        await db.commit()
    return conv_id


async def _unpublish(agent_id: str) -> None:
    async with create_session() as db:
        agent = await db.get(Agent, agent_id)
        assert agent is not None
        agent.visibility = "private"
        agent.status = "draft"
        await db.commit()


async def _switch_off(agent_id: str) -> None:
    async with create_session() as db:
        agent = await db.get(Agent, agent_id)
        assert agent is not None
        agent.is_active = False
        await db.commit()


async def _refusal(**kwargs: Any) -> AssemblyRefusal:
    with pytest.raises(AssemblyRefusal) as exc:
        await _resolve_agent_config(refuse_unavailable=True, **kwargs)
    return exc.value


class TestResolution:
    async def test_a_usable_agent_is_resolved(self, world: World) -> None:
        conv = await _conversation(world.subscriber, world.agent)
        cfg = await _resolve_agent_config(
            None, conv, user_id=world.subscriber, refuse_unavailable=True
        )
        assert cfg is not None and cfg["agent_id"] == world.agent

    async def test_conversation_bound_to_an_unpublished_agent(self, world: World) -> None:
        conv = await _conversation(world.subscriber, world.agent)
        await _unpublish(world.agent)

        refusal = await _refusal(agent_id=None, conversation_id=conv, user_id=world.subscriber)

        assert (refusal.error_code, refusal.status_code) == ("agent_unavailable", 409)
        assert "Start a new conversation" in refusal.detail
        assert refusal.detail_args == {"agent_id": world.agent, "reason": "resource_not_found"}

    async def test_conversation_bound_to_a_switched_off_agent_for_its_owner(
        self, world: World
    ) -> None:
        """Switching off applies to the owner too, as for every other resource."""
        conv = await _conversation(world.owner, world.agent)
        await _switch_off(world.agent)

        refusal = await _refusal(agent_id=None, conversation_id=conv, user_id=world.owner)

        assert refusal.error_code == "agent_unavailable"
        assert refusal.detail_args == {"agent_id": world.agent, "reason": "resource_inactive"}

    async def test_the_same_agent_named_explicitly_in_its_conversation(self, world: World) -> None:
        """The portal sends the selected agent with the conversation: still the bound one."""
        conv = await _conversation(world.owner, world.agent)
        await _switch_off(world.agent)
        refusal = await _refusal(agent_id=world.agent, conversation_id=conv, user_id=world.owner)
        assert refusal.error_code == "agent_unavailable"

    async def test_an_explicit_agent_in_a_new_chat(self, world: World) -> None:
        """Not bound to anything yet: the resource's own refusal."""
        await _unpublish(world.agent)
        refusal = await _refusal(
            agent_id=world.agent, conversation_id=None, user_id=world.subscriber
        )
        assert (refusal.error_code, refusal.status_code) == ("resource_not_found", 404)

        await _switch_off(world.agent)
        refusal = await _refusal(agent_id=world.agent, conversation_id=None, user_id=world.owner)
        assert (refusal.error_code, refusal.status_code) == ("resource_inactive", 409)

    async def test_an_explicit_agent_other_than_the_bound_one(self, world: World) -> None:
        other = str(uuid.uuid4())
        async with create_session() as db:
            db.add(Agent(id=other, user_id=world.owner, name="Other", is_active=False))
            await db.commit()
        conv = await _conversation(world.owner, world.agent)
        refusal = await _refusal(agent_id=other, conversation_id=conv, user_id=world.owner)
        assert refusal.error_code == "resource_inactive"

    async def test_without_the_flag_the_caller_still_carries_on(self, world: World) -> None:
        """The workflow Agent step keeps its behaviour until C1 phase 4."""
        conv = await _conversation(world.subscriber, world.agent)
        await _unpublish(world.agent)
        assert await _resolve_agent_config(None, conv, user_id=world.subscriber) is None

        await _switch_off(world.agent)
        cfg = await _resolve_agent_config(world.agent, None, user_id=world.owner)
        assert cfg is not None and cfg["agent_id"] == world.agent


class TestChatEndpoints:
    @pytest.fixture()
    def app(self) -> FastAPI:
        return create_app()

    @pytest.mark.parametrize("endpoint", ["/api/react", "/api/dag", "/api/auto"])
    async def test_the_turn_is_refused_with_the_code_and_the_advice(
        self, app: FastAPI, world: World, endpoint: str
    ) -> None:
        conv = await _conversation(world.subscriber, world.agent)
        await _unpublish(world.agent)
        token = create_access_token(world.subscriber, "s@x.io")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.post(
                endpoint, json={"q": "hello", "conversation_id": conv, "token": token}
            )

        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["error_code"] == "agent_unavailable"
        assert "Start a new conversation" in body["detail"]
        assert body["error_args"]["agent_id"] == world.agent


    @pytest.mark.parametrize("endpoint", ["/api/react", "/api/dag"])
    async def test_someone_elses_conversation_is_not_found_whatever_its_agent(
        self, app: FastAPI, world: World, endpoint: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The agent of a conversation is not looked at before the conversation is the caller's.

        The ownership check is slowed so that an agent resolution running
        beside it would answer first.
        """
        import asyncio

        from fim_one.web.api import chat as chat_mod

        conv = await _conversation(world.owner, world.agent)
        await _unpublish(world.agent)
        token = create_access_token(world.subscriber, "s@x.io")
        check = chat_mod._validate_conversation_ownership

        async def slow_check(conversation_id: str, user_id: str) -> None:
            await asyncio.sleep(0.3)
            await check(conversation_id, user_id)

        monkeypatch.setattr(chat_mod, "_validate_conversation_ownership", slow_check)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.post(
                endpoint, json={"q": "hello", "conversation_id": conv, "token": token}
            )

        assert resp.status_code == 404, resp.text
        body = resp.json()
        assert body["error_code"] == "conversation_not_found"
        assert world.agent not in resp.text


def test_the_message_tells_the_user_what_to_do() -> None:
    import json
    from pathlib import Path

    import fim_one

    root = Path(fim_one.__file__).resolve().parents[2]
    messages = json.loads((root / "frontend/messages/en/errors.json").read_text())
    assert "new conversation" in messages["agent_unavailable"].lower()
    for code in ("resource_not_found", "resource_inactive"):
        assert messages[code]

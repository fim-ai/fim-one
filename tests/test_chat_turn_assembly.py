"""A whole chat turn through each endpoint: what the agent loop is built from.

The chat endpoints assemble a turn in stages (agent, models, tools, MCP,
hooks) taken from ``web/services/agent_runtime.py``. These tests drive the
real endpoints over HTTP with a scripted model and look at what reached the
agent loop, with an agent and without one.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import fim_one.db.models  # noqa: F401
from fim_one.core.agent import ReActAgent
from fim_one.db import create_session, init_db, shutdown_db
from fim_one.db.base import Base
from fim_one.db.models.agent import Agent
from fim_one.db.models.connector import Connector, ConnectorAction
from fim_one.db.models.conversation import Conversation
from fim_one.db.models.organization import Organization, OrgMembership
from fim_one.db.models.user import User
from fim_one.web import create_app
from fim_one.web.auth import create_access_token

from .fake_llm import NO_NATIVE_TOOLS, FakeLLM, json_answer

pytestmark = pytest.mark.timeout(90)

_EVERY_ANSWER: dict[str, Any] = {
    "steps": [{"id": "step_1", "task": "say pong", "dependencies": []}],
    "type": "final_answer",
    "reasoning": "finished",
    "answer": "pong",
    "achieved": True,
    "confidence": 1.0,
}


@pytest.fixture()
async def db_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> AsyncIterator[None]:
    """The global engine against a temporary SQLite file, as the app uses it."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-chat-turn-assembly-long-enough")
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads"))
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
        self.user = str(uuid.uuid4())
        self.agent = str(uuid.uuid4())
        self.connector = str(uuid.uuid4())
        self.token = ""


@pytest.fixture()
async def world(db_env: None) -> World:
    """A user who owns one API connector and one agent bound to it."""
    w = World()
    async with create_session() as db:
        db.add(User(id=w.user, email="u@x.io", username="user", password_hash="x"))
        db.add(Organization(id=w.org, name="Org", slug=f"org-{w.org[:6]}", owner_id=w.user))
        await db.flush()
        db.add(OrgMembership(id=str(uuid.uuid4()), org_id=w.org, user_id=w.user, role="member"))
        db.add(
            Connector(
                id=w.connector,
                user_id=w.user,
                name="crm",
                type="api",
                base_url="https://crm.example.test",
                auth_type="none",
                status="published",
                is_active=True,
            )
        )
        db.add(
            ConnectorAction(
                id=str(uuid.uuid4()),
                connector_id=w.connector,
                name="ping",
                method="GET",
                path="/ping",
            )
        )
        db.add(
            Agent(
                id=w.agent,
                user_id=w.user,
                name="Helper",
                instructions="INSTRUCTIONS-OF-THE-HELPER",
                connector_ids=[w.connector],
                tool_categories=["general"],
                status="published",
                is_active=True,
            )
        )
        await db.commit()
    w.token = create_access_token(w.user, "u@x.io")
    return w


@pytest.fixture()
def loops(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every agent loop the endpoints build, with a scripted model behind them."""
    from fim_one.web.api import chat as chat_mod

    built: list[dict[str, Any]] = []

    class _Recording(ReActAgent):
        def __init__(self, **kwargs: Any) -> None:
            built.append(kwargs)
            super().__init__(**kwargs)

    def _fake() -> FakeLLM:
        # One reply that reads as a one-step plan to the planner, as a final
        # answer to the agent loop and as "achieved" to the plan analyzer.
        return FakeLLM([json_answer(_EVERY_ANSWER)], abilities=NO_NATIVE_TOOLS)

    async def _llm(_cfg: Any, _db: Any) -> FakeLLM:
        return _fake()

    class _Registry:
        """Every role resolves to the scripted model (a plan step picks by role)."""

        def get_default(self) -> FakeLLM:
            return _fake()

        def get_by_role(self, _role: str) -> FakeLLM:
            return _fake()

    async def _registry(_db: Any) -> _Registry:
        return _Registry()

    async def _no_network(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a chat turn under test reached for a real model")

    monkeypatch.setattr(chat_mod, "ReActAgent", _Recording)
    monkeypatch.setattr(chat_mod, "_resolve_llm", _llm)
    monkeypatch.setattr(chat_mod, "_resolve_fast_llm", _llm)
    monkeypatch.setattr(chat_mod, "get_model_registry_with_group", _registry)
    monkeypatch.setattr("fim_one.web.deps.get_model_registry_with_group", _registry)
    monkeypatch.setattr("litellm.acompletion", _no_network)
    monkeypatch.setattr("litellm.aresponses", _no_network)
    return built


async def _conversation(user_id: str, agent_id: str | None) -> str:
    conv_id = str(uuid.uuid4())
    async with create_session() as db:
        db.add(
            Conversation(id=conv_id, user_id=user_id, title="t", mode="react", agent_id=agent_id)
        )
        await db.commit()
    return conv_id


def _events(body: str) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    for block in body.split("\n\n"):
        name, data = None, None
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if name:
            events.append((name, json.loads(data) if data else None))
    return events


async def _turn(app: FastAPI, endpoint: str, world: World, agent_id: str | None) -> str:
    conv = await _conversation(world.user, agent_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.post(
            endpoint, json={"q": "say pong", "conversation_id": conv, "token": world.token}
        )
    assert resp.status_code == 200, resp.text
    return resp.text


class TestAReActTurn:
    @pytest.fixture()
    def app(self) -> FastAPI:
        return create_app()

    async def test_with_an_agent_the_loop_gets_the_agents_tools_and_its_gate(
        self, app: FastAPI, world: World, loops: list[dict[str, Any]]
    ) -> None:
        body = await _turn(app, "/api/react", world, world.agent)

        names = [name for name, _ in _events(body)]
        assert "done" in names, body[-2000:]
        (loop,) = loops
        tools = {t.name for t in loop["tools"].list_tools()}
        # The agent's categories and its bound connector; nothing else of the user's.
        assert "connector" in tools
        assert "shell_exec" not in tools and "call_agent" not in tools
        assert loop["agent_id"] == world.agent
        assert loop["user_id"] == world.user
        assert loop["agent_directive"] and "INSTRUCTIONS-OF-THE-HELPER" in loop["agent_directive"]
        assert [h.name for h in loop["hook_registry"].list_hooks()] == ["approval_gate"]

    async def test_with_no_agent_the_loop_gets_everything_the_user_can_use(
        self, app: FastAPI, world: World, loops: list[dict[str, Any]]
    ) -> None:
        body = await _turn(app, "/api/react", world, None)

        assert "done" in [name for name, _ in _events(body)], body[-2000:]
        (loop,) = loops
        tools = {t.name for t in loop["tools"].list_tools()}
        assert {"connector", "call_agent", "kb_retrieve"} <= tools
        assert loop["agent_id"] is None
        assert [h.name for h in loop["hook_registry"].list_hooks()] == ["approval_gate"]


class TestTheOtherEndpoints:
    @pytest.fixture()
    def app(self) -> FastAPI:
        return create_app()

    async def test_a_plan_turn_builds_its_step_loop_from_the_same_parts(
        self, app: FastAPI, world: World, loops: list[dict[str, Any]]
    ) -> None:
        body = await _turn(app, "/api/dag", world, world.agent)

        names = [name for name, _ in _events(body)]
        assert "done" in names and "error" not in names, body[-2000:]
        assert loops, "the plan ran at least one step"
        for loop in loops:
            tools = {t.name for t in loop["tools"].list_tools()}
            assert "connector" in tools
            assert "shell_exec" not in tools and "call_agent" not in tools
            assert loop["agent_id"] == world.agent
            assert [h.name for h in loop["hook_registry"].list_hooks()] == ["approval_gate"]

    async def test_auto_routes_and_then_runs_one_of_the_two(
        self, app: FastAPI, world: World, loops: list[dict[str, Any]]
    ) -> None:
        body = await _turn(app, "/api/auto", world, world.agent)

        events = _events(body)
        assert events[0][0] == "routing" and events[0][1]["mode"] == "react"
        assert "done" in [name for name, _ in events]
        (loop,) = loops
        assert "connector" in {t.name for t in loop["tools"].list_tools()}
        assert loop["agent_id"] == world.agent


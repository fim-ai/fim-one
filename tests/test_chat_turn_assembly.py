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
from fim_one.core.planner.checkpoint import DAGCheckpoint
from fim_one.db import create_session, init_db, shutdown_db
from fim_one.db.base import Base
from fim_one.db.models.agent import Agent
from fim_one.db.models.connector import Connector, ConnectorAction
from fim_one.db.models.conversation import Conversation
from fim_one.db.models.organization import Organization, OrgMembership
from fim_one.db.models.usage_ledger import UsageLedger
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
def loops(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> list[dict[str, Any]]:
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

    # Conversation sandboxes go under the test's own directory, not the checkout's data/.
    monkeypatch.setattr(
        chat_mod,
        "_conversation_sandbox_root",
        lambda conversation_id: tmp_path / "sandbox" / conversation_id if conversation_id else None,
    )
    # So do a plan turn's checkpoints (the default is data/dag_checkpoints in the cwd).
    monkeypatch.setattr(
        chat_mod, "DAGCheckpoint", lambda: DAGCheckpoint(str(tmp_path / "dag_checkpoints"))
    )
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


def _payloads(body: str) -> list[tuple[str, Any]]:
    """The events of a turn without the ``{"cursor", "data"}`` envelope."""
    return [
        (name, data["data"] if isinstance(data, dict) and "cursor" in data else data)
        for name, data in _events(body)
    ]


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



class TestCompletionNotificationOfAPersonalAgent:
    """A personal agent's completion notice is resolved through its owner.

    The agent has no organization, so the channel is looked up among the
    organizations its owner belongs to. The chat endpoints hand the owner's
    id to the notifier; without it the channel resolves to nothing and no
    notice is sent.
    """

    @pytest.fixture()
    def app(self) -> FastAPI:
        return create_app()

    @pytest.mark.parametrize("endpoint", ["/api/react", "/api/dag"])
    async def test_the_owners_id_reaches_the_notifier_and_the_notice_is_sent(
        self,
        endpoint: str,
        app: FastAPI,
        world: World,
        loops: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import asyncio
        from unittest.mock import AsyncMock

        from fim_one.core.channels import ChannelSendResult
        from fim_one.db.models.channel import Channel
        from fim_one.web import notifications

        channel_id = str(uuid.uuid4())
        async with create_session() as db:
            db.add(
                Channel(
                    id=channel_id,
                    name="Ops",
                    type="feishu",
                    org_id=world.org,
                    created_by=world.user,
                    is_active=True,
                    config={"app_id": "cli_x", "app_secret": "s", "chat_id": "oc_ops"},
                )
            )
            agent = await db.get(Agent, world.agent)
            assert agent is not None and agent.org_id is None
            agent.model_config_json = {
                "notifications": {"on_complete": {"enabled": True, "channel_id": channel_id}}
            }
            await db.commit()

        shims: list[Any] = []
        real = notifications.notify_agent_completion

        def _recording(**kwargs: Any) -> Any:
            shims.append(kwargs["agent"])
            return real(**kwargs)

        monkeypatch.setattr(notifications, "notify_agent_completion", _recording)
        send = AsyncMock(return_value=ChannelSendResult(ok=True))
        monkeypatch.setattr(
            "fim_one.core.channels.feishu.FeishuChannel.send_completion", send
        )

        body = await _turn(app, endpoint, world, world.agent)
        assert "done" in [name for name, _ in _events(body)], body[-2000:]

        # The notice is posted in the background, after "done".
        for _ in range(100):
            if send.await_count:
                break
            await asyncio.sleep(0.05)

        assert len(shims) == 1
        assert shims[0].org_id is None
        assert shims[0].user_id == world.user
        assert send.await_count == 1, "the completion notice was not sent"
        assert send.await_args is not None
        summary = send.await_args.args[0]
        assert summary.agent_name == "Helper"
        assert summary.user_message == "say pong"


class TestAutoIsAdmittedBeforeItClassifies:
    """Auto mode asks a model which way to route. That call is part of the turn."""

    @pytest.fixture()
    def app(self) -> FastAPI:
        return create_app()

    async def test_an_over_quota_turn_is_refused_before_any_model_is_asked(
        self,
        app: FastAPI,
        world: World,
        loops: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        asked: list[str] = []

        async def _classify(query: str, _llm: Any) -> Any:
            asked.append(query)
            raise AssertionError("classified a turn that is not admitted")

        monkeypatch.setattr("fim_one.core.planner.router.classify_execution_mode", _classify)
        async with create_session() as db:
            user = await db.get(User, world.user)
            assert user is not None
            user.token_quota = 10
            db.add(
                UsageLedger(
                    user_id=world.user, source_type="conversation", entry="chat", total_tokens=10
                )
            )
            await db.commit()

        conv = await _conversation(world.user, None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.post(
                "/api/auto", json={"q": "say pong", "conversation_id": conv, "token": world.token}
            )

        assert resp.status_code == 429, resp.text
        assert resp.json()["error_code"] == "token_quota_exceeded"
        assert asked == [] and loops == []

    @pytest.mark.parametrize("routed_to", ["react", "dag"])
    async def test_the_turn_is_admitted_once_and_in_order_before_it_is_routed(
        self,
        app: FastAPI,
        world: World,
        loops: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
        routed_to: str,
    ) -> None:
        """The routed endpoint resolved the user and admitted the turn a second time."""
        from fim_one.core.planner import router as router_mod
        from fim_one.web.api import chat as chat_mod
        from fim_one.web.services import agent_runtime

        seen: list[str] = []

        def _spy(module: Any, name: str, label: str) -> None:
            original = getattr(module, name)

            async def _recorded(*args: Any, **kwargs: Any) -> Any:
                seen.append(label)
                return await original(*args, **kwargs)

            monkeypatch.setattr(module, name, _recorded)

        async def _classify(_query: str, _llm: Any) -> Any:
            seen.append("route")
            return router_mod.RouteDecision(mode=routed_to, reasoning="scripted")

        _spy(chat_mod, "_resolve_user", "user")
        _spy(chat_mod, "_validate_conversation_ownership", "conversation")
        _spy(chat_mod, "check_quota", "quota")
        _spy(agent_runtime, "resolve_agent", "agent")
        monkeypatch.setattr(router_mod, "classify_execution_mode", _classify)

        body = await _turn(app, "/api/auto", world, world.agent)

        events = _events(body)
        assert events[0] == ("routing", {"mode": routed_to, "reasoning": "scripted"})
        assert "done" in [name for name, _ in events], body[-2000:]
        assert loops and all(loop["agent_id"] == world.agent for loop in loops)
        assert seen == ["user", "conversation", "quota", "agent", "route"]

    async def test_someone_elses_conversation_is_refused_before_any_model_is_asked(
        self,
        app: FastAPI,
        world: World,
        loops: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Ownership comes first: the routing call was made for a conversation not theirs."""
        asked: list[str] = []

        async def _classify(query: str, _llm: Any) -> Any:
            asked.append(query)
            raise AssertionError("classified a turn in someone else's conversation")

        monkeypatch.setattr("fim_one.core.planner.router.classify_execution_mode", _classify)
        other = str(uuid.uuid4())
        async with create_session() as db:
            db.add(User(id=other, email="o@x.io", username="other", password_hash="x"))
            await db.commit()
        # The other user's conversation, with the caller's own (usable) agent
        # named in the request: nothing but ownership stands in the way.
        conv = await _conversation(other, None)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.post(
                "/api/auto",
                json={
                    "q": "say pong",
                    "conversation_id": conv,
                    "agent_id": world.agent,
                    "token": world.token,
                },
            )

        assert resp.status_code == 404, resp.text
        assert resp.json()["error_code"] == "conversation_not_found"
        assert asked == [] and loops == []


class TestTheMidStreamQuotaGuard:
    """A turn admitted under the limit is stopped when its answer crosses it."""

    # 100 chunks of 100 characters: about 2,300 tokens by the guard's estimate.
    CHUNKS = 100
    CHUNK = "x" * 100
    QUOTA = 1000

    @pytest.fixture()
    def app(self) -> FastAPI:
        return create_app()

    async def _limit(self, world: World) -> None:
        async with create_session() as db:
            user = await db.get(User, world.user)
            assert user is not None
            user.token_quota = self.QUOTA
            await db.commit()

    @staticmethod
    def _in_flight(chunk: str) -> None:
        """What the model client does as a streamed call's text arrives: it
        keeps the turn's meter up to date with an estimate of the answer so
        far (``estimate_tokens``). The scripted model of these
        tests is not that client, so the stand-in stream does it."""
        from fim_one.core.model.usage import current_meter, estimate_tokens

        meter = current_meter.get()
        assert meter is not None, "the answer is streamed outside the turn's usage meter"
        meter.stream_progress(estimate_tokens(chunk))

    def _assert_stopped(self, body: str) -> None:
        events = _payloads(body)
        streamed = sum(
            len(data["content"])
            for name, data in events
            if name == "answer" and data.get("status") == "delta"
        )
        # Cut at the first check after what the turn's meter holds reaches
        # the limit (checked every 200 characters), not at the end.
        from fim_one.core.model.usage import estimate_tokens

        limit = self.QUOTA * len(self.CHUNK) / estimate_tokens(self.CHUNK)
        assert limit <= streamed <= limit + 200, streamed
        assert streamed < self.CHUNKS * len(self.CHUNK)
        (terminator,) = [data for name, data in events if name == "error"]
        assert terminator["code"] == "QUOTA_EXCEEDED"
        assert terminator["quota"] == self.QUOTA
        assert terminator["tokens_used"] >= self.QUOTA
        # The turn still ends the ordinary way, so the partial answer is kept.
        names = [name for name, _ in events]
        assert names.index("error") < names.index("done")

    async def test_a_react_answer_that_crosses_the_limit_is_cut(
        self,
        app: FastAPI,
        world: World,
        loops: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _long(_self: Any, *_args: Any, **_kwargs: Any) -> AsyncIterator[str]:
            for _ in range(self.CHUNKS):
                self._in_flight(self.CHUNK)
                yield self.CHUNK

        monkeypatch.setattr(ReActAgent, "stream_answer", _long)
        await self._limit(world)

        body = await _turn(app, "/api/react", world, world.agent)

        self._assert_stopped(body)

    async def test_a_plan_answer_that_crosses_the_limit_is_cut(
        self,
        app: FastAPI,
        world: World,
        loops: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from fim_one.web.api import chat as chat_mod

        async def _long(_self: Any, *_args: Any, **_kwargs: Any) -> AsyncIterator[str]:
            for _ in range(self.CHUNKS):
                self._in_flight(self.CHUNK)
                yield self.CHUNK

        monkeypatch.setattr(chat_mod.PlanAnalyzer, "stream_synthesize", _long)
        await self._limit(world)

        body = await _turn(app, "/api/dag", world, world.agent)

        self._assert_stopped(body)

    @pytest.mark.parametrize("endpoint", ["/api/react", "/api/dag"])
    async def test_an_answer_that_stays_under_the_limit_is_streamed_whole(
        self,
        app: FastAPI,
        world: World,
        loops: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
        endpoint: str,
    ) -> None:
        from fim_one.web.api import chat as chat_mod

        async def _short(_self: Any, *_args: Any, **_kwargs: Any) -> AsyncIterator[str]:
            for _ in range(10):
                self._in_flight(self.CHUNK)
                yield self.CHUNK

        monkeypatch.setattr(ReActAgent, "stream_answer", _short)
        monkeypatch.setattr(chat_mod.PlanAnalyzer, "stream_synthesize", _short)
        await self._limit(world)

        events = _payloads(await _turn(app, endpoint, world, world.agent))

        streamed = sum(
            len(data["content"])
            for name, data in events
            if name == "answer" and data.get("status") == "delta"
        )
        assert streamed == 1000
        assert "error" not in [name for name, _ in events]

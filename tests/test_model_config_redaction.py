"""An agent's model settings reach a model prompt or a tool result with their secrets masked.

The builder assistant is told the agent's current state in its
instructions, and its tools return the agent's settings; neither carries
the ``api_key`` an agent's inline model settings hold.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db
import fim_one.db.models  # noqa: F401
from fim_one.db import get_session
from fim_one.db.base import Base
from fim_one.db.models import User
from fim_one.web.auth import get_current_user

from fim_one.core.security.redact import MASK, is_secret_key, redact_secrets
from fim_one.core.tool.builtin import agent_builder
from fim_one.db.models import Agent

KEY = "sk-inline-0123456789abcdef0123"
CONFIG = {
    "model_name": "m-1",
    "base_url": "https://llm.example.com/v1",
    "api_key": KEY,
    "temperature": 0.3,
    "max_tokens": 900,
    "hooks": {"class_hooks": [{"name": "h", "options": {"authToken": KEY}}]},
}


class TestRedactSecrets:
    @pytest.mark.parametrize(
        "key",
        ["api_key", "apiKey", "x-api-key", "openai_api_key", "secret", "client_secret",
         "password", "access_token", "authToken", "Authorization", "credentials", "key"],
    )
    def test_secret_names(self, key: str) -> None:
        assert is_secret_key(key)

    @pytest.mark.parametrize(
        "key",
        ["max_tokens", "max_output_tokens", "model_name", "base_url", "keyboard", "monkey",
         "temperature", "context_size", "provider"],
    )
    def test_other_names(self, key: str) -> None:
        assert not is_secret_key(key)

    def test_nested_values_are_masked_and_the_input_is_kept(self) -> None:
        out = redact_secrets(CONFIG)
        assert KEY not in json.dumps(out)
        assert out["api_key"] == MASK
        assert out["hooks"]["class_hooks"][0]["options"]["authToken"] == MASK
        assert out["max_tokens"] == 900 and out["base_url"] == CONFIG["base_url"]
        assert CONFIG["api_key"] == KEY

    def test_an_empty_secret_stays_empty(self) -> None:
        assert redact_secrets({"api_key": ""}) == {"api_key": ""}
        assert redact_secrets({"api_key": None}) == {"api_key": None}
        assert redact_secrets(None) is None


class _Env:
    def __init__(self, client: AsyncClient, sessions: async_sessionmaker[AsyncSession], me: str) -> None:
        self.client = client
        self.sessions = sessions
        self.me = me

    async def agent(self, agent_id: str) -> Agent:
        async with self.sessions() as s:
            row = await s.get(Agent, agent_id)
            assert row is not None
            return row


@pytest_asyncio.fixture()
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Env]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    # The builder tools open their own sessions.
    monkeypatch.setattr(fim_one.db, "create_session", sessions)

    me = str(uuid.uuid4())
    async with sessions() as s:
        s.add(User(id=me, email="me@x.test", username="me", password_hash="x"))
        s.add(Agent(id="agent-1", user_id=me, name="Helper", model_config_json=CONFIG))
        await s.commit()

    from fim_one.web.api.builder import router

    app = FastAPI()
    app.include_router(router)

    async def _session() -> AsyncIterator[AsyncSession]:
        async with sessions() as s:
            yield s

    async def _me() -> User:
        async with sessions() as s:
            user = await s.get(User, me)
            assert user is not None
            return user

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = _me
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        yield _Env(client, sessions, me)
    await engine.dispose()


class TestBuilderSeesNoKey:
    async def test_builder_instructions(self, env: _Env) -> None:
        body = {"target_type": "agent", "target_id": "agent-1"}
        r = await env.client.post("/api/builder/session", json=body)
        assert r.status_code == 200, r.text
        builder = await env.agent(r.json()["data"]["builder_agent_id"])
        assert KEY not in builder.instructions
        assert "https://llm.example.com/v1" in builder.instructions

        # Opening it again refreshes the instructions the same way.
        r = await env.client.post("/api/builder/session", json=body)
        builder = await env.agent(r.json()["data"]["builder_agent_id"])
        assert KEY not in builder.instructions

    async def test_builder_tools(self, env: _Env) -> None:
        args = {"agent_id": "agent-1", "user_id": env.me}

        shown = await agent_builder.AgentGetSettingsTool(**args).run()
        assert KEY not in shown
        assert json.loads(shown)["model_config"]["api_key"] == MASK

        changed = await agent_builder.AgentSetModelTool(**args).run(temperature=0.5)
        assert KEY not in changed
        # The stored settings keep the key itself.
        stored = (await env.agent("agent-1")).model_config_json
        assert stored["api_key"] == KEY and stored["temperature"] == 0.5

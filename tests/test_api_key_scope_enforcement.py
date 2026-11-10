"""API-key scopes are enforced on every route a key can reach.

Keys store scopes, but nothing used to check them: a ``chat`` key could delete
the owner's agents, and a restricted key could create a new key with no scopes
— an unrestricted one. Scoped keys now reach only the routes their scopes grant
(default deny, ``fim_one.web.api_key_scopes``), a key can never create a key
broader than itself, and unrestricted keys and signed-in sessions keep full
access.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

import fim_one.db.models  # noqa: F401 — register all models with metadata
from fim_one.db import create_session, init_db, shutdown_db
from fim_one.db.base import Base
from fim_one.db.models import ApiKey, User
from fim_one.db.models.agent import Agent
from fim_one.web import create_app
from fim_one.web.api.chat import _resolve_user
from fim_one.web.api_key_scopes import (
    _RULES,
    check_delegation,
    parse_scopes,
    required_scope,
    scopes_allow,
)
from fim_one.web.auth import create_access_token
from fim_one.web.exceptions import AppError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def db_env() -> AsyncIterator[None]:
    """Initialise the global engine/session factory against a temp SQLite file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    prev_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{path}"
    await init_db()

    from fim_one.db import engine as engine_mod

    assert engine_mod._engine is not None
    async with engine_mod._engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield
    finally:
        await shutdown_db()
        if prev_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev_url
        os.unlink(path)


@pytest.fixture(scope="module")
def app() -> FastAPI:
    return create_app()


@pytest_asyncio.fixture()
async def client(app: FastAPI, db_env: None) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://api.test") as c:
        yield c


async def _seed_owner() -> User:
    session = create_session()
    try:
        user = User(
            id=str(uuid.uuid4()),
            email=f"{uuid.uuid4().hex[:8]}@example.com",
            username=f"u_{uuid.uuid4().hex[:8]}",
            password_hash="x",
            is_active=True,
        )
        session.add(user)
        await session.commit()
        return user
    finally:
        await session.close()


async def _seed_key(user: User, scopes: str | None) -> str:
    raw_key = f"fim_{uuid.uuid4().hex}"
    session = create_session()
    try:
        session.add(
            ApiKey(
                id=str(uuid.uuid4()),
                key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
                user_id=user.id,
                name="k",
                key_prefix=raw_key[:8],
                is_active=True,
                total_requests=0,
                scopes=scopes,
            )
        )
        await session.commit()
    finally:
        await session.close()
    return raw_key


async def _seed_agent(user: User) -> str:
    session = create_session()
    try:
        agent = Agent(user_id=user.id, name="victim agent", status="draft")
        session.add(agent)
        await session.commit()
        return str(agent.id)
    finally:
        await session.close()


async def _agent_exists(agent_id: str) -> bool:
    session = create_session()
    try:
        return (
            await session.execute(select(Agent).where(Agent.id == agent_id))
        ).scalar_one_or_none() is not None
    finally:
        await session.close()


async def _key_count(user: User) -> int:
    session = create_session()
    try:
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(ApiKey).where(ApiKey.user_id == user.id)
                )
            ).scalar()
            or 0
        )
    finally:
        await session.close()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# The reported cases
# ---------------------------------------------------------------------------


class TestScopedKeysStayInTheirLane:
    async def test_chat_key_cannot_delete_agent(self, client: AsyncClient) -> None:
        owner = await _seed_owner()
        agent_id = await _seed_agent(owner)
        chat_key = await _seed_key(owner, "chat")

        resp = await client.delete(f"/api/agents/{agent_id}", headers=_bearer(chat_key))

        assert resp.status_code == 403
        assert resp.json()["error_code"] == "api_key_scope_denied"
        assert await _agent_exists(agent_id)

    @pytest.mark.parametrize("scopes", ["agents", "agents:read", "chat"])
    async def test_scoped_key_cannot_mint_unrestricted_key(
        self, client: AsyncClient, scopes: str
    ) -> None:
        owner = await _seed_owner()
        key = await _seed_key(owner, scopes)
        before = await _key_count(owner)

        resp = await client.post(
            "/api/me/api-keys", headers=_bearer(key), json={"name": "escalated"}
        )

        assert resp.status_code == 403
        assert await _key_count(owner) == before

    async def test_scoped_key_cannot_mint_key_even_with_same_scopes(
        self, client: AsyncClient
    ) -> None:
        # Key management is not part of any scope.
        owner = await _seed_owner()
        key = await _seed_key(owner, "agents")
        resp = await client.post(
            "/api/me/api-keys", headers=_bearer(key), json={"name": "n", "scopes": "agents"}
        )
        assert resp.status_code == 403

    async def test_scoped_key_cannot_list_account_keys(self, client: AsyncClient) -> None:
        owner = await _seed_owner()
        key = await _seed_key(owner, "agents")
        resp = await client.get("/api/me/api-keys", headers=_bearer(key))
        assert resp.status_code == 403

    async def test_agents_key_reads_but_cannot_write_agents(self, client: AsyncClient) -> None:
        owner = await _seed_owner()
        agent_id = await _seed_agent(owner)
        key = await _seed_key(owner, "agents")

        assert (await client.get("/api/agents", headers=_bearer(key))).status_code == 200
        assert (
            await client.get(f"/api/agents/{agent_id}", headers=_bearer(key))
        ).status_code == 200
        assert (
            await client.post("/api/agents", headers=_bearer(key), json={"name": "x"})
        ).status_code == 403
        assert (
            await client.delete(f"/api/agents/{agent_id}", headers=_bearer(key))
        ).status_code == 403
        assert (
            await client.post(f"/api/agents/{agent_id}/publish", headers=_bearer(key), json={})
        ).status_code == 403
        assert await _agent_exists(agent_id)

    async def test_chat_key_uses_conversations_but_not_agents(
        self, client: AsyncClient
    ) -> None:
        owner = await _seed_owner()
        key = await _seed_key(owner, "chat")
        assert (await client.get("/api/conversations", headers=_bearer(key))).status_code == 200
        assert (await client.get("/api/agents", headers=_bearer(key))).status_code == 403

    async def test_scoped_key_cannot_reach_unlisted_route(self, client: AsyncClient) -> None:
        owner = await _seed_owner()
        key = await _seed_key(owner, "chat,agents,kb,connectors")
        assert (await client.get("/api/auth/me", headers=_bearer(key))).status_code == 403
        assert (
            await client.post("/api/auth/oauth/bind-ticket", headers=_bearer(key))
        ).status_code == 403

    async def test_disabled_and_bogus_keys_still_401(self, client: AsyncClient) -> None:
        resp = await client.delete("/api/agents/x", headers=_bearer("fim_bogus"))
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Legitimate access is unchanged
# ---------------------------------------------------------------------------


class TestFullAccessUnchanged:
    async def test_unrestricted_key_keeps_full_access(self, client: AsyncClient) -> None:
        owner = await _seed_owner()
        agent_id = await _seed_agent(owner)
        key = await _seed_key(owner, None)

        resp = await client.post(
            "/api/me/api-keys", headers=_bearer(key), json={"name": "child"}
        )
        assert resp.status_code == 201
        assert resp.json()["scopes"] is None
        assert (await client.get("/api/auth/me", headers=_bearer(key))).status_code == 200
        assert (
            await client.delete(f"/api/agents/{agent_id}", headers=_bearer(key))
        ).status_code == 200

    async def test_session_keeps_full_access(self, client: AsyncClient) -> None:
        owner = await _seed_owner()
        agent_id = await _seed_agent(owner)
        token = create_access_token(owner.id, owner.email or "")

        resp = await client.post(
            "/api/me/api-keys", headers=_bearer(token), json={"name": "from-session"}
        )
        assert resp.status_code == 201
        resp = await client.post(
            "/api/me/api-keys",
            headers=_bearer(token),
            json={"name": "chat-only", "scopes": "chat"},
        )
        assert resp.status_code == 201
        assert resp.json()["scopes"] == "chat"
        assert (await client.get("/api/me/api-keys", headers=_bearer(token))).status_code == 200
        assert (
            await client.delete(f"/api/agents/{agent_id}", headers=_bearer(token))
        ).status_code == 200


# ---------------------------------------------------------------------------
# Chat stream endpoints authenticate keys themselves
# ---------------------------------------------------------------------------


class TestChatStreamScope:
    async def test_key_without_chat_scope_cannot_stream(self, db_env: None) -> None:
        owner = await _seed_owner()
        key = await _seed_key(owner, "agents")
        with pytest.raises(AppError) as exc:
            await _resolve_user(key)
        assert exc.value.status_code == 403

    async def test_chat_key_can_stream(self, db_env: None) -> None:
        owner = await _seed_owner()
        key = await _seed_key(owner, "chat")
        principal, *_ = await _resolve_user(key)
        assert principal is not None
        assert principal.user_id == owner.id

    async def test_unrestricted_key_can_stream(self, db_env: None) -> None:
        owner = await _seed_owner()
        key = await _seed_key(owner, None)
        principal, *_ = await _resolve_user(key)
        assert principal is not None
        assert principal.user_id == owner.id

    async def test_the_turn_acts_as_a_principal_that_names_the_key_and_its_scopes(
        self, db_env: None
    ) -> None:
        from fim_one.core.assembly.principal import AuthMethod

        owner = await _seed_owner()
        scoped, *_ = await _resolve_user(await _seed_key(owner, "chat"))
        open_key, *_ = await _resolve_user(await _seed_key(owner, None))
        session, *_ = await _resolve_user(create_access_token(owner.id, owner.email or ""))

        assert scoped is not None and open_key is not None and session is not None
        assert (scoped.auth, scoped.scopes) == (AuthMethod.API_KEY, frozenset({"chat"}))
        assert (open_key.auth, open_key.scopes) == (AuthMethod.API_KEY, None)
        assert (session.auth, session.scopes) == (AuthMethod.SESSION, None)
        assert session.user_id == owner.id and session.is_admin is bool(owner.is_admin)


# ---------------------------------------------------------------------------
# Policy units
# ---------------------------------------------------------------------------


class TestPolicy:
    def test_parse_scopes(self) -> None:
        assert parse_scopes(None) is None
        assert parse_scopes("") is None
        assert parse_scopes("chat, agents") == {"chat", "agents"}
        # A non-empty value that names nothing restricts to nothing; it must
        # never widen to unrestricted.
        assert parse_scopes(",") == set()

    def test_default_deny(self) -> None:
        assert required_scope("GET", "/api/me/api-keys") is None
        assert required_scope("POST", "/api/me/api-keys") is None
        assert required_scope("DELETE", "/api/agents/abc") is None
        assert not scopes_allow({"chat"}, "DELETE", "/api/agents/abc")
        assert not scopes_allow(set(), "POST", "/api/react")

    def test_path_params_do_not_cross_segments(self) -> None:
        assert required_scope("GET", "/api/agents/abc") == "agents"
        assert required_scope("GET", "/api/agents/abc/forks") is None
        assert required_scope("POST", "/api/knowledge-bases/k1/retrieve") == "kb"
        assert required_scope("POST", "/api/knowledge-bases/k1/documents") is None

    def test_subtree_rules(self) -> None:
        assert required_scope("DELETE", "/api/connectors/c1") == "connectors"
        assert required_scope("GET", "/api/connectors") == "connectors"
        assert required_scope("GET", "/api/connectorsx") is None
        assert required_scope("GET", "/api/admin/users") == "admin"

    def test_head_follows_get(self) -> None:
        assert required_scope("HEAD", "/api/agents") == "agents"

    def test_unrestricted_and_session_allowed_everywhere(self) -> None:
        assert scopes_allow(None, "DELETE", "/api/agents/abc")

    def test_delegation(self) -> None:
        check_delegation(None, None)
        check_delegation(None, "chat")
        check_delegation({"chat", "kb"}, "chat")
        check_delegation({"chat", "kb"}, "kb,chat")
        for requested in (None, "", "admin", "chat,admin"):
            with pytest.raises(AppError) as exc:
                check_delegation({"chat", "kb"}, requested)
            assert exc.value.status_code == 403

    def test_every_rule_matches_a_real_route(self, app: FastAPI) -> None:
        """Guard against a rule drifting away from the route it was meant for."""
        schema = get_openapi(title="t", version="0", routes=app.routes)
        operations = [
            (method.upper(), path)
            for path, ops in schema["paths"].items()
            for method in ops
        ]
        for rule in _RULES:
            assert any(
                method in rule.methods and rule.pattern.match(path)
                for method, path in operations
            ), f"scope rule {rule.scope} {rule.pattern.pattern} matches no route"

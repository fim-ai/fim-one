"""Tests for ``GET /api/models/effective/vision``.

The composer warns before an image is attached to a text-only model, so this
endpoint must report exactly what the chat path would decide: agent config
first, then the active model group, then the system default, else ``False``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import jwt as pyjwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import fim_one.db.models  # noqa: F401 — register all models with metadata
from fim_one.db.base import Base
from fim_one.db.models import ModelConfig, User
from fim_one.web.app import create_app


TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
URL = "/api/models/effective/vision"


@pytest_asyncio.fixture()
async def engine() -> AsyncIterator:
    eng = create_async_engine(TEST_DB_URL)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture()
async def db_session(engine) -> AsyncIterator[AsyncSession]:
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture()
async def user(db_session: AsyncSession) -> User:
    u = User(
        id=str(uuid.uuid4()),
        username="composer_user",
        email="composer@example.com",
        password_hash="hashed",
        is_active=True,
    )
    db_session.add(u)
    await db_session.commit()
    return u


def _auth_headers(u: User) -> dict[str, str]:
    from fim_one.web.auth import ALGORITHM, SECRET_KEY

    payload = {
        "sub": u.id,
        "type": "access",
        "exp": datetime.now(UTC) + timedelta(hours=1),
    }
    return {"Authorization": f"Bearer {pyjwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)}"}


@pytest_asyncio.fixture()
async def client(engine, db_session, user):  # noqa: ARG001
    from fim_one.db import get_session

    @asynccontextmanager
    async def _noop_lifespan(app):  # type: ignore[no-untyped-def]
        yield

    with patch("fim_one.web.app.lifespan", _noop_lifespan):
        app = create_app()

    async def _override_session():  # type: ignore[no-untyped-def]
        yield db_session

    app.dependency_overrides[get_session] = _override_session

    @asynccontextmanager
    async def _mock_create_session():  # type: ignore[no-untyped-def]
        yield db_session

    with patch("fim_one.db.create_session", _mock_create_session), \
         patch("fim_one.db.engine.create_session", _mock_create_session):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    app.dependency_overrides.clear()


async def _add_system_default(db_session: AsyncSession, *, supports_vision: bool) -> ModelConfig:
    cfg = ModelConfig(
        id=str(uuid.uuid4()),
        user_id=None,
        name="Default LLM",
        provider="openai",
        model_name="gpt-4o-mini",
        category="llm",
        is_default=True,
        is_active=True,
        supports_vision=supports_vision,
    )
    db_session.add(cfg)
    await db_session.commit()
    return cfg


@pytest.mark.asyncio
async def test_defaults_to_no_vision_when_nothing_is_configured(client, user):
    resp = await client.get(URL, headers=_auth_headers(user))
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {"supports_vision": False}


@pytest.mark.asyncio
async def test_reports_system_default_capability(client, user, db_session):
    await _add_system_default(db_session, supports_vision=True)
    resp = await client.get(URL, headers=_auth_headers(user))
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["supports_vision"] is True


@pytest.mark.asyncio
async def test_text_only_system_default_reports_false(client, user, db_session):
    await _add_system_default(db_session, supports_vision=False)
    resp = await client.get(URL, headers=_auth_headers(user))
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["supports_vision"] is False


@pytest.mark.asyncio
async def test_agent_model_config_wins_over_system_default(client, user, db_session):
    """An agent pinned to a vision model overrides a text-only default."""
    await _add_system_default(db_session, supports_vision=False)
    vision_cfg = ModelConfig(
        id=str(uuid.uuid4()),
        user_id=user.id,
        name="Vision LLM",
        provider="openai",
        model_name="gpt-4o",
        category="llm",
        is_active=True,
        supports_vision=True,
    )
    db_session.add(vision_cfg)
    await db_session.commit()

    agent_cfg = {"model_config_json": {"model_config_id": vision_cfg.id}}
    with patch(
        "fim_one.web.api.chat._resolve_agent_config",
        new=lambda *a, **kw: _as_coroutine(agent_cfg),
    ):
        resp = await client.get(
            URL, headers=_auth_headers(user), params={"agent_id": "agent-1"}
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["supports_vision"] is True


def _as_coroutine(value):  # type: ignore[no-untyped-def]
    async def _inner():  # type: ignore[no-untyped-def]
        return value

    return _inner()


@pytest.mark.asyncio
async def test_requires_authentication(client):
    resp = await client.get(URL)
    assert resp.status_code in (401, 403)

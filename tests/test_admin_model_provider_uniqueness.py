"""Tests for provider-name and model-name uniqueness in the admin model API.

Covers the duplicate-rejection behaviour added to:

- ``POST  /api/admin/model-providers``                       (provider name)
- ``PUT   /api/admin/model-providers/{id}``                  (provider rename)
- ``POST  /api/admin/model-providers/{id}/models``           (model_name)
- ``PUT   /api/admin/model-provider-models/{id}``            (model rename)

A model_name is unique only *within* a provider — the same model_name under a
different provider is allowed.
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
from fim_one.db.models import User
from fim_one.web.app import create_app


TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


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
async def admin_user(db_session: AsyncSession) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username="root_admin",
        email="root@example.com",
        password_hash="hashed",
        is_admin=True,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    return user


def _auth_headers(user: User) -> dict[str, str]:
    from fim_one.web.auth import ALGORITHM, SECRET_KEY

    payload = {
        "sub": user.id,
        "type": "access",
        "exp": datetime.now(UTC) + timedelta(hours=1),
    }
    token = pyjwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture()
async def client(engine, db_session, admin_user):  # noqa: ARG001
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


async def _create_provider(client: AsyncClient, headers: dict[str, str], name: str) -> str:
    resp = await client.post(
        "/api/admin/model-providers",
        headers=headers,
        json={"name": name, "base_url": "https://api.example.com/v1"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Provider name uniqueness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_provider_duplicate_name_rejected(client, admin_user):
    headers = _auth_headers(admin_user)
    await _create_provider(client, headers, "OpenAI")

    resp = await client.post(
        "/api/admin/model-providers",
        headers=headers,
        json={"name": "OpenAI", "base_url": "https://api.other.com/v1"},
    )
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "model_provider_name_taken"


@pytest.mark.asyncio
async def test_rename_provider_to_existing_name_rejected(client, admin_user):
    headers = _auth_headers(admin_user)
    await _create_provider(client, headers, "OpenAI")
    second_id = await _create_provider(client, headers, "Anthropic")

    resp = await client.put(
        f"/api/admin/model-providers/{second_id}",
        headers=headers,
        json={"name": "OpenAI"},
    )
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "model_provider_name_taken"


@pytest.mark.asyncio
async def test_rename_provider_to_same_name_allowed(client, admin_user):
    """Renaming a provider to its own current name must not self-collide."""
    headers = _auth_headers(admin_user)
    pid = await _create_provider(client, headers, "OpenAI")

    resp = await client.put(
        f"/api/admin/model-providers/{pid}",
        headers=headers,
        json={"name": "OpenAI", "is_active": False},
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Model name uniqueness (scoped per provider)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_model_duplicate_id_under_same_provider_rejected(client, admin_user):
    headers = _auth_headers(admin_user)
    pid = await _create_provider(client, headers, "OpenAI")

    body = {"name": "GPT-4o", "model_name": "gpt-4o"}
    first = await client.post(
        f"/api/admin/model-providers/{pid}/models", headers=headers, json=body
    )
    assert first.status_code == 201, first.text

    dup = await client.post(
        f"/api/admin/model-providers/{pid}/models",
        headers=headers,
        json={"name": "GPT-4o (copy)", "model_name": "gpt-4o"},
    )
    assert dup.status_code == 409
    assert dup.json()["error_code"] == "model_name_taken"


@pytest.mark.asyncio
async def test_same_model_id_under_different_provider_allowed(client, admin_user):
    headers = _auth_headers(admin_user)
    p1 = await _create_provider(client, headers, "OpenAI")
    p2 = await _create_provider(client, headers, "Azure")

    body = {"name": "GPT-4o", "model_name": "gpt-4o"}
    r1 = await client.post(
        f"/api/admin/model-providers/{p1}/models", headers=headers, json=body
    )
    r2 = await client.post(
        f"/api/admin/model-providers/{p2}/models", headers=headers, json=body
    )
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 201, r2.text


@pytest.mark.asyncio
async def test_rename_model_to_existing_id_rejected(client, admin_user):
    headers = _auth_headers(admin_user)
    pid = await _create_provider(client, headers, "OpenAI")

    await client.post(
        f"/api/admin/model-providers/{pid}/models",
        headers=headers,
        json={"name": "GPT-4o", "model_name": "gpt-4o"},
    )
    second = await client.post(
        f"/api/admin/model-providers/{pid}/models",
        headers=headers,
        json={"name": "GPT-4o mini", "model_name": "gpt-4o-mini"},
    )
    second_id = second.json()["id"]

    resp = await client.put(
        f"/api/admin/model-provider-models/{second_id}",
        headers=headers,
        json={"model_name": "gpt-4o"},
    )
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "model_name_taken"

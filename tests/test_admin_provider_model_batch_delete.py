"""Tests for batch deletion of provider models.

Covers ``POST /api/admin/model-provider-models/batch-delete``: only the
requested models are removed, unknown ids are reported instead of failing the
whole request, and the payload is bounded.
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

BATCH_DELETE_URL = "/api/admin/model-provider-models/batch-delete"


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
    return str(resp.json()["id"])


async def _create_model(
    client: AsyncClient, headers: dict[str, str], provider_id: str, model_name: str
) -> str:
    resp = await client.post(
        f"/api/admin/model-providers/{provider_id}/models",
        headers=headers,
        json={"name": model_name.upper(), "model_name": model_name},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _remaining_model_names(client: AsyncClient, headers: dict[str, str]) -> set[str]:
    resp = await client.get("/api/admin/model-providers", headers=headers)
    assert resp.status_code == 200, resp.text
    return {
        m["model_name"] for p in resp.json()["providers"] for m in p["models"]
    }


@pytest.mark.asyncio
async def test_batch_delete_removes_only_selected_models(client, admin_user):
    headers = _auth_headers(admin_user)
    pid = await _create_provider(client, headers, "OpenAI")
    first = await _create_model(client, headers, pid, "gpt-4o")
    second = await _create_model(client, headers, pid, "gpt-4o-mini")
    await _create_model(client, headers, pid, "o3")

    resp = await client.post(
        BATCH_DELETE_URL, headers=headers, json={"ids": [first, second]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": 2, "not_found": []}
    assert await _remaining_model_names(client, headers) == {"o3"}


@pytest.mark.asyncio
async def test_batch_delete_spans_providers(client, admin_user):
    headers = _auth_headers(admin_user)
    p1 = await _create_provider(client, headers, "OpenAI")
    p2 = await _create_provider(client, headers, "Anthropic")
    m1 = await _create_model(client, headers, p1, "gpt-4o")
    m2 = await _create_model(client, headers, p2, "claude-sonnet-5")

    resp = await client.post(BATCH_DELETE_URL, headers=headers, json={"ids": [m1, m2]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == 2
    assert await _remaining_model_names(client, headers) == set()


@pytest.mark.asyncio
async def test_batch_delete_reports_unknown_ids(client, admin_user):
    """An id that is already gone must not abort the rest of the batch."""
    headers = _auth_headers(admin_user)
    pid = await _create_provider(client, headers, "OpenAI")
    existing = await _create_model(client, headers, pid, "gpt-4o")
    await _create_model(client, headers, pid, "o3")

    resp = await client.post(
        BATCH_DELETE_URL, headers=headers, json={"ids": [existing, "does-not-exist"]}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] == 1
    assert body["not_found"] == ["does-not-exist"]
    assert await _remaining_model_names(client, headers) == {"o3"}


@pytest.mark.asyncio
async def test_batch_delete_rejects_empty_id_list(client, admin_user):
    headers = _auth_headers(admin_user)
    resp = await client.post(BATCH_DELETE_URL, headers=headers, json={"ids": []})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_batch_delete_requires_admin(client, db_session):
    plain_user = User(
        id=str(uuid.uuid4()),
        username="plain",
        email="plain@example.com",
        password_hash="hashed",
        is_admin=False,
        is_active=True,
    )
    db_session.add(plain_user)
    await db_session.commit()

    resp = await client.post(
        BATCH_DELETE_URL,
        headers=_auth_headers(plain_user),
        json={"ids": [str(uuid.uuid4())]},
    )
    assert resp.status_code == 403

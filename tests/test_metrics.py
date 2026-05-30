"""Tests for the admin metrics/analytics API (src/fim_one/web/api/metrics.py).

Uses an in-memory SQLite database via the async test fixtures to validate:
- Correct response shape
- Period filtering
- Empty-data returns zeros
- Admin-only access enforcement
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from fim_one.db.base import Base
from fim_one.web.app import create_app
from fim_one.web.models import (
    Connector,
    ConnectorCallLog,
    Conversation,
    User,
    Workflow,
    WorkflowRun,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture()
async def engine():
    eng = create_async_engine(TEST_DB_URL)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture()
async def db_session(engine):
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session


@pytest_asyncio.fixture()
async def admin_user(db_session: AsyncSession) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username="admin_test",
        email="admin@test.com",
        password_hash="hashed",
        is_admin=True,
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest_asyncio.fixture()
async def regular_user(db_session: AsyncSession) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username="user_test",
        email="user@test.com",
        password_hash="hashed",
        is_admin=False,
    )
    db_session.add(user)
    await db_session.commit()
    return user


def _create_jwt(user_id: str) -> str:
    """Create a minimal JWT for testing — signed with the project's secret."""
    from fim_one.web.auth import SECRET_KEY, ALGORITHM

    import jwt as pyjwt

    payload = {
        "sub": user_id,
        "type": "access",
        "exp": datetime.now(UTC) + timedelta(hours=1),
    }
    return pyjwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


@pytest_asyncio.fixture()
async def client(engine, db_session, admin_user):  # noqa: ARG001 — admin_user ensures DB has the user
    """HTTPX async client wired to the FastAPI app with a test DB override."""
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, patch

    from fim_one.db import get_session

    # Patch init_db/shutdown_db so lifespan doesn't try to init a real DB
    @asynccontextmanager
    async def _noop_lifespan(app):
        yield

    with patch("fim_one.web.app.lifespan", _noop_lifespan):
        app = create_app()

    async def _override_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_session

    # Also patch create_session used by the maintenance middleware
    @asynccontextmanager
    async def _mock_create_session():
        yield db_session

    with patch("fim_one.db.create_session", _mock_create_session), \
         patch("fim_one.db.engine.create_session", _mock_create_session):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    app.dependency_overrides.clear()


def _auth_headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {_create_jwt(user.id)}"}


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_conversations(
    db: AsyncSession, user: User, count: int = 5, *, days_ago: int = 0
) -> list[Conversation]:
    convs = []
    for i in range(count):
        c = Conversation(
            id=str(uuid.uuid4()),
            user_id=user.id,
            title=f"conv-{i}",
            mode="chat",
            total_tokens=1000 * (i + 1),
            fast_llm_tokens=200 * (i + 1),
            model_name="claude-3-sonnet",
        )
        # Manually set created_at for period filtering tests
        if days_ago > 0:
            c.created_at = datetime.now(UTC) - timedelta(days=days_ago)  # type: ignore[assignment]
        convs.append(c)
        db.add(c)
    await db.commit()
    return convs


async def _seed_workflow_runs(
    db: AsyncSession, user: User, count: int = 3
) -> tuple[Workflow, list[WorkflowRun]]:
    wf = Workflow(
        id=str(uuid.uuid4()),
        user_id=user.id,
        name="Test Workflow",
        blueprint={"nodes": [], "edges": [], "viewport": {}},
    )
    db.add(wf)
    runs = []
    for i in range(count):
        r = WorkflowRun(
            id=str(uuid.uuid4()),
            workflow_id=wf.id,
            user_id=user.id,
            blueprint_snapshot=wf.blueprint,
            status="completed" if i % 2 == 0 else "failed",
            duration_ms=1000 * (i + 1),
            started_at=datetime.now(UTC) - timedelta(minutes=10),
            completed_at=datetime.now(UTC),
        )
        runs.append(r)
        db.add(r)
    await db.commit()
    return wf, runs


async def _seed_connector_calls(
    db: AsyncSession, user: User, count: int = 4
) -> list[ConnectorCallLog]:
    conn = Connector(
        id=str(uuid.uuid4()),
        user_id=user.id,
        name="Test Connector",
        type="api",
    )
    db.add(conn)
    logs = []
    for i in range(count):
        log = ConnectorCallLog(
            id=str(uuid.uuid4()),
            connector_id=conn.id,
            connector_name=conn.name,
            action_name=f"action-{i}",
            request_method="GET",
            request_url="https://example.com/api",
            response_status=200 if i % 3 != 0 else 500,
            response_time_ms=100 + i * 50,
            success=i % 3 != 0,
        )
        logs.append(log)
        db.add(log)
    await db.commit()
    return logs


# ---------------------------------------------------------------------------
# Tests: Overview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overview_returns_expected_shape(
    client: AsyncClient, db_session: AsyncSession, admin_user: User
):
    """Overview endpoint returns all required keys with correct types."""
    await _seed_conversations(db_session, admin_user, count=3)
    await _seed_workflow_runs(db_session, admin_user, count=4)
    await _seed_connector_calls(db_session, admin_user, count=5)

    resp = await client.get(
        "/api/metrics/overview?period=30d",
        headers=_auth_headers(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]

    # Top-level keys
    assert "conversations" in data
    assert "workflow_runs" in data
    assert "connector_calls" in data
    assert "token_usage" in data
    assert "active_users" in data

    # Conversations
    assert isinstance(data["conversations"]["total"], int)
    assert isinstance(data["conversations"]["active_today"], int)

    # Workflow runs
    assert isinstance(data["workflow_runs"]["total"], int)
    assert isinstance(data["workflow_runs"]["success_rate"], float)
    assert isinstance(data["workflow_runs"]["avg_duration_ms"], float)

    # Connector calls
    assert isinstance(data["connector_calls"]["total"], int)
    assert isinstance(data["connector_calls"]["failure_rate"], float)

    # Token usage
    assert isinstance(data["token_usage"]["total_input"], int)
    assert isinstance(data["token_usage"]["total_output"], int)


@pytest.mark.asyncio
async def test_overview_empty_data_returns_zeros(
    client: AsyncClient, admin_user: User
):
    """Overview with no data in DB returns zero/empty values, not errors."""
    resp = await client.get(
        "/api/metrics/overview?period=7d",
        headers=_auth_headers(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]

    assert data["conversations"]["total"] == 0
    assert data["conversations"]["active_today"] == 0
    assert data["workflow_runs"]["total"] == 0
    assert data["workflow_runs"]["success_rate"] == 0.0
    assert data["workflow_runs"]["avg_duration_ms"] == 0.0
    assert data["connector_calls"]["total"] == 0
    assert data["connector_calls"]["failure_rate"] == 0.0
    assert data["token_usage"]["total_input"] == 0
    assert data["token_usage"]["total_output"] == 0
    assert data["active_users"] == 0


@pytest.mark.asyncio
async def test_overview_period_filtering(
    client: AsyncClient, db_session: AsyncSession, admin_user: User
):
    """Conversations created outside the period window are excluded."""
    # Seed conversations: 2 recent, 3 from 10 days ago
    await _seed_conversations(db_session, admin_user, count=2, days_ago=0)
    await _seed_conversations(db_session, admin_user, count=3, days_ago=10)

    # 24h should only include recent (2)
    resp = await client.get(
        "/api/metrics/overview?period=24h",
        headers=_auth_headers(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["conversations"]["total"] == 2

    # 30d should include all (5)
    resp = await client.get(
        "/api/metrics/overview?period=30d",
        headers=_auth_headers(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["conversations"]["total"] == 5


# ---------------------------------------------------------------------------
# Tests: Token Usage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_usage_returns_expected_shape(
    client: AsyncClient, db_session: AsyncSession, admin_user: User
):
    await _seed_conversations(db_session, admin_user, count=3)

    resp = await client.get(
        "/api/metrics/token-usage?period=30d",
        headers=_auth_headers(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]

    assert "daily" in data
    assert "by_model" in data
    assert "total" in data
    assert isinstance(data["daily"], list)
    assert isinstance(data["by_model"], dict)
    assert "input" in data["total"]
    assert "output" in data["total"]


@pytest.mark.asyncio
async def test_token_usage_empty(client: AsyncClient, admin_user: User):
    resp = await client.get(
        "/api/metrics/token-usage?period=7d",
        headers=_auth_headers(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["daily"] == []
    assert data["by_model"] == {}
    assert data["total"]["input"] == 0
    assert data["total"]["output"] == 0


# ---------------------------------------------------------------------------
# Tests: Connector Usage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connector_usage_returns_expected_shape(
    client: AsyncClient, db_session: AsyncSession, admin_user: User
):
    await _seed_connector_calls(db_session, admin_user, count=6)

    resp = await client.get(
        "/api/metrics/connector-usage?period=30d",
        headers=_auth_headers(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]

    assert "connectors" in data
    assert isinstance(data["connectors"], list)
    assert len(data["connectors"]) > 0

    item = data["connectors"][0]
    assert "connector_id" in item
    assert "connector_name" in item
    assert "total_calls" in item
    assert "success_rate" in item
    assert "avg_latency_ms" in item
    assert "last_called_at" in item


@pytest.mark.asyncio
async def test_connector_usage_empty(client: AsyncClient, admin_user: User):
    resp = await client.get(
        "/api/metrics/connector-usage?period=7d",
        headers=_auth_headers(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["connectors"] == []


# ---------------------------------------------------------------------------
# Tests: Workflow Performance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workflow_performance_returns_expected_shape(
    client: AsyncClient, db_session: AsyncSession, admin_user: User
):
    await _seed_workflow_runs(db_session, admin_user, count=5)

    resp = await client.get(
        "/api/metrics/workflow-performance?period=30d",
        headers=_auth_headers(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]

    assert "workflows" in data
    assert isinstance(data["workflows"], list)
    assert len(data["workflows"]) > 0

    item = data["workflows"][0]
    assert "workflow_id" in item
    assert "name" in item
    assert "total_runs" in item
    assert "success_rate" in item
    assert "avg_duration_ms" in item
    assert "p95_duration_ms" in item


@pytest.mark.asyncio
async def test_workflow_performance_empty(client: AsyncClient, admin_user: User):
    resp = await client.get(
        "/api/metrics/workflow-performance?period=7d",
        headers=_auth_headers(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["workflows"] == []


# ---------------------------------------------------------------------------
# Tests: Admin-only access
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_admin_gets_403(
    client: AsyncClient, db_session: AsyncSession, regular_user: User
):
    """Non-admin users should receive a 403 on all metrics endpoints."""
    headers = _auth_headers(regular_user)

    for path in [
        "/api/metrics/overview",
        "/api/metrics/token-usage",
        "/api/metrics/connector-usage",
        "/api/metrics/workflow-performance",
    ]:
        resp = await client.get(path, headers=headers)
        assert resp.status_code == 403, f"{path} should be admin-only"


@pytest.mark.asyncio
async def test_unauthenticated_gets_401(client: AsyncClient):
    """Requests without auth headers should receive a 401."""
    for path in [
        "/api/metrics/overview",
        "/api/metrics/token-usage",
        "/api/metrics/connector-usage",
        "/api/metrics/workflow-performance",
    ]:
        resp = await client.get(path)
        assert resp.status_code == 401, f"{path} should require auth"

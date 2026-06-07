"""Integration tests for ``GET /api/me/usage`` quota-window fields.

Proves end-to-end that the endpoint exposes ``window_tokens`` / ``reset_at``
and that usage is measured over the billing-aligned quota window — the
same interval chat.py enforces against — rather than the calendar month:

- Free user (no subscription) → calendar-month window; ``reset_at`` is the
  first of next month.
- Paid user (subscription anchored mid-month) → anniversary window; usage
  recorded before the current window start is excluded from
  ``window_tokens``.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from fim_one.db.base import Base
from fim_one.db.models import BillingPlan, Conversation, Subscription, User
from fim_one.web.app import create_app

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
async def user(db_session: AsyncSession) -> User:
    u = User(
        id=str(uuid.uuid4()),
        username="usage_test",
        email="usage@test.com",
        password_hash="hashed",
        is_admin=False,
        # Admin override so quota resolves without the billing flag —
        # keeps the test focused on the *window*, not the quota chain.
        token_quota=1_000_000,
    )
    db_session.add(u)
    await db_session.commit()
    return u


@pytest_asyncio.fixture()
async def client(engine, db_session, user):  # noqa: ARG001
    @asynccontextmanager
    async def _noop_lifespan(app):
        yield

    with patch("fim_one.web.app.lifespan", _noop_lifespan):
        app = create_app()

    from fim_one.db import get_session

    async def _override_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_session

    @asynccontextmanager
    async def _mock_create_session():
        yield db_session

    with patch("fim_one.db.create_session", _mock_create_session), patch(
        "fim_one.db.engine.create_session", _mock_create_session
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    app.dependency_overrides.clear()


def _auth_headers(user: User) -> dict[str, str]:
    from fim_one.web.auth import ALGORITHM, SECRET_KEY

    import jwt as pyjwt

    token = pyjwt.encode(
        {"sub": user.id, "type": "access", "exp": datetime.now(UTC) + timedelta(hours=1)},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    return {"Authorization": f"Bearer {token}"}


async def _add_conversation(
    db: AsyncSession, user: User, *, tokens: int, days_ago: int
) -> None:
    c = Conversation(
        id=str(uuid.uuid4()),
        user_id=user.id,
        title="t",
        mode="chat",
        total_tokens=tokens,
    )
    c.created_at = datetime.now(UTC) - timedelta(days=days_ago)  # type: ignore[assignment]
    db.add(c)
    await db.commit()


@pytest.mark.asyncio
async def test_usage_exposes_window_fields_for_free_user(
    client: AsyncClient, db_session: AsyncSession, user: User
) -> None:
    # A conversation earlier this calendar month.
    await _add_conversation(db_session, user, tokens=5000, days_ago=2)

    resp = await client.get("/api/me/usage?period=month", headers=_auth_headers(user))
    assert resp.status_code == 200
    body = resp.json()

    # New fields are present and populated.
    assert "window_tokens" in body
    assert "reset_at" in body
    assert body["window_tokens"] >= 5000

    # Free user → calendar-month window → reset on the 1st of next month.
    reset = datetime.fromisoformat(body["reset_at"])
    assert reset.day == 1
    assert reset > datetime.now(UTC)

    # Percentage is measured against the window, not the stats period.
    assert body["quota"] == 1_000_000
    assert body["quota_used_pct"] == pytest.approx(
        body["window_tokens"] / 1_000_000 * 100, rel=1e-3
    )


@pytest.mark.asyncio
async def test_window_excludes_usage_before_subscription_anniversary(
    client: AsyncClient, db_session: AsyncSession, user: User
) -> None:
    now = datetime.now(UTC)
    plan = BillingPlan(
        slug="pro",
        name="Pro",
        stripe_price_id="price_pro_test",
        monthly_token_quota=5_000_000,
    )
    db_session.add(plan)
    await db_session.commit()

    # Anchor the billing window 10 days ago → current window starts then.
    sub = Subscription(
        user_id=user.id,
        plan_id=plan.id,
        stripe_subscription_id="sub_usage_test",
        stripe_price_id="price_pro_test",
        status="active",
        current_period_start=now - timedelta(days=10),
        current_period_end=now + timedelta(days=20),
        updated_at=now,
    )
    db_session.add(sub)
    await db_session.commit()

    # Before the window (should be excluded) and inside it (counted).
    await _add_conversation(db_session, user, tokens=7000, days_ago=20)
    await _add_conversation(db_session, user, tokens=3000, days_ago=5)

    resp = await client.get("/api/me/usage?period=month", headers=_auth_headers(user))
    assert resp.status_code == 200
    body = resp.json()

    # Only the in-window conversation counts toward the quota window.
    assert body["window_tokens"] == 3000
    # reset_at is the monthly anniversary (~20 days out), not the 1st.
    reset = datetime.fromisoformat(body["reset_at"])
    assert reset > now
    assert reset - now <= timedelta(days=32)

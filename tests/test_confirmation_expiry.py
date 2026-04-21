"""Tests for ``ConfirmationRequestExpirer``."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import fim_one.web.models  # noqa: F401
from fim_one.core.channels.expiry import ConfirmationRequestExpirer
from fim_one.db.base import Base
from fim_one.web.models.channel import Channel, ConfirmationRequest
from fim_one.web.models.organization import Organization
from fim_one.web.models.user import User


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def seed(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, str]:
    async with session_factory() as db:
        org_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        ch_id = str(uuid.uuid4())
        db.add(
            User(
                id=user_id,
                email="u@example.com",
                password_hash="x",
                username="u",
            )
        )
        db.add(
            Organization(id=org_id, name="Org", slug="org", owner_id=user_id)
        )
        db.add(
            Channel(
                id=ch_id,
                name="CH",
                type="feishu",
                org_id=org_id,
                created_by=user_id,
                config={"app_id": "cli_x", "app_secret": "s"},
            )
        )
        await db.commit()
        return {"org_id": org_id, "user_id": user_id, "channel_id": ch_id}


class TestSweep:
    @pytest.mark.asyncio
    async def test_expires_old_pending_rows(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        seed: dict[str, str],
    ) -> None:
        async with session_factory() as db:
            fresh_id = str(uuid.uuid4())
            stale_id = str(uuid.uuid4())
            now = datetime.now(UTC)
            db.add(
                ConfirmationRequest(
                    id=fresh_id,
                    org_id=seed["org_id"],
                    channel_id=seed["channel_id"],
                    status="pending",
                    created_at=now - timedelta(minutes=5),
                )
            )
            db.add(
                ConfirmationRequest(
                    id=stale_id,
                    org_id=seed["org_id"],
                    channel_id=seed["channel_id"],
                    status="pending",
                    created_at=now - timedelta(hours=48),
                )
            )
            await db.commit()

        expirer = ConfirmationRequestExpirer(max_age_minutes=60 * 24)
        async with session_factory() as db:
            expired = await expirer.sweep(db)
        assert expired == 1

        async with session_factory() as db:
            fresh = (
                await db.execute(
                    select(ConfirmationRequest).where(
                        ConfirmationRequest.id == fresh_id
                    )
                )
            ).scalar_one()
            stale = (
                await db.execute(
                    select(ConfirmationRequest).where(
                        ConfirmationRequest.id == stale_id
                    )
                )
            ).scalar_one()
        assert fresh.status == "pending"
        assert stale.status == "expired"
        assert stale.responded_at is not None

    @pytest.mark.asyncio
    async def test_leaves_terminal_rows_alone(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        seed: dict[str, str],
    ) -> None:
        """Rows already approved/rejected should never be touched,
        even if they're older than the TTL."""
        async with session_factory() as db:
            approved_id = str(uuid.uuid4())
            rejected_id = str(uuid.uuid4())
            old = datetime.now(UTC) - timedelta(hours=48)
            db.add(
                ConfirmationRequest(
                    id=approved_id,
                    org_id=seed["org_id"],
                    channel_id=seed["channel_id"],
                    status="approved",
                    created_at=old,
                    responded_at=old,
                )
            )
            db.add(
                ConfirmationRequest(
                    id=rejected_id,
                    org_id=seed["org_id"],
                    channel_id=seed["channel_id"],
                    status="rejected",
                    created_at=old,
                    responded_at=old,
                )
            )
            await db.commit()

        expirer = ConfirmationRequestExpirer(max_age_minutes=60 * 24)
        async with session_factory() as db:
            expired = await expirer.sweep(db)
        assert expired == 0

        async with session_factory() as db:
            a = (
                await db.execute(
                    select(ConfirmationRequest).where(
                        ConfirmationRequest.id == approved_id
                    )
                )
            ).scalar_one()
            r = (
                await db.execute(
                    select(ConfirmationRequest).where(
                        ConfirmationRequest.id == rejected_id
                    )
                )
            ).scalar_one()
        assert a.status == "approved"
        assert r.status == "rejected"

    @pytest.mark.asyncio
    async def test_sweep_is_idempotent(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        seed: dict[str, str],
    ) -> None:
        """Second sweep on the same data should return zero — the
        previously-expired rows are no longer ``pending`` and don't
        re-enter the sweep's predicate."""
        async with session_factory() as db:
            stale_id = str(uuid.uuid4())
            db.add(
                ConfirmationRequest(
                    id=stale_id,
                    org_id=seed["org_id"],
                    channel_id=seed["channel_id"],
                    status="pending",
                    created_at=datetime.now(UTC) - timedelta(hours=48),
                )
            )
            await db.commit()

        expirer = ConfirmationRequestExpirer(max_age_minutes=60 * 24)
        async with session_factory() as db:
            first = await expirer.sweep(db)
            second = await expirer.sweep(db)
        assert first == 1
        assert second == 0

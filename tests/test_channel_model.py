"""ORM-level tests for Channel and ConfirmationRequest.

Verifies that:
- Tables are created via ``Base.metadata.create_all()``.
- ``config`` is encrypted at rest (ciphertext stored, dict returned).
- Basic CRUD works end-to-end on an in-memory SQLite database.
"""

from __future__ import annotations

import uuid
from typing import AsyncIterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401  # populate Base.metadata
from fim_one.db.base import Base
from fim_one.db.models.channel import Channel, ConfirmationRequest
from fim_one.db.models.organization import Organization
from fim_one.db.models.user import User


@pytest.fixture(autouse=True)
def _stable_cred_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin CREDENTIAL_ENCRYPTION_KEY so encrypt/decrypt round-trips."""
    import fim_one.core.security.encryption as enc

    monkeypatch.setenv(
        "CREDENTIAL_ENCRYPTION_KEY", "test-channel-key-1234-abcdef-ghijkl-mn"
    )
    enc._CREDENTIAL_KEY_RAW = "test-channel-key-1234-abcdef-ghijkl-mn"
    enc._cred_fernet_instance = None


@pytest.fixture()
async def async_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture()
async def owner(async_session: AsyncSession) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username="owner",
        email="owner@test.com",
        is_admin=False,
    )
    async_session.add(user)
    await async_session.commit()
    return user


@pytest.fixture()
async def org(async_session: AsyncSession, owner: User) -> Organization:
    o = Organization(
        id=str(uuid.uuid4()),
        name="Acme",
        slug=f"acme-{uuid.uuid4().hex[:6]}",
        owner_id=owner.id,
    )
    async_session.add(o)
    await async_session.commit()
    return o


class TestChannelCrud:
    @pytest.mark.asyncio
    async def test_create_and_round_trip(
        self,
        async_session: AsyncSession,
        owner: User,
        org: Organization,
    ) -> None:
        ch = Channel(
            name="Feishu Ops",
            type="feishu",
            org_id=org.id,
            created_by=owner.id,
            config={"app_id": "cli_x", "app_secret": "secret", "chat_id": "oc_x"},
        )
        async_session.add(ch)
        await async_session.commit()

        stmt = select(Channel).where(Channel.org_id == org.id)
        loaded = (await async_session.execute(stmt)).scalar_one()
        assert loaded.name == "Feishu Ops"
        assert loaded.type == "feishu"
        assert loaded.config["app_secret"] == "secret"
        assert loaded.is_active is True

    @pytest.mark.asyncio
    async def test_config_encrypted_at_rest(
        self,
        async_session: AsyncSession,
        owner: User,
        org: Organization,
    ) -> None:
        ch = Channel(
            name="F",
            type="feishu",
            org_id=org.id,
            created_by=owner.id,
            config={"app_secret": "super-secret-value-abc"},
        )
        async_session.add(ch)
        await async_session.commit()

        # Raw SQL read — bypass the TypeDecorator to see the ciphertext.
        raw = await async_session.execute(
            text("SELECT config FROM channels WHERE id = :id"),
            {"id": ch.id},
        )
        stored = raw.scalar_one()
        # Must NOT be plaintext JSON.
        assert "super-secret-value-abc" not in str(stored)


class TestConfirmationRequestCrud:
    @pytest.mark.asyncio
    async def test_default_status_pending(
        self,
        async_session: AsyncSession,
        owner: User,
        org: Organization,
    ) -> None:
        ch = Channel(
            name="F",
            type="feishu",
            org_id=org.id,
            created_by=owner.id,
            config={"app_id": "x"},
        )
        async_session.add(ch)
        await async_session.commit()

        req = ConfirmationRequest(
            tool_call_id="tc-1",
            agent_id="agent-1",
            user_id=owner.id,
            org_id=org.id,
            channel_id=ch.id,
            payload={"tool_name": "oa__purchase_pay", "args": {"amount": 500}},
        )
        async_session.add(req)
        await async_session.commit()

        loaded = (
            await async_session.execute(
                select(ConfirmationRequest).where(
                    ConfirmationRequest.tool_call_id == "tc-1"
                )
            )
        ).scalar_one()
        assert loaded.status == "pending"
        assert loaded.payload is not None
        assert loaded.payload["tool_name"] == "oa__purchase_pay"

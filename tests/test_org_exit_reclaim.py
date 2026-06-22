"""Leaving / being removed from an org revokes that org's subscriptions.

Covers :func:`fim_one.web.api.market.reclaim_org_subscriptions` (wired into
``organizations.remove_member``): a user's org-scoped resource subscriptions
and the per-user credentials they stored for those resources are deleted, so
they lose access and can no longer fall back to the owner's credential.
Market-published subscriptions (decision D4) are left untouched.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.db.base import Base
from fim_one.db.models.connector import Connector
from fim_one.db.models.connector_credential import ConnectorCredential
from fim_one.db.models.mcp_server import MCPServer
from fim_one.db.models.mcp_server_credential import MCPServerCredential
from fim_one.db.models.resource_subscription import ResourceSubscription
from fim_one.db.models.user import User
from fim_one.web.api.market import reclaim_org_subscriptions
from fim_one.web.platform import MARKET_ORG_ID

ORG_ID = "org-under-test"


@pytest.fixture(autouse=True)
def _cred_key(monkeypatch: pytest.MonkeyPatch) -> Any:
    import fim_one.core.security.encryption as enc

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "test-reclaim-key-1234567890")
    enc._CREDENTIAL_KEY_RAW = "test-reclaim-key-1234567890"
    enc._cred_fernet_instance = None
    yield
    enc._cred_fernet_instance = None


@pytest.fixture()
async def async_session() -> AsyncIterator[AsyncSession]:
    import fim_one.db.models  # noqa: F401 — register all models

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn: Any, _record: Any) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_reclaim_revokes_org_subs_and_credentials_but_keeps_market(
    async_session: AsyncSession,
) -> None:
    from fim_one.core.security.encryption import encrypt_credential

    db = async_session
    user = User(id=uuid.uuid4().hex, username="m", email="m@example.com")
    owner = User(id=uuid.uuid4().hex, username="o", email="o@example.com")
    connector = Connector(id=uuid.uuid4().hex, user_id=owner.id, name="Shared DB")
    mcp = MCPServer(id=uuid.uuid4().hex, user_id=owner.id, name="Shared MCP")
    # A separate connector subscribed via the Market (must survive the reclaim).
    market_connector = Connector(id=uuid.uuid4().hex, user_id=owner.id, name="Market DB")
    db.add_all([user, owner, connector, mcp, market_connector])
    await db.commit()  # parents must exist before FK-referencing rows

    # Org-scoped subscriptions (revocable) + a Market subscription (must survive).
    # NOTE: resource_subscriptions is unique on (user, type, resource_id) — a
    # resource is subscribed once — so the Market sub points at a distinct resource.
    org_conn_sub = ResourceSubscription(
        user_id=user.id, resource_type="connector",
        resource_id=connector.id, org_id=ORG_ID,
    )
    org_mcp_sub = ResourceSubscription(
        user_id=user.id, resource_type="mcp_server",
        resource_id=mcp.id, org_id=ORG_ID,
    )
    market_sub = ResourceSubscription(
        user_id=user.id, resource_type="connector",
        resource_id=market_connector.id, org_id=MARKET_ORG_ID,
    )
    # Per-user credentials stored for the org-shared resources.
    conn_cred = ConnectorCredential(
        connector_id=connector.id, user_id=user.id,
        credentials_blob=encrypt_credential({"token": "x"}),
    )
    mcp_cred = MCPServerCredential(server_id=mcp.id, user_id=user.id)
    db.add_all([org_conn_sub, org_mcp_sub, market_sub, conn_cred, mcp_cred])
    await db.commit()

    removed = await reclaim_org_subscriptions(db, user_id=user.id, org_id=ORG_ID)
    await db.commit()

    assert removed == 2

    # Org-scoped subs gone; Market sub survives.
    remaining_subs = (
        await db.execute(
            select(ResourceSubscription.org_id).where(
                ResourceSubscription.user_id == user.id
            )
        )
    ).scalars().all()
    assert remaining_subs == [MARKET_ORG_ID]

    # Per-user credentials for the org-shared resources are gone (no fallback path).
    conn_cred_count = (
        await db.execute(
            select(ConnectorCredential.id).where(
                ConnectorCredential.user_id == user.id
            )
        )
    ).scalars().all()
    mcp_cred_count = (
        await db.execute(
            select(MCPServerCredential.id).where(
                MCPServerCredential.user_id == user.id
            )
        )
    ).scalars().all()
    assert conn_cred_count == []
    assert mcp_cred_count == []


async def test_reclaim_noop_when_no_org_subs(async_session: AsyncSession) -> None:
    db = async_session
    user = User(id=uuid.uuid4().hex, username="n", email="n@example.com")
    db.add(user)
    await db.commit()

    removed = await reclaim_org_subscriptions(db, user_id=user.id, org_id=ORG_ID)
    await db.commit()
    assert removed == 0

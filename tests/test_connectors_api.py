"""Connector endpoint regressions found by end-to-end testing.

- A subscriber (the only way a non-owner sees a shared connector) 404'd on
  ``GET /api/connectors/{id}`` and on every ``/my-credentials`` endpoint,
  because those lookups skipped the subscription list. Org members could
  therefore never store their own token for a shared connector.
- ``POST /api/connectors/import`` stored secret auth fields (``default_token``
  etc.) in plaintext ``auth_config``.
- ``base_url`` scheme validation ran on create only, not on update/import.
- ``circuit-breaker-reset`` accepted any connector id from any user.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.db.base import Base
from fim_one.db.models.connector import Connector
from fim_one.db.models.connector_credential import ConnectorCredential
from fim_one.db.models.resource_subscription import ResourceSubscription
from fim_one.web.api import connectors as api
from fim_one.web.exceptions import AppError
from fim_one.web.schemas.connector import (
    ConnectorImportRequest,
    ConnectorUpdate,
    CredentialUpsertRequest,
)

OWNER = "owner-1"
SUBSCRIBER = "subscriber-1"
STRANGER = "stranger-1"
ORG = "org-1"


@pytest.fixture()
async def db() -> AsyncIterator[AsyncSession]:
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


def _user(user_id: str) -> Any:
    return SimpleNamespace(id=user_id, is_admin=False)


async def _shared_connector(db: AsyncSession) -> str:
    connector_id = str(uuid.uuid4())
    db.add(
        Connector(
            id=connector_id,
            user_id=OWNER,
            name="Shared API",
            type="api",
            base_url="https://api.example.com",
            auth_type="bearer",
            auth_config={"token_prefix": "Bearer"},
            visibility="org",
            org_id=ORG,
        )
    )
    db.add(
        ResourceSubscription(
            user_id=SUBSCRIBER,
            resource_type="connector",
            resource_id=connector_id,
            org_id=ORG,
        )
    )
    await db.commit()
    return connector_id


class TestSubscriberCredentials:
    @pytest.mark.asyncio
    async def test_subscriber_can_read_connector(self, db: AsyncSession) -> None:
        connector_id = await _shared_connector(db)

        resp = await api.get_connector(connector_id, current_user=_user(SUBSCRIBER), db=db)

        assert resp.data is not None
        assert resp.data["id"] == connector_id

    @pytest.mark.asyncio
    async def test_subscriber_can_store_own_token(self, db: AsyncSession) -> None:
        connector_id = await _shared_connector(db)

        await api.upsert_my_credentials(
            connector_id,
            CredentialUpsertRequest(token="sub-token"),
            current_user=_user(SUBSCRIBER),
            db=db,
        )
        status = await api.get_my_credentials(
            connector_id, current_user=_user(SUBSCRIBER), db=db
        )

        assert status.data is not None
        assert status.data["has_credentials"] is True
        row = (
            await db.execute(
                select(ConnectorCredential).where(
                    ConnectorCredential.user_id == SUBSCRIBER
                )
            )
        ).scalar_one()
        assert row.connector_id == connector_id

    @pytest.mark.asyncio
    async def test_non_subscriber_still_404s(self, db: AsyncSession) -> None:
        connector_id = await _shared_connector(db)

        with pytest.raises(AppError) as exc:
            await api.get_my_credentials(
                connector_id, current_user=_user(STRANGER), db=db
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_unshared_connector_hidden_from_former_subscriber(
        self, db: AsyncSession
    ) -> None:
        connector_id = await _shared_connector(db)
        connector = await db.get(Connector, connector_id)
        assert connector is not None
        connector.visibility = "personal"
        connector.org_id = None
        await db.commit()

        with pytest.raises(AppError) as exc:
            await api.get_connector(connector_id, current_user=_user(SUBSCRIBER), db=db)
        assert exc.value.status_code == 404


class TestCircuitBreakerReset:
    @pytest.mark.asyncio
    async def test_stranger_cannot_reset(self, db: AsyncSession) -> None:
        connector_id = await _shared_connector(db)

        with pytest.raises(AppError) as exc:
            await api.reset_circuit_breaker(
                connector_id, current_user=_user(STRANGER), db=db
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_owner_can_reset(self, db: AsyncSession) -> None:
        connector_id = await _shared_connector(db)

        resp = await api.reset_circuit_breaker(
            connector_id, current_user=_user(OWNER), db=db
        )

        assert resp.data is not None
        assert resp.data["connector_id"] == connector_id


class TestImport:
    @pytest.mark.asyncio
    async def test_import_drops_secret_auth_fields(self, db: AsyncSession) -> None:
        body = ConnectorImportRequest(
            name="Imported",
            connector_type="api",
            base_url="https://api.example.com",
            auth_type="bearer",
            auth_config={"token_prefix": "Bearer", "default_token": "leaked"},
        )

        resp = await api.import_connector(body, current_user=_user(OWNER), db=db)

        assert resp.data is not None
        connector_id = resp.data["connector"]["id"]
        stored = await db.get(Connector, connector_id)
        assert stored is not None
        assert stored.auth_config == {"token_prefix": "Bearer"}


class TestBaseUrlScheme:
    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://host/x", "gopher://h"])
    def test_update_rejects_non_http(self, url: str) -> None:
        with pytest.raises(ValidationError):
            ConnectorUpdate(base_url=url)

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://host/x"])
    def test_import_rejects_non_http(self, url: str) -> None:
        with pytest.raises(ValidationError):
            ConnectorImportRequest(name="x", connector_type="api", base_url=url)

    def test_http_and_unset_allowed(self) -> None:
        assert ConnectorUpdate(base_url="https://a.example").base_url == "https://a.example"
        assert ConnectorUpdate().base_url is None
        assert ConnectorImportRequest(name="x", connector_type="database").base_url is None

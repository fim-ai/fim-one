"""Integration tests for the /api/channels endpoints and Feishu callback."""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.web.models  # noqa: F401
from fim_one.core.channels import ChannelSendResult
from fim_one.db.base import Base
from fim_one.web.auth import create_access_token
from fim_one.web.models.channel import Channel, ConfirmationRequest
from fim_one.web.models.organization import Organization, OrgMembership
from fim_one.web.models.user import User


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stable_cred_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import fim_one.core.security.encryption as enc

    monkeypatch.setenv(
        "CREDENTIAL_ENCRYPTION_KEY", "test-api-key-01234567890abcdef"
    )
    enc._CREDENTIAL_KEY_RAW = "test-api-key-01234567890abcdef"
    enc._cred_fernet_instance = None
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-01234567890")


@pytest.fixture()
async def engine() -> AsyncIterator[Any]:
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture()
async def session_factory(engine: Any) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture()
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    """FastAPI test client with DB dependency overridden."""
    from fim_one.web.api.channels import router
    from fastapi import FastAPI
    from fim_one.db import get_session

    app = FastAPI()
    app.include_router(router)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture()
async def seed(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, Any]:
    """Create user + org + org-owner membership."""
    async with session_factory() as db:
        user = User(
            id=str(uuid.uuid4()),
            username="admin",
            email="admin@test.com",
            is_admin=False,
        )
        db.add(user)
        org = Organization(
            id=str(uuid.uuid4()),
            name="Acme",
            slug=f"acme-{uuid.uuid4().hex[:6]}",
            owner_id=user.id,
        )
        db.add(org)
        membership = OrgMembership(
            id=str(uuid.uuid4()),
            org_id=org.id,
            user_id=user.id,
            role="owner",
        )
        db.add(membership)
        await db.commit()

    token = create_access_token(user.id, user.email or "")
    return {
        "user_id": user.id,
        "org_id": org.id,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }


@pytest.fixture()
async def outsider(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, Any]:
    """A second user NOT in the seed org."""
    async with session_factory() as db:
        user = User(
            id=str(uuid.uuid4()),
            username="outsider",
            email="o@test.com",
            is_admin=False,
        )
        db.add(user)
        await db.commit()
    token = create_access_token(user.id, user.email or "")
    return {
        "user_id": user.id,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestCreateChannel:
    @pytest.mark.asyncio
    async def test_owner_can_create(
        self, client: AsyncClient, seed: dict[str, Any]
    ) -> None:
        payload = {
            "name": "Feishu Ops",
            "type": "feishu",
            "org_id": seed["org_id"],
            "config": {
                "app_id": "cli_x",
                "app_secret": "shh",
                "chat_id": "oc_1",
            },
            "is_active": True,
        }
        resp = await client.post(
            "/api/channels", json=payload, headers=seed["headers"]
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "Feishu Ops"
        assert body["type"] == "feishu"
        # app_secret must be masked in responses.
        assert body["config"]["app_secret"] == "***"
        assert body["config"]["app_id"] == "cli_x"
        assert body["callback_url"].endswith(f"/api/channels/{body['id']}/callback")

    @pytest.mark.asyncio
    async def test_outsider_cannot_create(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        outsider: dict[str, Any],
    ) -> None:
        payload = {
            "name": "X",
            "type": "feishu",
            "org_id": seed["org_id"],
            "config": {},
        }
        resp = await client.post(
            "/api/channels", json=payload, headers=outsider["headers"]
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_unauthenticated_rejected(
        self, client: AsyncClient, seed: dict[str, Any]
    ) -> None:
        resp = await client.post(
            "/api/channels",
            json={"name": "x", "type": "feishu", "org_id": seed["org_id"]},
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_invalid_type_rejected(
        self, client: AsyncClient, seed: dict[str, Any]
    ) -> None:
        resp = await client.post(
            "/api/channels",
            json={
                "name": "x",
                "type": "telegram",  # not in enum
                "org_id": seed["org_id"],
            },
            headers=seed["headers"],
        )
        assert resp.status_code == 422


class TestListAndGetChannel:
    @pytest.mark.asyncio
    async def test_list_filters_by_org(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with session_factory() as db:
            db.add(
                Channel(
                    id=str(uuid.uuid4()),
                    name="A",
                    type="feishu",
                    org_id=seed["org_id"],
                    created_by=seed["user_id"],
                    config={"chat_id": "oc_1"},
                )
            )
            await db.commit()

        resp = await client.get(
            f"/api/channels?org_id={seed['org_id']}",
            headers=seed["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["name"] == "A"

    @pytest.mark.asyncio
    async def test_get_returns_callback_url(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with session_factory() as db:
            ch = Channel(
                id=str(uuid.uuid4()),
                name="B",
                type="feishu",
                org_id=seed["org_id"],
                created_by=seed["user_id"],
                config={"chat_id": "oc_b"},
            )
            db.add(ch)
            await db.commit()

        resp = await client.get(
            f"/api/channels/{ch.id}", headers=seed["headers"]
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["callback_url"].endswith(f"/api/channels/{ch.id}/callback")


class TestUpdateAndDelete:
    @pytest.mark.asyncio
    async def test_patch_merges_config(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with session_factory() as db:
            ch = Channel(
                id=str(uuid.uuid4()),
                name="C",
                type="feishu",
                org_id=seed["org_id"],
                created_by=seed["user_id"],
                config={
                    "app_id": "cli_x",
                    "app_secret": "orig-secret",
                    "chat_id": "oc_old",
                },
            )
            db.add(ch)
            await db.commit()

        resp = await client.patch(
            f"/api/channels/{ch.id}",
            json={"config": {"chat_id": "oc_new"}},
            headers=seed["headers"],
        )
        assert resp.status_code == 200
        # Re-fetch DB row to check the merged config.
        async with session_factory() as db:
            refreshed = (
                await db.execute(select(Channel).where(Channel.id == ch.id))
            ).scalar_one()
            assert refreshed.config["chat_id"] == "oc_new"
            assert refreshed.config["app_secret"] == "orig-secret"
            assert refreshed.config["app_id"] == "cli_x"

    @pytest.mark.asyncio
    async def test_delete(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with session_factory() as db:
            ch = Channel(
                id=str(uuid.uuid4()),
                name="D",
                type="feishu",
                org_id=seed["org_id"],
                created_by=seed["user_id"],
                config={},
            )
            db.add(ch)
            await db.commit()

        resp = await client.delete(
            f"/api/channels/{ch.id}", headers=seed["headers"]
        )
        assert resp.status_code == 204
        async with session_factory() as db:
            row = (
                await db.execute(select(Channel).where(Channel.id == ch.id))
            ).scalar_one_or_none()
            assert row is None


# ---------------------------------------------------------------------------
# Test send
# ---------------------------------------------------------------------------


class TestTestChannel:
    @pytest.mark.asyncio
    async def test_invokes_send_interactive_card(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with session_factory() as db:
            ch = Channel(
                id=str(uuid.uuid4()),
                name="T",
                type="feishu",
                org_id=seed["org_id"],
                created_by=seed["user_id"],
                config={
                    "app_id": "cli_x",
                    "app_secret": "s",
                    "chat_id": "oc_test",
                },
            )
            db.add(ch)
            await db.commit()

        send_mock = AsyncMock(return_value=ChannelSendResult(ok=True))
        with patch(
            "fim_one.core.channels.feishu.FeishuChannel.send_interactive_card",
            new=send_mock,
        ):
            resp = await client.post(
                f"/api/channels/{ch.id}/test", headers=seed["headers"]
            )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        send_mock.assert_awaited_once()
        # Verify chat_id + a card dict (has the expected shape) were passed.
        args, _ = send_mock.await_args
        assert args[0] == "oc_test"
        assert isinstance(args[1], dict)
        assert "elements" in args[1]


# ---------------------------------------------------------------------------
# Feishu callback
# ---------------------------------------------------------------------------


class TestFeishuCallback:
    @pytest.mark.asyncio
    async def test_url_verification_echoes_challenge(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with session_factory() as db:
            ch = Channel(
                id=str(uuid.uuid4()),
                name="CB",
                type="feishu",
                org_id=seed["org_id"],
                created_by=seed["user_id"],
                config={"app_id": "cli_x", "app_secret": "s"},
            )
            db.add(ch)
            await db.commit()

        # No auth header — callback is public.
        resp = await client.post(
            f"/api/channels/{ch.id}/callback",
            json={"type": "url_verification", "challenge": "xyz"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"challenge": "xyz"}

    @pytest.mark.asyncio
    async def test_approve_flips_pending_row(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with session_factory() as db:
            ch = Channel(
                id=str(uuid.uuid4()),
                name="CB2",
                type="feishu",
                org_id=seed["org_id"],
                created_by=seed["user_id"],
                config={"app_id": "cli_x", "app_secret": "s"},
            )
            db.add(ch)
            req = ConfirmationRequest(
                id=str(uuid.uuid4()),
                tool_call_id="tc",
                agent_id=None,
                user_id=seed["user_id"],
                org_id=seed["org_id"],
                channel_id=ch.id,
                status="pending",
                payload={"tool_name": "x"},
            )
            db.add(req)
            await db.commit()

        payload = {
            "action": {
                "value": {
                    "confirmation_id": req.id,
                    "decision": "approve",
                }
            },
            "open_id": "ou_operator",
        }
        resp = await client.post(
            f"/api/channels/{ch.id}/callback", json=payload
        )
        assert resp.status_code == 200

        async with session_factory() as db:
            row = (
                await db.execute(
                    select(ConfirmationRequest).where(
                        ConfirmationRequest.id == req.id
                    )
                )
            ).scalar_one()
            assert row.status == "approved"
            assert row.responded_by_open_id == "ou_operator"
            assert row.responded_at is not None

    @pytest.mark.asyncio
    async def test_reject_flips_pending_row(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with session_factory() as db:
            ch = Channel(
                id=str(uuid.uuid4()),
                name="CB3",
                type="feishu",
                org_id=seed["org_id"],
                created_by=seed["user_id"],
                config={"app_id": "cli_x", "app_secret": "s"},
            )
            db.add(ch)
            req = ConfirmationRequest(
                id=str(uuid.uuid4()),
                tool_call_id="tc2",
                agent_id=None,
                user_id=seed["user_id"],
                org_id=seed["org_id"],
                channel_id=ch.id,
                status="pending",
                payload={"tool_name": "x"},
            )
            db.add(req)
            await db.commit()

        payload = {
            "action": {
                "value": {
                    "confirmation_id": req.id,
                    "decision": "reject",
                }
            },
            "open_id": "ou_x",
        }
        resp = await client.post(
            f"/api/channels/{ch.id}/callback", json=payload
        )
        assert resp.status_code == 200
        async with session_factory() as db:
            row = (
                await db.execute(
                    select(ConfirmationRequest).where(
                        ConfirmationRequest.id == req.id
                    )
                )
            ).scalar_one()
            assert row.status == "rejected"

    @pytest.mark.asyncio
    async def test_double_click_is_idempotent(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with session_factory() as db:
            ch = Channel(
                id=str(uuid.uuid4()),
                name="CB4",
                type="feishu",
                org_id=seed["org_id"],
                created_by=seed["user_id"],
                config={"app_id": "cli_x", "app_secret": "s"},
            )
            db.add(ch)
            req = ConfirmationRequest(
                id=str(uuid.uuid4()),
                tool_call_id="tc3",
                agent_id=None,
                user_id=seed["user_id"],
                org_id=seed["org_id"],
                channel_id=ch.id,
                status="pending",
                payload={},
            )
            db.add(req)
            await db.commit()

        # First click: approve
        payload_approve = {
            "action": {
                "value": {"confirmation_id": req.id, "decision": "approve"}
            },
            "open_id": "ou_a",
        }
        await client.post(
            f"/api/channels/{ch.id}/callback", json=payload_approve
        )
        # Second click: reject should be ignored (already terminal).
        payload_reject = {
            "action": {
                "value": {"confirmation_id": req.id, "decision": "reject"}
            },
            "open_id": "ou_b",
        }
        await client.post(
            f"/api/channels/{ch.id}/callback", json=payload_reject
        )
        async with session_factory() as db:
            row = (
                await db.execute(
                    select(ConfirmationRequest).where(
                        ConfirmationRequest.id == req.id
                    )
                )
            ).scalar_one()
            assert row.status == "approved"
            assert row.responded_by_open_id == "ou_a"

    @pytest.mark.asyncio
    async def test_invalid_signature_rejected(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with session_factory() as db:
            ch = Channel(
                id=str(uuid.uuid4()),
                name="CB5",
                type="feishu",
                org_id=seed["org_id"],
                created_by=seed["user_id"],
                config={
                    "app_id": "cli_x",
                    "app_secret": "s",
                    "encrypt_key": "signing-key",
                },
            )
            db.add(ch)
            await db.commit()

        headers = {
            "X-Lark-Request-Timestamp": "0",
            "X-Lark-Request-Nonce": "n",
            "X-Lark-Signature": "0" * 64,  # wrong
        }
        resp = await client.post(
            f"/api/channels/{ch.id}/callback",
            content=json.dumps({"foo": "bar"}),
            headers={**headers, "Content-Type": "application/json"},
        )
        assert resp.status_code == 401

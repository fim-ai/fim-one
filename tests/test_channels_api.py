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
    async def test_sends_plain_text_no_buttons(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """The /test endpoint is a connectivity sanity check; it must
        deliver a plain text message (no interactive buttons), so users
        who don't use approval hooks aren't confused by Approve/Reject
        CTAs on a supposedly-harmless test."""
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
            "fim_one.core.channels.feishu.FeishuChannel.send_message",
            new=send_mock,
        ):
            resp = await client.post(
                f"/api/channels/{ch.id}/test", headers=seed["headers"]
            )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        send_mock.assert_awaited_once()
        args, _ = send_mock.await_args
        payload = args[0]
        assert payload["chat_id"] == "oc_test"
        assert payload["msg_type"] == "text"
        assert "FIM One test message" in payload["content"]


# ---------------------------------------------------------------------------
# Hook Playground — test-approval + confirmation polling
# ---------------------------------------------------------------------------


async def _seed_active_feishu_channel(
    session_factory: async_sessionmaker[AsyncSession],
    seed: dict[str, Any],
    *,
    is_active: bool = True,
) -> str:
    async with session_factory() as db:
        ch = Channel(
            id=str(uuid.uuid4()),
            name="Playground",
            type="feishu",
            org_id=seed["org_id"],
            created_by=seed["user_id"],
            is_active=is_active,
            config={
                "app_id": "cli_x",
                "app_secret": "s",
                "chat_id": "oc_playground",
            },
        )
        db.add(ch)
        await db.commit()
        return ch.id


class TestApprovalPlayground:
    @pytest.mark.asyncio
    async def test_creates_real_confirmation_and_sends_card(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        ch_id = await _seed_active_feishu_channel(session_factory, seed)

        send_mock = AsyncMock(return_value=ChannelSendResult(ok=True))
        with patch(
            "fim_one.core.channels.feishu.FeishuChannel.send_interactive_card",
            new=send_mock,
        ):
            resp = await client.post(
                f"/api/channels/{ch_id}/test-approval",
                json={
                    "tool_name": "drop_table",
                    "tool_args": {"table": "orders"},
                },
                headers=seed["headers"],
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["confirmation_id"]
        send_mock.assert_awaited_once()

        # A real DB row was created in pending state.
        async with session_factory() as db:
            row = (
                await db.execute(
                    select(ConfirmationRequest).where(
                        ConfirmationRequest.id == body["confirmation_id"]
                    )
                )
            ).scalar_one_or_none()
            assert row is not None
            assert row.status == "pending"
            assert row.channel_id == ch_id
            assert row.payload["tool_name"] == "drop_table"
            assert row.payload["test_mode"] is True

    @pytest.mark.asyncio
    async def test_fills_defaults_when_body_empty(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        ch_id = await _seed_active_feishu_channel(session_factory, seed)

        send_mock = AsyncMock(return_value=ChannelSendResult(ok=True))
        with patch(
            "fim_one.core.channels.feishu.FeishuChannel.send_interactive_card",
            new=send_mock,
        ):
            resp = await client.post(
                f"/api/channels/{ch_id}/test-approval",
                json={},
                headers=seed["headers"],
            )

        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    @pytest.mark.asyncio
    async def test_rejects_when_channel_disabled(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        ch_id = await _seed_active_feishu_channel(
            session_factory, seed, is_active=False
        )

        resp = await client.post(
            f"/api/channels/{ch_id}/test-approval",
            json={},
            headers=seed["headers"],
        )
        body = resp.json()
        assert resp.status_code == 200
        assert body["ok"] is False
        assert "disabled" in (body.get("error") or "").lower()

    @pytest.mark.asyncio
    async def test_outsider_rejected(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        outsider: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        ch_id = await _seed_active_feishu_channel(session_factory, seed)

        resp = await client.post(
            f"/api/channels/{ch_id}/test-approval",
            json={},
            headers=outsider["headers"],
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_marks_expired_on_send_failure(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        ch_id = await _seed_active_feishu_channel(session_factory, seed)

        send_mock = AsyncMock(
            return_value=ChannelSendResult(ok=False, error="network")
        )
        with patch(
            "fim_one.core.channels.feishu.FeishuChannel.send_interactive_card",
            new=send_mock,
        ):
            resp = await client.post(
                f"/api/channels/{ch_id}/test-approval",
                json={},
                headers=seed["headers"],
            )

        body = resp.json()
        assert body["ok"] is False
        assert body["confirmation_id"]

        async with session_factory() as db:
            row = (
                await db.execute(
                    select(ConfirmationRequest).where(
                        ConfirmationRequest.id == body["confirmation_id"]
                    )
                )
            ).scalar_one_or_none()
            assert row is not None
            assert row.status == "expired"


class TestConfirmationStatus:
    @pytest.mark.asyncio
    async def test_returns_current_status(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        ch_id = await _seed_active_feishu_channel(session_factory, seed)

        send_mock = AsyncMock(return_value=ChannelSendResult(ok=True))
        with patch(
            "fim_one.core.channels.feishu.FeishuChannel.send_interactive_card",
            new=send_mock,
        ):
            create = await client.post(
                f"/api/channels/{ch_id}/test-approval",
                json={"tool_name": "foo", "tool_args": {"a": 1}},
                headers=seed["headers"],
            )
        conf_id = create.json()["confirmation_id"]

        resp = await client.get(
            f"/api/channels/{ch_id}/confirmations/{conf_id}",
            headers=seed["headers"],
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == conf_id
        assert body["status"] == "pending"
        assert body["tool_name"] == "foo"
        assert body["test_mode"] is True

    @pytest.mark.asyncio
    async def test_reflects_approval_via_callback(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        ch_id = await _seed_active_feishu_channel(session_factory, seed)

        send_mock = AsyncMock(return_value=ChannelSendResult(ok=True))
        with patch(
            "fim_one.core.channels.feishu.FeishuChannel.send_interactive_card",
            new=send_mock,
        ):
            create = await client.post(
                f"/api/channels/{ch_id}/test-approval",
                json={},
                headers=seed["headers"],
            )
        conf_id = create.json()["confirmation_id"]

        # Simulate the Feishu callback flipping the row to approved.
        async with session_factory() as db:
            row = (
                await db.execute(
                    select(ConfirmationRequest).where(
                        ConfirmationRequest.id == conf_id
                    )
                )
            ).scalar_one()
            row.status = "approved"
            row.responded_by_open_id = "ou_operator_123"
            await db.commit()

        resp = await client.get(
            f"/api/channels/{ch_id}/confirmations/{conf_id}",
            headers=seed["headers"],
        )
        body = resp.json()
        assert body["status"] == "approved"
        assert body["responded_by_open_id"] == "ou_operator_123"

    @pytest.mark.asyncio
    async def test_404_when_not_found(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        ch_id = await _seed_active_feishu_channel(session_factory, seed)
        resp = await client.get(
            f"/api/channels/{ch_id}/confirmations/{uuid.uuid4()}",
            headers=seed["headers"],
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_outsider_rejected(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        outsider: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        ch_id = await _seed_active_feishu_channel(session_factory, seed)

        send_mock = AsyncMock(return_value=ChannelSendResult(ok=True))
        with patch(
            "fim_one.core.channels.feishu.FeishuChannel.send_interactive_card",
            new=send_mock,
        ):
            create = await client.post(
                f"/api/channels/{ch_id}/test-approval",
                json={},
                headers=seed["headers"],
            )
        conf_id = create.json()["confirmation_id"]

        resp = await client.get(
            f"/api/channels/{ch_id}/confirmations/{conf_id}",
            headers=outsider["headers"],
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Discover chats (Feishu group picker)
# ---------------------------------------------------------------------------


class TestDiscoverChats:
    @pytest.mark.asyncio
    async def test_create_mode_success(
        self, client: AsyncClient, seed: dict[str, Any]
    ) -> None:
        """App ID + secret + org_id (no channel_id) — calls Feishu."""
        fake_items = [
            {
                "chat_id": "oc_1",
                "name": "Ops Team",
                "avatar": "https://img/1.png",
                "description": "Daily ops",
                "external": False,
            },
            {
                "chat_id": "oc_2",
                "name": "External Client",
                "external": True,
                "member_count": 7,
            },
        ]
        list_mock = AsyncMock(return_value=fake_items)
        with patch(
            "fim_one.core.channels.feishu.FeishuChannel.list_chats",
            new=list_mock,
        ):
            resp = await client.post(
                "/api/channels/discover-chats",
                json={
                    "app_id": "cli_x",
                    "app_secret": "shh",
                    "org_id": seed["org_id"],
                },
                headers=seed["headers"],
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["items"][0]["chat_id"] == "oc_1"
        assert body["items"][0]["name"] == "Ops Team"
        assert body["items"][0]["avatar"] == "https://img/1.png"
        assert body["items"][1]["external"] is True
        assert body["items"][1]["member_count"] == 7
        list_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalid_credentials_returns_400(
        self, client: AsyncClient, seed: dict[str, Any]
    ) -> None:
        list_mock = AsyncMock(
            side_effect=RuntimeError(
                "Feishu list_chats failed: invalid app_secret"
            )
        )
        with patch(
            "fim_one.core.channels.feishu.FeishuChannel.list_chats",
            new=list_mock,
        ):
            resp = await client.post(
                "/api/channels/discover-chats",
                json={
                    "app_id": "cli_x",
                    "app_secret": "wrong",
                    "org_id": seed["org_id"],
                },
                headers=seed["headers"],
            )
        assert resp.status_code == 400
        assert "invalid app_secret" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_edit_mode_uses_stored_secret(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with session_factory() as db:
            ch = Channel(
                id=str(uuid.uuid4()),
                name="E",
                type="feishu",
                org_id=seed["org_id"],
                created_by=seed["user_id"],
                config={
                    "app_id": "cli_x",
                    "app_secret": "stored-secret",
                    "chat_id": "oc_old",
                },
            )
            db.add(ch)
            await db.commit()

        captured: dict[str, Any] = {}

        async def _fake_list_chats(
            self: Any, *args: Any, **kwargs: Any
        ) -> list[dict[str, Any]]:
            captured["app_id"] = self.config.get("app_id")
            captured["app_secret"] = self.config.get("app_secret")
            return []

        with patch(
            "fim_one.core.channels.feishu.FeishuChannel.list_chats",
            _fake_list_chats,
        ):
            resp = await client.post(
                "/api/channels/discover-chats",
                json={"app_id": "cli_x", "channel_id": ch.id},
                headers=seed["headers"],
            )
        assert resp.status_code == 200, resp.text
        # The adapter passed to list_chats must have decrypted secret.
        assert captured["app_secret"] == "stored-secret"
        assert captured["app_id"] == "cli_x"

    @pytest.mark.asyncio
    async def test_outsider_rejected(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        outsider: dict[str, Any],
    ) -> None:
        resp = await client.post(
            "/api/channels/discover-chats",
            json={
                "app_id": "cli_x",
                "app_secret": "s",
                "org_id": seed["org_id"],
            },
            headers=outsider["headers"],
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_missing_secret_in_create_mode_returns_400(
        self, client: AsyncClient, seed: dict[str, Any]
    ) -> None:
        resp = await client.post(
            "/api/channels/discover-chats",
            json={"app_id": "cli_x", "org_id": seed["org_id"]},
            headers=seed["headers"],
        )
        assert resp.status_code == 400
        assert "app_secret" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_unauthenticated_rejected(
        self, client: AsyncClient, seed: dict[str, Any]
    ) -> None:
        resp = await client.post(
            "/api/channels/discover-chats",
            json={
                "app_id": "cli_x",
                "app_secret": "s",
                "org_id": seed["org_id"],
            },
        )
        assert resp.status_code in (401, 403)


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
    async def test_first_click_returns_decided_card(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """The webhook replaces the confirmation card with a decided
        card on the first approve — this is what disables the buttons
        client-side so the user can't click again.
        """
        async with session_factory() as db:
            ch = Channel(
                id=str(uuid.uuid4()),
                name="CB-Decided-1",
                type="feishu",
                org_id=seed["org_id"],
                created_by=seed["user_id"],
                config={"app_id": "cli_x", "app_secret": "s"},
            )
            db.add(ch)
            req = ConfirmationRequest(
                id=str(uuid.uuid4()),
                tool_call_id="tc-decided-1",
                agent_id=None,
                user_id=seed["user_id"],
                org_id=seed["org_id"],
                channel_id=ch.id,
                status="pending",
                payload={"tool_name": "send_email"},
            )
            db.add(req)
            await db.commit()

        payload = {
            "action": {
                "value": {"confirmation_id": req.id, "decision": "approve"}
            },
            "open_id": "ou_first",
        }
        resp = await client.post(
            f"/api/channels/{ch.id}/callback", json=payload
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["toast"]["type"] == "success"
        assert body["toast"]["content"] == "Approval recorded."
        assert body["card"]["type"] == "raw"
        new_card = body["card"]["data"]
        assert new_card["schema"] == "2.0"
        assert new_card["header"]["template"] == "green"
        # No buttons in the decided card — this is what prevents
        # further clicks.
        elements = new_card["body"]["elements"]
        serialized = json.dumps(elements)
        assert '"tag": "button"' not in serialized

    @pytest.mark.asyncio
    async def test_second_click_toast_says_already_decided(
        self,
        client: AsyncClient,
        seed: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Duplicate click (e.g. stale client) must show a distinct
        toast and still return the decided card — the DB stays
        unchanged.
        """
        async with session_factory() as db:
            ch = Channel(
                id=str(uuid.uuid4()),
                name="CB-Decided-2",
                type="feishu",
                org_id=seed["org_id"],
                created_by=seed["user_id"],
                config={"app_id": "cli_x", "app_secret": "s"},
            )
            db.add(ch)
            req = ConfirmationRequest(
                id=str(uuid.uuid4()),
                tool_call_id="tc-decided-2",
                agent_id=None,
                user_id=seed["user_id"],
                org_id=seed["org_id"],
                channel_id=ch.id,
                status="pending",
                payload={"tool_name": "ship_it"},
            )
            db.add(req)
            await db.commit()

        payload = {
            "action": {
                "value": {"confirmation_id": req.id, "decision": "approve"}
            },
            "open_id": "ou_first",
        }
        # First click — terminal approved.
        first = await client.post(
            f"/api/channels/{ch.id}/callback", json=payload
        )
        assert first.status_code == 200
        assert first.json()["toast"]["content"] == "Approval recorded."

        # Second click — must tell the clicker it's already approved.
        second = await client.post(
            f"/api/channels/{ch.id}/callback", json=payload
        )
        assert second.status_code == 200
        sbody = second.json()
        assert sbody["toast"]["type"] == "success"
        assert sbody["toast"]["content"] == (
            "This request was already approved."
        )
        # Decided card is still returned so a stale client catches up.
        assert sbody["card"]["type"] == "raw"
        assert sbody["card"]["data"]["header"]["template"] == "green"

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

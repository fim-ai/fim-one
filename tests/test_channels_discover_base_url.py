"""The Feishu group picker asks the API origin the channel itself would call.

``POST /api/channels/discover-chats`` built its adapter from the app id and
secret alone, so it always called ``https://open.feishu.cn``: it could not
work for a privately deployed Feishu, and it posted that deployment's app
secret to feishu.cn. It now resolves and checks ``base_url`` the way
``FeishuChannel`` does. Every outbound request here goes to a mocked
transport.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

import fim_one.db.models  # noqa: F401
from fim_one.core.channels import feishu as feishu_mod
from fim_one.db import create_session, init_db, shutdown_db
from fim_one.db.base import Base
from fim_one.db.models.channel import Channel
from fim_one.db.models.organization import Organization, OrgMembership
from fim_one.db.models.user import User
from fim_one.web import create_app
from fim_one.web.auth import create_access_token

pytestmark = pytest.mark.timeout(60)

PRIVATE = "https://open.feishu.corp.example"
OTHER_LISTED = "https://open.feishu.other.example"
USER = "user-discover"
ORG = "org-discover"


@pytest.fixture(autouse=True)
def _stable_cred_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import fim_one.core.security.encryption as enc

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "test-api-key-01234567890abcdef")
    monkeypatch.setattr(enc, "_CREDENTIAL_KEY_RAW", "test-api-key-01234567890abcdef")
    monkeypatch.setattr(enc, "_cred_fernet_instance", None)


@pytest.fixture()
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-discover-base-url-long-enough")
    await init_db()
    from fim_one.db import engine as engine_mod

    assert engine_mod._engine is not None
    async with engine_mod._engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with create_session() as db:
        db.add(User(id=USER, email="u@x.io", username="user", password_hash="x"))
        db.add(Organization(id=ORG, name="Org", slug="org-discover", owner_id=USER))
        await db.flush()
        db.add(OrgMembership(id=str(uuid.uuid4()), org_id=ORG, user_id=USER, role="owner"))
        await db.commit()
    token = create_access_token(USER, "u@x.io")
    try:
        # Built before ``feishu`` is given its recording transport below.
        async with AsyncClient(
            transport=ASGITransport(app=create_app()),
            base_url="http://t",
            headers={"Authorization": f"Bearer {token}"},
        ) as http:
            yield http
    finally:
        await shutdown_db()
        os.unlink(path)


class _Feishu:
    """Stands in for every Feishu host: records each request, answers like the API."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "t-1"})
        return httpx.Response(
            200,
            json={"code": 0, "data": {"items": [{"chat_id": "oc_1", "name": "Ops"}]}},
        )

    def client(self, **_kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._answer))

    @property
    def hosts(self) -> set[str]:
        return {request.url.host for request in self.requests}

    def secrets_sent(self) -> dict[str, str]:
        """host → the app secret posted to it."""
        sent: dict[str, str] = {}
        for request in self.requests:
            if request.method == "POST":
                sent[request.url.host] = json.loads(request.content)["app_secret"]
        return sent


@pytest.fixture()
def feishu(monkeypatch: pytest.MonkeyPatch) -> _Feishu:
    """Every client ``FeishuChannel`` opens goes to the recording transport."""
    fake = _Feishu()
    monkeypatch.setattr(feishu_mod, "httpx", SimpleNamespace(AsyncClient=fake.client))
    monkeypatch.setenv("FEISHU_ALLOWED_BASE_URLS", f"{PRIVATE}, {OTHER_LISTED}")
    return fake


async def _stored_channel(base_url: str | None) -> str:
    config = {
        "app_id": "cli_x",
        "app_secret": "stored-secret",
        "chat_id": "oc_old",
        "encrypt_key": "sign-me",
    }
    if base_url is not None:
        config["base_url"] = base_url
    channel_id = str(uuid.uuid4())
    async with create_session() as db:
        db.add(
            Channel(
                id=channel_id, name="F", type="feishu", org_id=ORG, created_by=USER, config=config
            )
        )
        await db.commit()
    return channel_id


async def _discover(client: AsyncClient, **body: Any) -> httpx.Response:
    return await client.post("/api/channels/discover-chats", json={"app_id": "cli_x", **body})


def _assert_refused(resp: httpx.Response) -> None:
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["error_code"] == "feishu_channel_base_url_not_allowed"
    # The picker shows ``detail`` as it is.
    assert "FEISHU_ALLOWED_BASE_URLS" in body["detail"]


class TestEditMode:
    async def test_the_stored_channels_host_is_asked_and_no_other(
        self, client: AsyncClient, feishu: _Feishu
    ) -> None:
        channel_id = await _stored_channel(PRIVATE)

        resp = await _discover(client, channel_id=channel_id)

        assert resp.status_code == 200, resp.text
        assert [item["chat_id"] for item in resp.json()["items"]] == ["oc_1"]
        assert feishu.hosts == {"open.feishu.corp.example"}
        assert feishu.secrets_sent() == {"open.feishu.corp.example": "stored-secret"}

    async def test_a_channel_without_a_base_url_asks_feishu_cn(
        self, client: AsyncClient, feishu: _Feishu
    ) -> None:
        channel_id = await _stored_channel(None)

        resp = await _discover(client, channel_id=channel_id)

        assert resp.status_code == 200, resp.text
        assert feishu.hosts == {"open.feishu.cn"}

    async def test_a_stored_host_that_is_no_longer_allowed_is_refused_unasked(
        self, client: AsyncClient, feishu: _Feishu, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        channel_id = await _stored_channel(PRIVATE)
        monkeypatch.delenv("FEISHU_ALLOWED_BASE_URLS")

        resp = await _discover(client, channel_id=channel_id)

        _assert_refused(resp)
        assert feishu.requests == []

    async def test_the_stored_secret_is_not_sent_to_a_host_the_request_names(
        self, client: AsyncClient, feishu: _Feishu
    ) -> None:
        channel_id = await _stored_channel(PRIVATE)

        resp = await _discover(client, channel_id=channel_id, base_url=OTHER_LISTED)

        assert resp.status_code == 200, resp.text
        assert feishu.hosts == {"open.feishu.corp.example"}

    async def test_a_retyped_secret_goes_to_the_stored_host_by_default(
        self, client: AsyncClient, feishu: _Feishu
    ) -> None:
        channel_id = await _stored_channel(PRIVATE)

        resp = await _discover(client, channel_id=channel_id, app_secret="retyped")

        assert resp.status_code == 200, resp.text
        assert feishu.secrets_sent() == {"open.feishu.corp.example": "retyped"}

    async def test_a_retyped_secret_may_come_with_its_own_host(
        self, client: AsyncClient, feishu: _Feishu
    ) -> None:
        channel_id = await _stored_channel(PRIVATE)

        resp = await _discover(
            client, channel_id=channel_id, app_secret="retyped", base_url=OTHER_LISTED
        )

        assert resp.status_code == 200, resp.text
        assert feishu.secrets_sent() == {"open.feishu.other.example": "retyped"}


class TestCreateMode:
    async def test_the_host_in_the_request_is_asked(
        self, client: AsyncClient, feishu: _Feishu
    ) -> None:
        resp = await _discover(client, app_secret="typed", org_id=ORG, base_url=f"{PRIVATE}/")

        assert resp.status_code == 200, resp.text
        assert feishu.secrets_sent() == {"open.feishu.corp.example": "typed"}
        assert feishu.hosts == {"open.feishu.corp.example"}

    async def test_without_a_base_url_feishu_cn_is_asked(
        self, client: AsyncClient, feishu: _Feishu
    ) -> None:
        resp = await _discover(client, app_secret="typed", org_id=ORG)

        assert resp.status_code == 200, resp.text
        assert feishu.hosts == {"open.feishu.cn"}

    @pytest.mark.parametrize(
        "base_url",
        [
            "https://internal.example",
            "http://127.0.0.1:8080",
            "https://open.feishu.cn.evil.example",
            f"{PRIVATE}/open-apis",
        ],
    )
    async def test_a_host_that_is_not_allowed_is_refused_unasked(
        self, client: AsyncClient, feishu: _Feishu, base_url: str
    ) -> None:
        resp = await _discover(client, app_secret="typed", org_id=ORG, base_url=base_url)

        _assert_refused(resp)
        assert feishu.requests == []

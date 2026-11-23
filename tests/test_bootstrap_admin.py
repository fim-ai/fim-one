"""The first administrator is created once, and only with the setup token."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from fim_one.db.models import User
from fim_one.db.models.organization import OrgMembership
from fim_one.web.platform import MARKET_ORG_ID
from fim_one.web.services.bootstrap import setup_token_matches
from tests.audit.harness import PASSWORD, World, world  # noqa: F401

TOKEN = "bootstrap-token-for-tests"


def _body(email: str, token: str = TOKEN) -> dict[str, str]:
    return {"email": email, "password": PASSWORD, "setup_token": token}


class TestSetupToken:
    def test_match_ignores_length_differences(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FIM_SETUP_TOKEN", TOKEN)
        assert setup_token_matches(TOKEN) is True
        assert setup_token_matches("nope") is False
        assert setup_token_matches("") is False
        monkeypatch.setenv("FIM_SETUP_TOKEN", "")
        assert setup_token_matches(TOKEN) is False

    async def test_refused_when_the_server_has_no_token(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FIM_SETUP_TOKEN", raising=False)
        response = await world.call(
            "POST", "/api/auth/setup", json=_body("root@example.test", "anything"), expect=503
        )
        assert response.json()["error_code"] == "setup_token_not_configured"

    async def test_wrong_token_is_refused(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FIM_SETUP_TOKEN", TOKEN)
        response = await world.call(
            "POST", "/api/auth/setup", json=_body("root@example.test", "wrong-token"), expect=403
        )
        assert response.json()["error_code"] == "setup_token_invalid"

    async def test_setup_creates_one_administrator(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FIM_SETUP_TOKEN", TOKEN)
        created = await world.call("POST", "/api/auth/setup", json=_body("root@example.test"))
        assert created.json()["user"]["is_admin"] is True

        again = await world.call(
            "POST", "/api/auth/setup", json=_body("other@example.test"), expect=403
        )
        assert again.json()["error_code"] == "system_already_initialized"

        async with world.factory() as db:
            admins = (
                await db.execute(select(User).where(User.is_admin.is_(True)))
            ).scalars().all()
            assert [row.email for row in admins] == ["root@example.test"]
            member = (
                await db.execute(
                    select(OrgMembership).where(
                        OrgMembership.org_id == MARKET_ORG_ID,
                        OrgMembership.user_id == admins[0].id,
                    )
                )
            ).scalar_one()
            assert member.role == "owner"

    async def test_register_does_not_create_the_first_administrator(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FIM_SETUP_TOKEN", TOKEN)
        response = await world.call(
            "POST",
            "/api/auth/register",
            json={"email": "first@example.test", "password": PASSWORD},
            expect=403,
        )
        assert response.json()["error_code"] == "system_not_initialized"

    async def test_only_one_concurrent_setup_wins(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FIM_SETUP_TOKEN", TOKEN)
        first, second = await asyncio.gather(
            world.client.post("/api/auth/setup", json=_body("a@example.test")),
            world.client.post("/api/auth/setup", json=_body("b@example.test")),
        )
        statuses = sorted((first.status_code, second.status_code))
        assert statuses == [200, 403]
        async with world.factory() as db:
            count = (await db.execute(select(func.count(User.id)))).scalar_one()
            assert count == 1

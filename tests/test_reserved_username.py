"""No account may take a name that reads as a deleted user's label.

The records that outlive a deleted account call it ``deleted-user-<id>``;
an account named that way could pass for one, or be taken for it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select

from fim_one.db.models import User
from fim_one.web.auth import create_oauth_state
from fim_one.web.oauth import OAuthProvider, OAuthUserInfo
from tests.audit.harness import World, world  # noqa: F401

RESERVED = "deleted-user-1a2b3c4d"


async def test_a_user_cannot_rename_themselves_to_it(world: World) -> None:  # noqa: F811
    ann = await world.user("ann")

    for name in (RESERVED, "Deleted-User-x"):
        response = await world.call(
            "PATCH", "/api/auth/profile", by=ann, json={"username": name}, expect=400
        )
        assert response.json()["error_code"] == "username_reserved"

    assert (await world.get(User, ann.id)).username == "ann"


async def test_an_administrator_cannot_create_an_account_with_it(world: World) -> None:  # noqa: F811
    root = await world.user("root", admin=True)

    response = await world.call(
        "POST", "/api/admin/users", by=root,
        json={"username": RESERVED, "password": "s3cret-pass", "email": "x@example.test"},
        expect=400,
    )  # fmt: skip

    assert response.json()["error_code"] == "username_reserved"
    async with world.factory() as db:
        assert await db.scalar(select(User).where(User.email == "x@example.test")) is None


async def test_a_provider_login_with_it_gets_another_name(world: World) -> None:  # noqa: F811
    info = OAuthUserInfo(
        provider="github", id="gh-9", username=RESERVED, email="new@example.test",
        display_name="New", email_verified=True,
    )  # fmt: skip
    provider = MagicMock(spec=OAuthProvider)
    provider.name = "github"

    async def _info(_provider: Any, _token: str) -> OAuthUserInfo:
        return info

    with (
        patch("fim_one.web.api.oauth.get_provider", return_value=provider),
        patch("fim_one.web.api.oauth.exchange_code", AsyncMock(return_value="tok")),
        patch("fim_one.web.api.oauth.fetch_user_info", _info),
    ):
        await world.call(
            "GET", "/api/auth/oauth/github/callback",
            params={"code": "abc", "state": create_oauth_state("login", None)}, expect=302,
        )  # fmt: skip

    async with world.factory() as db:
        created = await db.scalar(select(User).where(User.email == "new@example.test"))
    assert created is not None and created.username == "github_user"

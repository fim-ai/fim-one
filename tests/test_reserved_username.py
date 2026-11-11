"""No account may take a name that reads as a deleted user's label.

The records that outlive a deleted account call it ``deleted-user-<id>``;
an account named that way could pass for one, or be taken for it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from fim_one.db.models import User
from fim_one.web.auth import create_oauth_state
from fim_one.web.oauth import OAuthProvider, OAuthUserInfo
from fim_one.web.services.user_deletion import is_reserved_username
from tests.audit.harness import World, world  # noqa: F401

RESERVED = "deleted-user-1a2b3c4d"
# Spellings drawn like the label.
LOOK_ALIKES = [
    "  deleted-user-1a2b3c4d",
    "d\u0435leted-user-1a2b3c4d",  # Cyrillic e
    "DELETED-USER-1A2B3C4D",
    "D\u0415LETED-user-1a2b3c4d",  # Cyrillic capital IE
    "\u0501\u0435\u04cf\u0435\u0442\u0435\u0501-\u057d\u0455\u0435\u0433-x",
    "deleted\u2011user\u2011x",  # non-breaking hyphen
    "deleted\u2010user-x",
    "deleted\u2212user-x",  # minus sign
    "\u200bdeleted-user-x",  # zero-width space
    "deleted-\u200buser-x",
    "deleted-user\u200d-x",  # zero-width joiner
    "\uff44\uff45leted-user-x",  # full-width letters
    "deleted\uff0duser-x",  # full-width hyphen
    "deleted\u00aduser-x",  # soft hyphen, drawn as a hyphen
    "deleted-\u00aduser-x",  # soft hyphen, invisible
    "\u00a0deleted-user-x",  # no-break space
]


@pytest.mark.parametrize("name", LOOK_ALIKES)
def test_a_look_alike_of_the_label_is_reserved(name: str) -> None:
    assert is_reserved_username(name), ascii(name)


@pytest.mark.parametrize(
    "name",
    [None, "", "ann", "deleted", "deleteduser-x", "user-deleted-x", "d\u00e9leted-user-x",
     "\u0434\u0435\u043b", "\u5f20\u4e09", "deleted_user_x"],
)  # fmt: skip
def test_other_names_are_not(name: str | None) -> None:
    assert not is_reserved_username(name)


async def test_a_user_cannot_rename_themselves_to_it(world: World) -> None:  # noqa: F811
    ann = await world.user("ann")

    for name in (RESERVED, "Deleted-User-x", *LOOK_ALIKES):
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

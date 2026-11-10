"""An administrator's change to permissions or secrets and its record are stored together.

The administration endpoints wrote their audit row in a commit of its own
after the change was committed: when that second write failed, the change
was done, the request answered 500 and no record existed. For changes to
who may do what, a change with no record is the worse outcome.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from fim_one.core.audit import writer as audit
from fim_one.db.models import IpRule, SystemSetting, User
from fim_one.db.models.api_key import ApiKey
from fim_one.db.models.invite_code import InviteCode
from tests.audit.harness import World, world  # noqa: F401


def _failing(action_prefix: str) -> Any:
    real = audit.record

    async def record(action: str, **kwargs: Any) -> None:
        if action.startswith(action_prefix):
            raise RuntimeError("audit table unavailable")
        await real(action, **kwargs)

    return patch("fim_one.core.audit.writer.record", record)


async def _attempt(world: World, method: str, path: str, by: User, **kwargs: Any) -> None:
    with pytest.raises(RuntimeError, match="audit table unavailable"):
        await world.client.request(method, path, headers=world.auth(by), **kwargs)


class TestWhenTheRecordCannotBeWritten:
    async def test_a_user_is_not_disabled(self, world: World) -> None:
        root = await world.user("root", admin=True)
        bob = await world.user("bob")

        with _failing("user."):
            await _attempt(
                world, "PATCH", f"/api/admin/users/{bob.id}/active", root, json={"is_active": False}
            )

        assert (await world.get(User, bob.id)).is_active is True

    async def test_administrator_rights_are_not_granted(self, world: World) -> None:
        root = await world.user("root", admin=True)
        bob = await world.user("bob")

        with _failing("user."):
            await _attempt(
                world, "PATCH", f"/api/admin/users/{bob.id}/admin", root, json={"is_admin": True}
            )

        assert (await world.get(User, bob.id)).is_admin is False

    async def test_a_password_is_not_reset(self, world: World) -> None:
        root = await world.user("root", admin=True)
        bob = await world.user("bob", password="the-old-password-1")
        before = (await world.get(User, bob.id)).password_hash

        with _failing("user."):
            await _attempt(
                world,
                "POST",
                f"/api/admin/users/{bob.id}/reset-password",
                root,
                json={"new_password": "a-new-password-2"},
            )

        assert (await world.get(User, bob.id)).password_hash == before

    async def test_a_quota_is_not_set_and_a_user_is_not_deleted(self, world: World) -> None:
        root = await world.user("root", admin=True)
        bob = await world.user("bob")

        with _failing("user."):
            await _attempt(
                world, "PATCH", f"/api/admin/users/{bob.id}/quota", root, json={"token_quota": 5}
            )
            await _attempt(world, "DELETE", f"/api/admin/users/{bob.id}", root)

        kept = await world.get(User, bob.id)
        assert kept is not None and kept.token_quota is None

    async def test_an_api_key_is_not_created_or_switched(self, world: World) -> None:
        from sqlalchemy import select

        root = await world.user("root", admin=True)
        await world.add(
            ApiKey(id="key-1", user_id=root.id, name="ci", key_prefix="fim_abcd", key_hash="h")
        )

        with _failing("api_key."):
            await _attempt(world, "POST", "/api/admin/api-keys", root, json={"name": "new"})
            await _attempt(
                world, "PATCH", "/api/admin/api-keys/key-1/active", root, json={"is_active": False}
            )
            await _attempt(world, "DELETE", "/api/admin/api-keys/key-1", root)

        async with world.factory() as db:
            keys = (await db.execute(select(ApiKey))).scalars().all()
        assert [(k.id, k.is_active) for k in keys] == [("key-1", True)]

    async def test_an_ip_rule_is_not_created(self, world: World) -> None:
        from sqlalchemy import select

        root = await world.user("root", admin=True)

        with _failing("ip_rule."):
            await _attempt(
                world,
                "POST",
                "/api/admin/ip-rules",
                root,
                json={"ip_address": "198.51.100.9", "rule_type": "deny"},
            )

        async with world.factory() as db:
            assert (await db.execute(select(IpRule))).scalars().all() == []

    async def test_an_invite_code_is_not_created(self, world: World) -> None:
        from sqlalchemy import select

        root = await world.user("root", admin=True)

        with _failing("invite_code."):
            await _attempt(world, "POST", "/api/admin/invite-codes", root, json={})

        async with world.factory() as db:
            assert (await db.execute(select(InviteCode))).scalars().all() == []

    async def test_no_setting_of_the_save_is_changed(self, world: World) -> None:
        from sqlalchemy import select

        root = await world.user("root", admin=True)

        with _failing("settings."):
            await _attempt(
                world,
                "PATCH",
                "/api/admin/settings",
                root,
                json={"registration_mode": "disabled", "maintenance_mode": True},
            )

        async with world.factory() as db:
            assert (await db.execute(select(SystemSetting))).scalars().all() == []


class TestOrdinarily:
    async def test_the_change_and_its_record_are_both_there(self, world: World) -> None:
        root = await world.user("root", admin=True)
        bob = await world.user("bob")

        await world.call(
            "PATCH",
            "/api/admin/settings",
            by=root,
            json={"registration_mode": "invite", "maintenance_mode": False},
        )
        await world.call(
            "PATCH", f"/api/admin/users/{bob.id}/admin", by=root, json={"is_admin": True}
        )

        settings = await world.one("settings.update", actor=root)
        assert settings.detail == "registration_mode=invite; maintenance_mode=False"
        await world.one("user.grant_admin", actor=root, target=("user", bob.id))
        assert (await world.get(User, bob.id)).is_admin is True

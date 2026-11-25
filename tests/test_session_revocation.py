"""Refresh and access tokens die when a session is revoked or the password changes.

The cut is the account's session generation, not the clock. A token issued in
the same second as the change is rejected, and so is a refresh that rotates
while the change is in flight.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import jwt

from fim_one.db.models import User
from fim_one.web.auth import ALGORITHM, SECRET_KEY, create_refresh_token, hash_refresh_token
from tests.audit.harness import PASSWORD, World, world  # noqa: F401

NEW_PASSWORD = "a brand new passphrase 42"


async def _login(world: World, email: str, password: str = PASSWORD) -> tuple[str, str]:
    response = await world.call(
        "POST", "/api/auth/login", json={"email": email, "password": password}
    )
    body = response.json()
    return body["access_token"], body["refresh_token"]


async def _rejected(world: World, access: str, refresh: str) -> None:
    me = await world.client.get("/api/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 401
    again = await world.client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert again.status_code == 401


class TestSessionRevocation:
    async def test_revoke_all_blocks_the_refresh_token(self, world: World) -> None:
        alice = await world.user("alice", password=PASSWORD)
        access, refresh = await _login(world, alice.email or "")
        rotated = await world.client.post("/api/auth/refresh", json={"refresh_token": refresh})
        assert rotated.status_code == 200
        access = rotated.json()["access_token"]
        refresh = rotated.json()["refresh_token"]

        await world.call(
            "POST",
            "/api/me/sessions/revoke-all",
            headers={"Authorization": f"Bearer {access}"},
        )

        await _rejected(world, access, refresh)
        stored = await world.get(User, alice.id)
        assert stored.refresh_token is None

    async def test_password_change_blocks_old_tokens(self, world: World) -> None:
        alice = await world.user("alice", password=PASSWORD)
        access, refresh = await _login(world, alice.email or "")

        await world.call(
            "POST",
            "/api/auth/change-password",
            headers={"Authorization": f"Bearer {access}"},
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        )

        await _rejected(world, access, refresh)
        fresh, _rotated = await _login(world, alice.email or "", NEW_PASSWORD)
        me = await world.client.get("/api/auth/me", headers={"Authorization": f"Bearer {fresh}"})
        assert me.status_code == 200

    async def test_generation_blocks_refresh_when_the_hash_matches(self, world: World) -> None:
        alice = await world.user("alice", password=PASSWORD)
        token = create_refresh_token(alice.id, alice.email or "", session_generation=0)
        async with world.factory() as db:
            user = await db.get(User, alice.id)
            assert user is not None
            user.refresh_token = hash_refresh_token(token)
            user.refresh_token_expires_at = datetime.now(UTC) + timedelta(days=1)
            user.session_generation = 1
            await db.commit()

        response = await world.client.post("/api/auth/refresh", json={"refresh_token": token})
        assert response.status_code == 401
        assert response.json()["error_code"] == "session_invalidated"

    async def test_legacy_token_dies_after_generation_moves(self, world: World) -> None:
        alice = await world.user("alice", password=PASSWORD)
        now = datetime.now(UTC)
        legacy = jwt.encode(
            {
                "sub": alice.id,
                "email": alice.email,
                "type": "access",
                "iat": now,
                "exp": now + timedelta(minutes=30),
            },
            SECRET_KEY,
            algorithm=ALGORITHM,
        )
        alive = await world.client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {legacy}"}
        )
        assert alive.status_code == 200

        async with world.factory() as db:
            user = await db.get(User, alice.id)
            assert user is not None
            user.session_generation = 1
            await db.commit()

        dead = await world.client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {legacy}"}
        )
        assert dead.status_code == 401

    async def test_refresh_during_password_change_cannot_keep_a_session(self, world: World) -> None:
        alice = await world.user("alice", password=PASSWORD)
        access, refresh = await _login(world, alice.email or "")

        changed, rotated = await asyncio.gather(
            world.client.post(
                "/api/auth/change-password",
                headers={"Authorization": f"Bearer {access}"},
                json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
            ),
            world.client.post("/api/auth/refresh", json={"refresh_token": refresh}),
        )

        assert changed.status_code == 200
        assert rotated.status_code in (200, 401)
        if rotated.status_code == 200:
            kept = await world.client.get(
                "/api/auth/me",
                headers={"Authorization": f"Bearer {rotated.json()['access_token']}"},
            )
            assert kept.status_code == 401
            again = await world.client.post(
                "/api/auth/refresh",
                json={"refresh_token": rotated.json()["refresh_token"]},
            )
            assert again.status_code == 401
        await _rejected(world, access, refresh)
        fresh, _next = await _login(world, alice.email or "", NEW_PASSWORD)
        me = await world.client.get("/api/auth/me", headers={"Authorization": f"Bearer {fresh}"})
        assert me.status_code == 200

    async def test_completed_refresh_dies_after_password_change(self, world: World) -> None:
        alice = await world.user("alice", password=PASSWORD)
        access, refresh = await _login(world, alice.email or "")
        rotated = await world.client.post("/api/auth/refresh", json={"refresh_token": refresh})
        assert rotated.status_code == 200
        new_access = rotated.json()["access_token"]
        new_refresh = rotated.json()["refresh_token"]

        changed = await world.client.post(
            "/api/auth/change-password",
            headers={"Authorization": f"Bearer {new_access}"},
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        )
        assert changed.status_code == 200
        await _rejected(world, new_access, new_refresh)
        await _rejected(world, access, refresh)

    async def test_refresh_loses_if_generation_moved(self, world: World) -> None:
        from sqlalchemy import update

        alice = await world.user("alice", password=PASSWORD)
        _access, refresh = await _login(world, alice.email or "")
        presented = hash_refresh_token(refresh)

        async with world.factory() as db:
            user = await db.get(User, alice.id)
            assert user is not None
            generation = int(user.session_generation or 0)

        changed = await world.client.post(
            "/api/auth/change-password",
            headers={"Authorization": f"Bearer {_access}"},
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        )
        assert changed.status_code == 200

        async with world.factory() as db:
            rotated = await db.execute(
                update(User)
                .where(
                    User.id == alice.id,
                    User.refresh_token == presented,
                    User.session_generation == generation,
                )
                .values(refresh_token="rotated-hash")
            )
            await db.commit()
        assert getattr(rotated, "rowcount", None) == 0
        await _rejected(world, _access, refresh)

    async def test_reenable_does_not_restore_access_token(self, world: World) -> None:
        root = await world.user("root", admin=True, password=PASSWORD)
        alice = await world.user("alice", password=PASSWORD)
        access, refresh = await _login(world, alice.email or "")

        await world.call(
            "PATCH",
            f"/api/admin/users/{alice.id}/active",
            by=root,
            json={"is_active": False},
        )
        disabled = await world.client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {access}"}
        )
        assert disabled.status_code == 403

        await world.call(
            "PATCH",
            f"/api/admin/users/{alice.id}/active",
            by=root,
            json={"is_active": True},
        )
        await _rejected(world, access, refresh)
        fresh, _next = await _login(world, alice.email or "")
        me = await world.client.get("/api/auth/me", headers={"Authorization": f"Bearer {fresh}"})
        assert me.status_code == 200

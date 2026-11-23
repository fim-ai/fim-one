"""Refresh and access tokens die when a session is revoked or the password changes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from fim_one.db.models import User
from fim_one.web.auth import create_refresh_token, hash_refresh_token
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
        await asyncio.sleep(1.1)

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
        await asyncio.sleep(1.1)

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

    async def test_cutoff_blocks_refresh_when_the_hash_matches(self, world: World) -> None:
        alice = await world.user("alice", password=PASSWORD)
        token = create_refresh_token(alice.id, alice.email or "")
        async with world.factory() as db:
            user = await db.get(User, alice.id)
            assert user is not None
            user.refresh_token = hash_refresh_token(token)
            user.refresh_token_expires_at = datetime.now(UTC) + timedelta(days=1)
            user.tokens_invalidated_at = datetime.now(UTC) + timedelta(minutes=5)
            await db.commit()

        response = await world.client.post("/api/auth/refresh", json={"refresh_token": token})
        assert response.status_code == 401
        assert response.json()["error_code"] == "session_invalidated"

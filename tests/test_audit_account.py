"""What a user does to their own account leaves a record.

Changing a password, switching two-factor on or off, linking or unlinking
a provider, creating or deleting an API key, signing every session out:
none of it was recorded anywhere. Each now leaves one audit event with the
user as actor and target and the caller's address, and never the password,
the code or the key.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pyotp
from sqlalchemy import select

from fim_one.db.models import User, UserOAuthBinding
from fim_one.db.models.api_key import ApiKey
from fim_one.db.models.email_verification import EmailVerification
from fim_one.web.auth import create_oauth_state
from fim_one.web.oauth import OAuthProvider, OAuthUserInfo
from tests.audit.harness import PASSWORD, World, world  # noqa: F401
from tests.totp import current_code

NEW_PASSWORD = "a brand new passphrase 42"


async def _user_by_email(world: World, email: str) -> User:
    async with world.factory() as db:
        return (await db.execute(select(User).where(User.email == email))).scalar_one()


class TestCreatingAnAccount:
    async def test_first_time_setup(self, world: World) -> None:
        await world.call(
            "POST", "/api/auth/setup", json={"email": "root@example.test", "password": PASSWORD}
        )

        root = await _user_by_email(world, "root@example.test")
        row = await world.one("auth.setup_admin", actor=root, target=("user", root.id))
        assert row.actor_kind == "admin"
        world.assert_absent(row, PASSWORD)

    async def test_registration(self, world: World) -> None:
        await world.user("root", admin=True)

        await world.call(
            "POST",
            "/api/auth/register",
            json={"email": "new@example.test", "password": PASSWORD},
        )

        new = await _user_by_email(world, "new@example.test")
        row = await world.one("auth.register", actor=new, target=("user", new.id))
        assert (row.actor_label, row.detail) == ("new@example.test", None)
        world.assert_absent(row, PASSWORD)

    async def test_the_first_account_registered_is_marked(self, world: World) -> None:
        await world.call(
            "POST",
            "/api/auth/register",
            json={"email": "first@example.test", "password": PASSWORD},
        )

        first = await _user_by_email(world, "first@example.test")
        row = await world.one("auth.register", actor=first, target=("user", first.id))
        assert row.detail == "first account, made administrator"


class TestPasswords:
    async def test_changing_it(self, world: World) -> None:
        alice = await world.user("alice", password=PASSWORD)

        await world.call(
            "POST",
            "/api/auth/change-password",
            by=alice,
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        )

        row = await world.one("auth.password_change", actor=alice, target=("user", alice.id))
        assert row.auth_method == "session"
        world.assert_absent(row, PASSWORD, NEW_PASSWORD)

    async def test_a_wrong_current_password_changes_and_records_nothing(
        self, world: World
    ) -> None:
        alice = await world.user("alice", password=PASSWORD)

        await world.call(
            "POST",
            "/api/auth/change-password",
            by=alice,
            json={"current_password": "not it", "new_password": NEW_PASSWORD},
            expect=400,
        )

        assert await world.events() == []

    async def test_setting_a_first_one(self, world: World) -> None:
        octo = await world.user("octo")  # signed up through a provider: no password

        await world.call(
            "POST", "/api/auth/set-password", by=octo, json={"new_password": NEW_PASSWORD}
        )

        row = await world.one("auth.password_set", actor=octo, target=("user", octo.id))
        world.assert_absent(row, NEW_PASSWORD)

    async def test_resetting_it_by_emailed_code_while_signed_in(self, world: World) -> None:
        alice = await world.user("alice", password=PASSWORD)
        await world.add(
            EmailVerification(
                email=alice.email,
                code="654321",
                purpose="reset_password",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )

        await world.call(
            "POST",
            "/api/auth/reset-password",
            by=alice,
            json={"code": "654321", "new_password": NEW_PASSWORD},
        )

        row = await world.one("auth.password_reset", actor=alice, target=("user", alice.id))
        assert row.detail == "emailed code, signed in"
        world.assert_absent(row, NEW_PASSWORD, "654321")

    async def test_resetting_a_forgotten_one(self, world: World) -> None:
        """Nobody is signed in: the emailed code is what proved the account."""
        alice = await world.user("alice", password=PASSWORD)
        reset_token = str(uuid.uuid4())
        await world.add(
            EmailVerification(
                email=alice.email,
                code="111222",
                purpose="reset_password",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
                verified_at=datetime.now(UTC),
                reset_token=reset_token,
            )
        )

        await world.call(
            "POST",
            "/api/auth/forgot-password",
            json={"email": alice.email, "reset_token": reset_token, "new_password": NEW_PASSWORD},
        )

        row = await world.one("auth.password_reset", actor=alice, target=("user", alice.id))
        assert (row.detail, row.auth_method) == ("emailed code", "email_code")
        world.assert_absent(row, NEW_PASSWORD, reset_token)


class TestTwoFactor:
    async def test_switching_it_on(self, world: World) -> None:
        secret = pyotp.random_base32()
        alice = await world.user("alice", password=PASSWORD, totp_secret=secret)

        response = await world.call(
            "POST", "/api/auth/2fa/enable", by=alice, json={"code": current_code(secret)}
        )

        row = await world.one("auth.two_factor_enable", actor=alice, target=("user", alice.id))
        world.assert_absent(row, secret, *response.json()["backup_codes"])

    async def test_switching_it_off(self, world: World) -> None:
        secret = pyotp.random_base32()
        alice = await world.user(
            "alice", password=PASSWORD, totp_secret=secret, totp_enabled=True
        )

        await world.call("POST", "/api/auth/2fa/disable", by=alice, json={"password": PASSWORD})

        row = await world.one("auth.two_factor_disable", actor=alice, target=("user", alice.id))
        world.assert_absent(row, secret, PASSWORD)

    async def test_new_backup_codes(self, world: World) -> None:
        alice = await world.user(
            "alice", password=PASSWORD, totp_secret=pyotp.random_base32(), totp_enabled=True
        )

        response = await world.call(
            "POST", "/api/auth/2fa/backup-codes", by=alice, json={"password": PASSWORD}
        )

        row = await world.one(
            "auth.backup_codes_regenerate", actor=alice, target=("user", alice.id)
        )
        world.assert_absent(row, PASSWORD, *response.json()["backup_codes"])


class TestLinkedProviders:
    async def test_unlinking_one(self, world: World) -> None:
        alice = await world.user("alice", password=PASSWORD)
        await world.add(UserOAuthBinding(user_id=alice.id, provider="github", oauth_id="gh-7"))

        await world.call("DELETE", "/api/auth/oauth-bindings/github", by=alice)

        row = await world.one("auth.oauth_unbind", actor=alice, target=("user", alice.id))
        assert row.detail == "provider=github"

    async def test_linking_one(self, world: World) -> None:
        alice = await world.user("alice", password=PASSWORD)
        info = OAuthUserInfo(
            provider="github",
            id="gh-7",
            username="alice-gh",
            email=alice.email,
            display_name="Alice",
            email_verified=True,
        )
        provider = MagicMock(spec=OAuthProvider)
        provider.name = "github"

        async def _info(_provider: Any, _token: str) -> OAuthUserInfo:
            return info

        with (
            patch("fim_one.web.api.oauth.get_provider", return_value=provider),
            patch("fim_one.web.api.oauth.exchange_code", AsyncMock(return_value="tok")),
            patch("fim_one.web.api.oauth.fetch_user_info", _info),
        ):
            response = await world.call(
                "GET",
                "/api/auth/oauth/github/callback",
                params={"code": "abc", "state": create_oauth_state("bind", alice.id)},
                expect=302,
            )

        assert "bind_success=github" in response.headers["location"]
        # The redirect carries no session: the actor is the account the
        # signed bind state names.
        row = await world.one("auth.oauth_bind", actor=alice, target=("user", alice.id))
        assert row.detail == "provider=github"
        assert await world.logins() == []  # linking is not a sign-in


class TestOwnApiKeys:
    async def test_creating_one(self, world: World) -> None:
        alice = await world.user("alice")

        created = (
            await world.call(
                "POST", "/api/me/api-keys", by=alice, json={"name": "ci", "scopes": "chat"}
            )
        ).json()

        row = await world.one("api_key.create", actor=alice, target=("api_key", created["id"]))
        assert row.target_label == "ci"
        assert row.detail == f"prefix={created['key_prefix']}, scopes=chat"
        world.assert_absent(row, created["key"], hashlib.sha256(created["key"].encode()).hexdigest())

    async def test_switching_one_off_and_on(self, world: World) -> None:
        alice = await world.user("alice")
        key = ApiKey(id="key-1", user_id=alice.id, name="ci", key_prefix="fim_abcd", key_hash="h")
        await world.add(key)

        for active in (False, True):
            await world.call(
                "PATCH", "/api/me/api-keys/key-1/active", by=alice, json={"is_active": active}
            )

        off = await world.one("api_key.disable", actor=alice, target=("api_key", "key-1"))
        on = await world.one("api_key.enable", actor=alice, target=("api_key", "key-1"))
        assert off.detail == on.detail == "prefix=fim_abcd"

    async def test_deleting_one(self, world: World) -> None:
        alice = await world.user("alice")
        await world.add(
            ApiKey(id="key-1", user_id=alice.id, name="ci", key_prefix="fim_abcd", key_hash="h")
        )

        await world.call("DELETE", "/api/me/api-keys/key-1", by=alice)

        row = await world.one("api_key.delete", actor=alice, target=("api_key", "key-1"))
        assert (row.target_label, row.detail) == ("ci", "prefix=fim_abcd")
        assert await world.get(ApiKey, "key-1") is None

    async def test_someone_elses_key_is_not_found_and_nothing_is_recorded(
        self, world: World
    ) -> None:
        alice = await world.user("alice")
        bob = await world.user("bob")
        await world.add(
            ApiKey(id="key-1", user_id=bob.id, name="ci", key_prefix="fim_abcd", key_hash="h")
        )

        await world.call("DELETE", "/api/me/api-keys/key-1", by=alice, expect=404)

        assert await world.events() == []


class TestSessions:
    async def test_signing_every_session_out(self, world: World) -> None:
        alice = await world.user("alice")

        await world.call("POST", "/api/me/sessions/revoke-all", by=alice)

        await world.one("auth.sessions_revoke", actor=alice, target=("user", alice.id))


class TestEmailChange:
    async def test_the_endpoint_is_switched_off_and_records_nothing(self, world: World) -> None:
        """Changing the email address is disabled (501). The record is in
        place for when it returns; this holds the current behaviour."""
        alice = await world.user("alice")

        await world.call(
            "POST",
            "/api/auth/change-email/confirm",
            by=alice,
            json={"new_email": "a2@example.test", "code": "123456"},
            expect=501,
        )

        assert await world.events() == []


class TestDeletingTheAccount:
    async def test_the_record_says_how_the_user_was_signed_in(self, world: World) -> None:
        """The actor was built by hand here, with no sign-in method and
        ``user`` as kind even for an administrator."""
        dave = await world.user("dave")

        await world.call("DELETE", "/api/auth/account", by=dave)

        # The record outlives the account: it names the user by the
        # deleted-user label and carries no address (tests/test_user_deletion.py).
        row = await world.one(
            "account.self_delete", actor=None, target=("user", dave.id), ip=None
        )
        assert (row.actor_id, row.auth_method, row.actor_kind) == (dave.id, "session", "user")
        assert await world.get(User, dave.id) is None


class TestTheUsername:
    async def test_a_change_is_recorded_under_the_old_name_with_both(self, world: World) -> None:
        """The username is what later records call the user by; a change of
        it left nothing, so a record's actor could not be traced across it."""
        bob = await world.user("bob")

        await world.call("PATCH", "/api/auth/profile", by=bob, json={"username": "robert"})

        (row,) = await world.events("auth.username_change")
        assert (row.actor_id, row.actor_label, row.target_label) == (bob.id, "bob", "robert")
        assert row.changes == {"username": {"from": "bob", "to": "robert"}}
        assert row.ip_address is not None

    async def test_other_profile_fields_leave_no_record(self, world: World) -> None:
        bob = await world.user("bob")

        await world.call(
            "PATCH", "/api/auth/profile", by=bob, json={"display_name": "Bob", "username": "bob"}
        )

        assert await world.events() == []

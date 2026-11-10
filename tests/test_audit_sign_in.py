"""Sign-ins are recorded with their method, OAuth sign-ins included.

A sign-in through an OAuth provider left no row at all: the callback
issued tokens without writing ``login_history``, and had no ``Request`` to
read an address from. No sign-in row said how the user had signed in.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pyotp
import pytest

from fim_one.db.models import User, UserOAuthBinding
from fim_one.db.models.email_verification import EmailVerification
from fim_one.web.auth import create_oauth_state
from fim_one.web.oauth import OAuthProvider, OAuthUserInfo
from tests.audit.harness import CLIENT_IP, PASSWORD, USER_AGENT, World, world  # noqa: F401


@pytest.fixture()
def github() -> Iterator[dict[str, Any]]:
    """A configured GitHub provider whose code exchange answers with ``identity``."""
    identity: dict[str, Any] = {
        "info": OAuthUserInfo(
            provider="github",
            id="gh-1001",
            username="octo",
            email="octo@example.test",
            display_name="Octo",
            email_verified=True,
        )
    }
    provider = MagicMock(spec=OAuthProvider)
    provider.name = "github"

    async def _info(_provider: Any, _token: str) -> OAuthUserInfo:
        return identity["info"]  # type: ignore[no-any-return]

    with (
        patch("fim_one.web.api.oauth.get_provider", return_value=provider),
        patch("fim_one.web.api.oauth.exchange_code", AsyncMock(return_value="provider-token")),
        patch("fim_one.web.api.oauth.fetch_user_info", _info),
    ):
        yield identity


async def _callback(world: World, **params: str) -> Any:
    query = {"code": "abc", "state": create_oauth_state("login", None), **params}
    return await world.call(
        "GET", "/api/auth/oauth/github/callback", params=query, expect=(302, 307)
    )


def _row(row: Any) -> tuple[Any, ...]:
    return (row.user_id, row.email, row.method, row.success, row.failure_reason)


class TestOAuthSignIn:
    async def test_oauth_login_recorded(self, world: World, github: dict[str, Any]) -> None:
        octo = await world.user("octo")
        await world.add(
            UserOAuthBinding(
                user_id=octo.id, provider="github", oauth_id="gh-1001", email=octo.email
            )
        )

        response = await _callback(world)

        assert "access_token" in response.headers["location"]
        (row,) = await world.logins()
        assert _row(row) == (octo.id, octo.email, "oauth:github", True, None)
        # The callback has no Request parameter: the address came from the context.
        assert (row.ip_address, row.user_agent) == (CLIENT_IP, USER_AGENT)
        # Signing in creates nothing: no event beside the sign-in row.
        assert await world.events() == []

    async def test_a_first_sign_in_that_creates_the_account_is_an_event(
        self, world: World, github: dict[str, Any]
    ) -> None:
        await world.user("root", admin=True)  # not the first account of the instance

        await _callback(world)

        async with world.factory() as db:
            from sqlalchemy import select

            created = (
                await db.execute(select(User).where(User.username == "octo"))
            ).scalar_one()
        event = await world.one("auth.oauth_signup", actor=created, target=("user", created.id))
        assert (event.target_label, event.detail) == ("octo", "provider=github")
        (row,) = await world.logins()
        assert _row(row) == (created.id, "octo@example.test", "oauth:github", True, None)

    async def test_a_refused_new_account_is_a_failed_sign_in(
        self, world: World, github: dict[str, Any]
    ) -> None:
        from fim_one.db.models import SystemSetting
        from fim_one.web.api.admin import SETTING_REGISTRATION_MODE

        await world.add(SystemSetting(key=SETTING_REGISTRATION_MODE, value="disabled"))

        response = await _callback(world)

        assert "error=registration_disabled" in response.headers["location"]
        (row,) = await world.logins()
        assert _row(row) == (
            None,
            "octo@example.test",
            "oauth:github",
            False,
            "registration_disabled",
        )
        assert row.ip_address == CLIENT_IP
        assert await world.events() == []

    async def test_with_a_second_factor_both_steps_name_the_provider(
        self, world: World, github: dict[str, Any]
    ) -> None:
        secret = pyotp.random_base32()
        octo = await world.user("octo", totp_enabled=True, totp_secret=secret)
        await world.add(UserOAuthBinding(user_id=octo.id, provider="github", oauth_id="gh-1001"))

        response = await _callback(world)
        temp_token = response.headers["location"].split("temp_token=")[1]
        await world.call(
            "POST",
            "/api/auth/login/verify-2fa",
            json={"temp_token": temp_token, "code": pyotp.TOTP(secret).now()},
        )

        first, second = await world.logins()
        assert _row(first) == (octo.id, octo.email, "oauth:github", False, "2fa_pending")
        assert _row(second) == (octo.id, octo.email, "oauth:github", True, None)


class TestPasswordAndCodeSignIn:
    async def test_a_password_sign_in_says_password(self, world: World) -> None:
        alice = await world.user("alice", password=PASSWORD)

        await world.call(
            "POST", "/api/auth/login", json={"email": alice.email, "password": PASSWORD}
        )
        await world.call(
            "POST", "/api/auth/login", json={"email": alice.email, "password": "no"}, expect=401
        )
        await world.call(
            "POST", "/api/auth/login", json={"email": "ghost@x.io", "password": "no"}, expect=401
        )

        assert [_row(r) for r in await world.logins()] == [
            (alice.id, alice.email, "password", True, None),
            (alice.id, alice.email, "password", False, "wrong_password"),
            (None, "ghost@x.io", "password", False, "user_not_found"),
        ]

    async def test_an_email_code_sign_in_says_email_code(self, world: World) -> None:
        from datetime import UTC, datetime, timedelta

        alice = await world.user("alice")
        await world.add(
            EmailVerification(
                email=alice.email,
                code="123456",
                purpose="login",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )

        await world.call(
            "POST", "/api/auth/login-with-code", json={"email": alice.email, "code": "123456"}
        )

        (row,) = await world.logins()
        assert _row(row) == (alice.id, alice.email, "email_code", True, None)

    async def test_the_second_factor_step_repeats_the_first_steps_method(
        self, world: World
    ) -> None:
        secret = pyotp.random_base32()
        alice = await world.user("alice", password=PASSWORD, totp_enabled=True, totp_secret=secret)

        first = await world.call(
            "POST", "/api/auth/login", json={"email": alice.email, "password": PASSWORD}
        )
        await world.call(
            "POST",
            "/api/auth/login/verify-2fa",
            json={"temp_token": first.json()["temp_token"], "code": "000000"},
            expect=401,
        )

        rows = await world.logins()
        assert [_row(r) for r in rows] == [
            (alice.id, alice.email, "password", False, "2fa_pending"),
            (alice.id, alice.email, "password", False, "2fa_code_invalid"),
        ]


class TestReadingSignIns:
    async def test_the_users_own_list_and_the_administrators_list_show_the_method(
        self, world: World
    ) -> None:
        root = await world.user("root", admin=True, password=PASSWORD)
        await world.call(
            "POST", "/api/auth/login", json={"email": root.email, "password": PASSWORD}
        )

        own = (await world.call("GET", "/api/me/sessions", by=root)).json()
        listed = (await world.call("GET", "/api/admin/login-history", by=root)).json()

        assert [item["method"] for item in own["items"]] == ["password"]
        assert [item["method"] for item in listed["items"]] == ["password"]


class TestADisabledAccount:
    async def test_a_provider_sign_in_issues_nothing_and_is_recorded_as_refused(
        self, world: World, github: dict[str, Any]
    ) -> None:
        """Tokens were issued (every API then answered 403), the stored
        refresh token was overwritten and the attempt recorded as a success."""
        octo = await world.user("octo", is_active=False, refresh_token="kept-digest")
        await world.add(UserOAuthBinding(user_id=octo.id, provider="github", oauth_id="gh-1001"))

        response = await _callback(world)

        location = response.headers["location"]
        assert "error=account_disabled" in location and "access_token" not in location
        (row,) = await world.logins()
        assert _row(row) == (octo.id, octo.email, "oauth:github", False, "account_disabled")
        assert (await world.get(User, octo.id)).refresh_token == "kept-digest"

    async def test_a_disabled_account_matched_by_email_gets_no_binding(
        self, world: World, github: dict[str, Any]
    ) -> None:
        from sqlalchemy import select

        octo = await world.user("octo", is_active=False)  # verified email, no binding

        response = await _callback(world)

        assert "error=account_disabled" in response.headers["location"]
        async with world.factory() as db:
            assert (await db.execute(select(UserOAuthBinding))).scalars().all() == []
        (row,) = await world.logins()
        assert _row(row) == (octo.id, octo.email, "oauth:github", False, "account_disabled")


class TestAnAccountThatDoesNotExist:
    @pytest.fixture(autouse=True)
    def _fresh_throttle(self) -> Iterator[None]:
        from fim_one.core.audit import refusals

        refusals._throttle.reset()
        refusals._summary_pending.clear()
        yield
        refusals._throttle.reset()
        refusals._summary_pending.clear()

    async def test_a_password_typed_into_the_email_field_is_not_stored(self, world: World) -> None:
        typed = "Tr0ub4dor&3-my-real-password"

        await world.call(
            "POST", "/api/auth/login", json={"email": typed, "password": "x"}, expect=401
        )
        await world.call(
            "POST",
            "/api/auth/login",
            json={"email": "sk-live-0123456789abcdef0123456789abcdef@x.io", "password": "x"},
            expect=401,
        )

        first, second = await world.logins()
        assert first.email == f"(not an email address; {len(typed)} characters)"
        assert second.email == "(not an email address; 45 characters)"
        world.assert_absent(first, typed)
        world.assert_absent(second, "sk-live-0123456789abcdef0123456789abcdef")

    async def test_an_address_that_is_one_is_kept(self, world: World) -> None:
        await world.call(
            "POST", "/api/auth/login", json={"email": "ghost@x.io", "password": "x"}, expect=401
        )

        (row,) = await world.logins()
        assert _row(row) == (None, "ghost@x.io", "password", False, "user_not_found")

    async def test_an_overlong_address_is_refused_by_the_form(self, world: World) -> None:
        await world.call(
            "POST",
            "/api/auth/login",
            json={"email": "a" * 300 + "@x.io", "password": "x"},
            expect=422,
        )

        assert await world.logins() == []

    async def test_rows_for_unknown_accounts_from_one_address_are_bounded(
        self, world: World
    ) -> None:
        from fim_one.core.audit import refusals

        for n in range(25):
            await world.call(
                "POST",
                "/api/auth/login",
                json={"email": f"guess{n}@x.io", "password": "x"},
                expect=401,
            )

        assert len(await world.logins()) == 10
        # The rest are counted, and reported by a summary audit record.
        assert await refusals.write_summary("auth.login_unknown_account") is True
        (summary,) = await world.events("auth.login_unknown_account")
        assert summary.detail is not None and summary.detail.startswith("15 more")

    async def test_failures_for_an_account_that_exists_are_all_recorded(
        self, world: World
    ) -> None:
        alice = await world.user("alice", password=PASSWORD)

        for _ in range(15):
            await world.call(
                "POST",
                "/api/auth/login",
                json={"email": alice.email, "password": "wrong"},
                expect=401,
            )

        assert len(await world.logins()) == 15


class TestAFirstStepThatStillOwesTheSecondFactor:
    """It was stored as a successful sign-in: "my sessions" and the sign-in
    history showed a password-only attempt as signed in, and the success
    count included it."""

    async def test_it_is_neither_a_sign_in_nor_a_failure(self, world: World) -> None:
        secret = pyotp.random_base32()
        root = await world.user("root", admin=True)
        alice = await world.user("alice", password=PASSWORD, totp_enabled=True, totp_secret=secret)

        first = await world.call(
            "POST", "/api/auth/login", json={"email": alice.email, "password": PASSWORD}
        )

        (row,) = await world.logins()
        assert (row.success, row.failure_reason) == (False, "2fa_pending")
        own = (await world.call("GET", "/api/me/sessions", by=alice)).json()["items"]
        assert [(i["success"], i["outcome"]) for i in own] == [(False, "pending_second_factor")]
        stats = (await world.call("GET", "/api/admin/login-history/stats", by=root)).json()
        assert (stats["total_attempts"], stats["successful"], stats["failed"]) == (1, 0, 0)
        assert stats["recent_failures"] == 0
        for wanted, found in (("true", 0), ("false", 0)):
            listed = await world.call("GET", f"/api/admin/login-history?success={wanted}", by=root)
            assert len(listed.json()["items"]) == found
        everything = (await world.call("GET", "/api/admin/login-history", by=root)).json()
        assert [i["outcome"] for i in everything["items"]] == ["pending_second_factor"]

        # Completing the second step is the sign-in.
        await world.call(
            "POST",
            "/api/auth/login/verify-2fa",
            json={"temp_token": first.json()["temp_token"], "code": pyotp.TOTP(secret).now()},
        )
        stats = (await world.call("GET", "/api/admin/login-history/stats", by=root)).json()
        assert (stats["total_attempts"], stats["successful"], stats["failed"]) == (2, 1, 0)

    async def test_wrong_codes_are_still_counted_by_the_limiter(self, world: World) -> None:
        secret = pyotp.random_base32()
        alice = await world.user("alice", password=PASSWORD, totp_enabled=True, totp_secret=secret)
        first = await world.call(
            "POST", "/api/auth/login", json={"email": alice.email, "password": PASSWORD}
        )
        body = {"temp_token": first.json()["temp_token"], "code": "000000"}

        statuses = [
            (await world.call("POST", "/api/auth/login/verify-2fa", json=body, expect=(401, 429)))
            .status_code
            for _ in range(12)
        ]

        assert statuses[0] == 401 and statuses[-1] == 429

    async def test_a_row_written_before_reads_as_pending_too(self, world: World) -> None:
        from fim_one.db.models import LoginHistory

        root = await world.user("root", admin=True)
        async with world.factory() as db:
            db.add(LoginHistory(user_id=root.id, success=True, failure_reason="2fa_pending"))
            await db.commit()

        stats = (await world.call("GET", "/api/admin/login-history/stats", by=root)).json()
        listed = (await world.call("GET", "/api/admin/login-history", by=root)).json()

        assert (stats["successful"], stats["failed"]) == (0, 0)
        assert [i["outcome"] for i in listed["items"]] == ["pending_second_factor"]

"""Regression tests for the OAuth login/bind flow and provider helpers.

Originally covered two security/correctness fixes in ``_handle_login``:

1. Email-based auto-bind is gated on ``email_verified`` — matching an existing
   local account by an *unverified* provider email would let an attacker take
   over that account by setting their third-party email to the victim's.
2. The OAuth-issued refresh token is stored as a SHA-256 digest (matching the
   ``/refresh`` comparison), never as the raw long-lived JWT.

Extended (2026-05 audit, Track C2) to close the OAuth test blind spots:

* Per-provider ``email_verified`` extraction in ``fetch_user_info`` (GitHub,
  Google, Discord, Feishu), unknown-provider rejection, token-exchange failure
  surfacing, and the Feishu two-step token retrieval ordering.
* OAuth ``state`` validation, ``registration_mode`` gating for new users,
  username-collision auto-increment, the bind flow's mismatch/already-bound/
  already-connected guards, and ``/bind-ticket`` issuance.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import pytest_asyncio
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401 — register all models with metadata
from fim_one.db.base import Base
from fim_one.db.models import SystemSetting, User, UserOAuthBinding
from fim_one.web.api.admin import (
    SETTING_REGISTRATION_ENABLED,
    SETTING_REGISTRATION_MODE,
)
from fim_one.web.api.oauth import _handle_bind, _handle_login, issue_bind_ticket
from fim_one.web.auth import (
    create_oauth_state,
    verify_bind_ticket,
    verify_oauth_state,
)
from fim_one.web.oauth import (
    OAuthProvider,
    OAuthUserInfo,
    exchange_code,
    fetch_user_info,
    get_provider,
)


@pytest_asyncio.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _seed_user(session: AsyncSession, email: str) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=email,
    )
    session.add(user)
    await session.commit()
    return user


async def _binding_for(session: AsyncSession, provider: str, oauth_id: str) -> UserOAuthBinding | None:
    result = await session.execute(
        select(UserOAuthBinding).where(
            UserOAuthBinding.provider == provider,
            UserOAuthBinding.oauth_id == oauth_id,
        )
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# httpx mock helpers — mirror tests/test_http_request.py: MagicMock(spec=
# httpx.Response). exchange_code/fetch_user_info use AsyncClient.post/.get
# (not .request), so we patch those two methods directly.
# ---------------------------------------------------------------------------


def _json_response(
    body: object,
    *,
    status_code: int = 200,
    raise_status: bool = True,
) -> httpx.Response:
    """Build a fake ``httpx.Response`` whose ``.json()`` returns ``body``.

    When ``raise_status`` is False the response models a provider error: its
    ``raise_for_status`` raises ``httpx.HTTPStatusError`` like the real one.
    """
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = body
    if raise_status and status_code < 400:
        resp.raise_for_status.return_value = None
    else:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(spec=httpx.Request),
            response=resp,
        )
    return resp


def _make_provider(name: str) -> OAuthProvider:
    """Build an ``OAuthProvider`` directly (no env vars) for helper tests."""
    return OAuthProvider(
        name=name,
        client_id="client-id",
        client_secret="client-secret",
        authorize_url="https://example.test/authorize",
        token_url="https://example.test/token",
        user_info_url="https://example.test/userinfo",
        scopes=["email"],
    )


def _redirect_location(resp: RedirectResponse) -> str:
    """Extract the Location header from a FastAPI ``RedirectResponse``."""
    location = resp.headers["location"]
    assert isinstance(location, str)
    return location


def _query_param(url: str, key: str) -> str | None:
    """Return the first value of ``key`` from a URL's query string, if any."""
    params = parse_qs(urlparse(url).query)
    values = params.get(key)
    return values[0] if values else None


class TestEmailVerifiedGating:
    async def test_verified_email_auto_binds_existing_user(
        self, session: AsyncSession
    ) -> None:
        existing = await _seed_user(session, "victim@example.com")
        info = OAuthUserInfo(
            provider="github",
            id="gh-verified",
            username="attacker",
            email="victim@example.com",
            display_name="A",
            email_verified=True,
        )

        await _handle_login(session, info, "github", "http://frontend")

        binding = await _binding_for(session, "github", "gh-verified")
        assert binding is not None
        # Verified email → logs in as the existing local account.
        assert binding.user_id == existing.id

    async def test_unverified_email_does_not_hijack_existing_user(
        self, session: AsyncSession
    ) -> None:
        existing = await _seed_user(session, "victim@example.com")
        info = OAuthUserInfo(
            provider="github",
            id="gh-unverified",
            username="attacker",
            email="victim@example.com",  # same address, but unverified
            display_name="A",
            email_verified=False,
        )

        await _handle_login(session, info, "github", "http://frontend")

        binding = await _binding_for(session, "github", "gh-unverified")
        assert binding is not None
        # Unverified email must NOT match the existing account — a fresh user
        # is created instead, so the victim's account is never taken over.
        assert binding.user_id != existing.id


class TestRefreshTokenHashed:
    async def test_oauth_refresh_token_stored_as_digest(
        self, session: AsyncSession
    ) -> None:
        existing = await _seed_user(session, "user@example.com")
        info = OAuthUserInfo(
            provider="github",
            id="gh-1",
            username="user",
            email="user@example.com",
            display_name="U",
            email_verified=True,
        )

        await _handle_login(session, info, "github", "http://frontend")

        refreshed = await session.get(User, existing.id)
        assert refreshed is not None
        assert refreshed.refresh_token is not None
        # A SHA-256 hex digest: 64 hex chars, and never a raw JWT (which has dots).
        assert len(refreshed.refresh_token) == 64
        assert "." not in refreshed.refresh_token


# ===========================================================================
# Per-provider email_verified extraction in fetch_user_info
# ===========================================================================


class TestFetchUserInfoEmailVerified:
    async def test_github_primary_verified_email(self) -> None:
        provider = _make_provider("github")
        profile = _json_response(
            {"id": 42, "login": "octocat", "name": "The Octocat", "email": "public@gh.test"}
        )
        emails = _json_response(
            [
                {"email": "secondary@gh.test", "primary": False, "verified": True},
                {"email": "primary@gh.test", "primary": True, "verified": True},
            ]
        )
        with patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            side_effect=[profile, emails],
        ):
            info = await fetch_user_info(provider, "tok")

        assert info.provider == "github"
        assert info.id == "42"
        assert info.username == "octocat"
        # Primary email from /user/emails wins over the public profile email.
        assert info.email == "primary@gh.test"
        assert info.email_verified is True

    async def test_github_primary_unverified_email_not_trusted(self) -> None:
        provider = _make_provider("github")
        profile = _json_response({"id": 7, "login": "spoofer", "name": None, "email": None})
        emails = _json_response(
            [{"email": "victim@corp.test", "primary": True, "verified": False}]
        )
        with patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            side_effect=[profile, emails],
        ):
            info = await fetch_user_info(provider, "tok")

        assert info.email == "victim@corp.test"
        # Provider asserts the primary email is NOT verified → untrusted.
        assert info.email_verified is False

    async def test_github_emails_endpoint_unavailable_falls_back_to_profile(self) -> None:
        """When /user/emails 404s, fall back to the profile email, unverified."""
        provider = _make_provider("github")
        profile = _json_response({"id": 9, "login": "fallback", "name": "F", "email": "profile@gh.test"})
        emails = _json_response([], status_code=404, raise_status=False)
        with patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            side_effect=[profile, emails],
        ):
            info = await fetch_user_info(provider, "tok")

        # /user/emails returned non-200 → fall back to profile email, no verify signal.
        assert info.email == "profile@gh.test"
        assert info.email_verified is False

    async def test_google_verified_email_flag(self) -> None:
        provider = _make_provider("google")
        body = _json_response(
            {
                "id": "g-123",
                "email": "person@gmail.test",
                "name": "A Person",
                "verified_email": True,
            }
        )
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=body):
            info = await fetch_user_info(provider, "tok")

        assert info.provider == "google"
        assert info.id == "g-123"
        assert info.username == "person"  # local part of email
        assert info.email == "person@gmail.test"
        assert info.email_verified is True

    async def test_google_unverified_email_flag(self) -> None:
        provider = _make_provider("google")
        body = _json_response(
            {"id": "g-456", "email": "person@gmail.test", "name": "A", "verified_email": False}
        )
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=body):
            info = await fetch_user_info(provider, "tok")

        assert info.email_verified is False

    async def test_discord_verified_flag(self) -> None:
        provider = _make_provider("discord")
        body = _json_response(
            {
                "id": "d-1",
                "username": "disco",
                "global_name": "Disco Naut",
                "email": "d@discord.test",
                "verified": True,
            }
        )
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=body):
            info = await fetch_user_info(provider, "tok")

        assert info.provider == "discord"
        assert info.id == "d-1"
        assert info.username == "disco"
        assert info.display_name == "Disco Naut"
        assert info.email == "d@discord.test"
        assert info.email_verified is True

    async def test_discord_unverified_flag(self) -> None:
        provider = _make_provider("discord")
        body = _json_response(
            {"id": "d-2", "username": "disco", "email": "d@discord.test", "verified": False}
        )
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=body):
            info = await fetch_user_info(provider, "tok")

        # global_name absent → display_name falls back to username.
        assert info.display_name == "disco"
        assert info.email_verified is False

    async def test_feishu_enterprise_email_is_verified(self) -> None:
        provider = _make_provider("feishu")
        body = _json_response(
            {
                "data": {
                    "open_id": "ou_abcdef1234",
                    "name": "李雷",
                    "en_name": "Lei Li",
                    "enterprise_email": "lei.li@corp.test",
                }
            }
        )
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=body):
            info = await fetch_user_info(provider, "tok")

        assert info.provider == "feishu"
        assert info.id == "ou_abcdef1234"
        assert info.username == "lei_li"  # en_name lowercased, spaces → underscores
        # Enterprise email is administratively controlled → trusted.
        assert info.email == "lei.li@corp.test"
        assert info.email_verified is True

    async def test_feishu_personal_email_not_verified(self) -> None:
        provider = _make_provider("feishu")
        body = _json_response(
            {
                "data": {
                    "open_id": "ou_personal99",
                    "name": "Solo",
                    "en_name": "Solo",
                    "email": "solo@personal.test",  # self-set, no enterprise_email
                }
            }
        )
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=body):
            info = await fetch_user_info(provider, "tok")

        assert info.email == "solo@personal.test"
        # No enterprise_email → the self-set personal email is untrusted.
        assert info.email_verified is False

    async def test_unknown_provider_raises_value_error(self) -> None:
        provider = _make_provider("myspace")
        body = _json_response({"id": "x"})
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=body):
            with pytest.raises(ValueError, match="Unknown provider"):
                await fetch_user_info(provider, "tok")


# ===========================================================================
# Token exchange — failure surfacing + Feishu two-step ordering
# ===========================================================================


class TestExchangeCode:
    async def test_standard_provider_returns_access_token(self) -> None:
        provider = _make_provider("google")
        resp = _json_response({"access_token": "ya29.token", "token_type": "Bearer"})
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=resp):
            token = await exchange_code(provider, "auth-code", "https://cb.test")
        assert token == "ya29.token"

    async def test_token_exchange_non_200_surfaces_http_error(self) -> None:
        """A non-200 token response is surfaced (raise_for_status), not swallowed."""
        provider = _make_provider("github")
        resp = _json_response(
            {"error": "bad_verification_code"}, status_code=401, raise_status=False
        )
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=resp):
            with pytest.raises(httpx.HTTPStatusError):
                await exchange_code(provider, "auth-code", "https://cb.test")

    async def test_token_exchange_200_without_token_raises_value_error(self) -> None:
        """A 200 body that omits access_token surfaces a ValueError, not a crash."""
        provider = _make_provider("discord")
        resp = _json_response({"scope": "identify email"})  # no access_token
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=resp):
            with pytest.raises(ValueError, match="No access_token"):
                await exchange_code(provider, "auth-code", "https://cb.test")

    async def test_feishu_two_step_token_retrieval_order(self) -> None:
        """Feishu: app_access_token POST first, then the code→user-token POST."""
        provider = _make_provider("feishu")
        app_token_resp = _json_response({"app_access_token": "app-tok-xyz"})
        user_token_resp = _json_response({"data": {"access_token": "user-tok-abc"}})
        post_mock = AsyncMock(side_effect=[app_token_resp, user_token_resp])
        with patch("httpx.AsyncClient.post", post_mock):
            token = await exchange_code(provider, "auth-code", "https://cb.test")

        assert token == "user-tok-abc"
        assert post_mock.await_count == 2
        # Call 1: app_access_token endpoint with app credentials.
        first_call = post_mock.await_args_list[0]
        assert first_call.args[0].endswith("/auth/v3/app_access_token/internal")
        assert first_call.kwargs["json"]["app_id"] == "client-id"
        assert first_call.kwargs["json"]["app_secret"] == "client-secret"
        # Call 2: token_url, authorized with the app access token from call 1.
        second_call = post_mock.await_args_list[1]
        assert second_call.args[0] == provider.token_url
        assert second_call.kwargs["headers"]["Authorization"] == "Bearer app-tok-xyz"
        assert second_call.kwargs["json"]["code"] == "auth-code"

    async def test_feishu_missing_app_access_token_raises(self) -> None:
        provider = _make_provider("feishu")
        bad_app_resp = _json_response({"code": 99991663, "msg": "no token"})
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=bad_app_resp):
            with pytest.raises(ValueError, match="app_access_token"):
                await exchange_code(provider, "auth-code", "https://cb.test")


# ===========================================================================
# get_provider config resolution + OAuth state validation
# ===========================================================================


class TestGetProvider:
    def test_unconfigured_provider_returns_none(self) -> None:
        import os

        with patch.dict("os.environ", clear=False):
            os.environ.pop("GITHUB_CLIENT_ID", None)
            os.environ.pop("GITHUB_CLIENT_SECRET", None)
            # No credentials in the environment → provider is unconfigured.
            assert get_provider("github") is None

    def test_unknown_provider_name_returns_none(self) -> None:
        assert get_provider("not-a-provider") is None

    def test_configured_github_provider(self) -> None:
        env = {"GITHUB_CLIENT_ID": "gh-id", "GITHUB_CLIENT_SECRET": "gh-secret"}
        with patch.dict("os.environ", env, clear=False):
            provider = get_provider("github")
        assert provider is not None
        assert provider.name == "github"
        assert provider.client_id == "gh-id"
        assert provider.client_secret == "gh-secret"

    def test_feishu_uses_app_id_app_secret_env(self) -> None:
        env = {"FEISHU_APP_ID": "feishu-app", "FEISHU_APP_SECRET": "feishu-secret"}
        with patch.dict("os.environ", env, clear=False):
            provider = get_provider("feishu")
        assert provider is not None
        # Feishu maps APP_ID/APP_SECRET onto the standard client_id/secret slots.
        assert provider.client_id == "feishu-app"
        assert provider.client_secret == "feishu-secret"


class TestOAuthStateValidation:
    def test_valid_state_round_trips(self) -> None:
        token = create_oauth_state(action="login", user_id=None)
        entry = verify_oauth_state(token)
        assert entry is not None
        assert entry["action"] == "login"
        assert entry["type"] == "oauth_state"

    def test_bind_state_carries_user_id(self) -> None:
        token = create_oauth_state(action="bind", user_id="user-99")
        entry = verify_oauth_state(token)
        assert entry is not None
        assert entry["action"] == "bind"
        assert entry["sub"] == "user-99"

    def test_garbage_state_rejected(self) -> None:
        assert verify_oauth_state("not-a-jwt") is None

    def test_expired_state_rejected(self) -> None:
        token = create_oauth_state(action="login", user_id=None, ttl=-1)
        assert verify_oauth_state(token) is None

    def test_wrong_type_token_rejected(self) -> None:
        # A bind ticket is signed with the same key but is not an oauth_state.
        from fim_one.web.auth import create_bind_ticket

        ticket = create_bind_ticket("user-1")
        assert verify_oauth_state(ticket) is None


# ===========================================================================
# registration_mode gating + username-collision auto-increment (login flow)
# ===========================================================================


async def _set_setting(session: AsyncSession, key: str, value: str) -> None:
    session.add(SystemSetting(key=key, value=value))
    await session.commit()


def _new_user_info(provider: str = "github", oauth_id: str = "new-1") -> OAuthUserInfo:
    return OAuthUserInfo(
        provider=provider,
        id=oauth_id,
        username="newcomer",
        email="newcomer@example.com",
        display_name="New Comer",
        email_verified=True,
    )


class TestRegistrationModeGating:
    async def test_disabled_mode_blocks_new_user(self, session: AsyncSession) -> None:
        await _set_setting(session, SETTING_REGISTRATION_MODE, "disabled")
        resp = await _handle_login(session, _new_user_info(), "github", "http://frontend")
        location = _redirect_location(resp)
        assert _query_param(location, "error") == "registration_disabled"
        # No user / binding was created.
        assert await _binding_for(session, "github", "new-1") is None

    async def test_invite_mode_blocks_new_user(self, session: AsyncSession) -> None:
        # OAuth can't carry an invite code, so "invite" mode also blocks new users.
        await _set_setting(session, SETTING_REGISTRATION_MODE, "invite")
        resp = await _handle_login(session, _new_user_info(), "github", "http://frontend")
        location = _redirect_location(resp)
        assert _query_param(location, "error") == "registration_disabled"
        assert await _binding_for(session, "github", "new-1") is None

    async def test_open_mode_allows_new_user(self, session: AsyncSession) -> None:
        await _set_setting(session, SETTING_REGISTRATION_MODE, "open")
        resp = await _handle_login(session, _new_user_info(), "github", "http://frontend")
        location = _redirect_location(resp)
        # Success → callback fragment with tokens, not an error.
        assert "/auth/callback" in location
        assert "error=" not in location
        binding = await _binding_for(session, "github", "new-1")
        assert binding is not None

    async def test_legacy_registration_enabled_false_blocks(self, session: AsyncSession) -> None:
        # No registration_mode set → falls back to the legacy boolean.
        await _set_setting(session, SETTING_REGISTRATION_ENABLED, "false")
        resp = await _handle_login(session, _new_user_info(), "github", "http://frontend")
        assert _query_param(_redirect_location(resp), "error") == "registration_disabled"

    async def test_no_settings_defaults_to_open(self, session: AsyncSession) -> None:
        # Neither setting present → defaults to "open" (new user allowed).
        resp = await _handle_login(session, _new_user_info(), "github", "http://frontend")
        assert await _binding_for(session, "github", "new-1") is not None
        assert "error=" not in _redirect_location(resp)


class TestUsernameCollision:
    async def test_username_collision_auto_increments(self, session: AsyncSession) -> None:
        # Seed a user already holding the derived username "newcomer".
        taken = User(
            id=str(uuid.uuid4()),
            username="newcomer",
            email="someone-else@example.com",
        )
        session.add(taken)
        await session.commit()

        # New OAuth user derives username "newcomer" but its verified email does
        # not match the seeded user → a fresh account with a suffixed username.
        info = OAuthUserInfo(
            provider="github",
            id="collide-1",
            username="newcomer",
            email="fresh@example.com",
            display_name="Fresh",
            email_verified=True,
        )
        await _handle_login(session, info, "github", "http://frontend")

        binding = await _binding_for(session, "github", "collide-1")
        assert binding is not None
        created = await session.get(User, binding.user_id)
        assert created is not None
        assert created.id != taken.id
        # First free suffix is chosen.
        assert created.username == "newcomer_1"

    async def test_username_collision_multiple_suffixes(self, session: AsyncSession) -> None:
        for name in ("dup", "dup_1", "dup_2"):
            session.add(User(id=str(uuid.uuid4()), username=name, email=f"{name}@x.test"))
        await session.commit()

        info = OAuthUserInfo(
            provider="discord",
            id="dup-oauth",
            username="dup",
            email="newdup@example.com",
            display_name="Dup",
            email_verified=True,
        )
        await _handle_login(session, info, "discord", "http://frontend")

        binding = await _binding_for(session, "discord", "dup-oauth")
        assert binding is not None
        created = await session.get(User, binding.user_id)
        assert created is not None
        assert created.username == "dup_3"


# ===========================================================================
# Bind flow — _handle_bind guards
# ===========================================================================


def _bind_state(user_id: str) -> dict[str, object]:
    return {"type": "oauth_state", "action": "bind", "sub": user_id}


class TestBindFlow:
    async def test_bind_email_mismatch_rejected(self, session: AsyncSession) -> None:
        user = await _seed_user(session, "owner@example.com")
        info = OAuthUserInfo(
            provider="github",
            id="gh-bind-1",
            username="x",
            email="different@example.com",
            display_name="X",
            email_verified=True,
        )
        resp = await _handle_bind(
            session, _bind_state(user.id), info, "github", "http://frontend"
        )
        assert _query_param(_redirect_location(resp), "bind_error") == "email_mismatch"
        assert await _binding_for(session, "github", "gh-bind-1") is None

    async def test_bind_already_bound_to_other_identity_rejected(
        self, session: AsyncSession
    ) -> None:
        # The (provider, oauth_id) identity is already bound to ANOTHER user.
        other = await _seed_user(session, "other@example.com")
        session.add(
            UserOAuthBinding(
                user_id=other.id,
                provider="github",
                oauth_id="gh-shared",
                email="shared@example.com",
                display_name="Shared",
            )
        )
        await session.commit()

        user = await _seed_user(session, "shared@example.com")
        info = OAuthUserInfo(
            provider="github",
            id="gh-shared",
            username="x",
            email="shared@example.com",
            display_name="Shared",
            email_verified=True,
        )
        resp = await _handle_bind(
            session, _bind_state(user.id), info, "github", "http://frontend"
        )
        assert _query_param(_redirect_location(resp), "bind_error") == "already_bound"

    async def test_bind_already_connected_same_provider_rejected(
        self, session: AsyncSession
    ) -> None:
        # This user already has a binding for this provider (different oauth_id).
        user = await _seed_user(session, "me@example.com")
        session.add(
            UserOAuthBinding(
                user_id=user.id,
                provider="github",
                oauth_id="gh-old",
                email="me@example.com",
                display_name="Me",
            )
        )
        await session.commit()

        info = OAuthUserInfo(
            provider="github",
            id="gh-new",
            username="x",
            email="me@example.com",
            display_name="Me",
            email_verified=True,
        )
        resp = await _handle_bind(
            session, _bind_state(user.id), info, "github", "http://frontend"
        )
        assert _query_param(_redirect_location(resp), "bind_error") == "already_connected"

    async def test_bind_success_creates_binding(self, session: AsyncSession) -> None:
        user = await _seed_user(session, "bind-ok@example.com")
        info = OAuthUserInfo(
            provider="discord",
            id="d-bind-ok",
            username="x",
            email="bind-ok@example.com",
            display_name="OK",
            email_verified=True,
        )
        resp = await _handle_bind(
            session, _bind_state(user.id), info, "discord", "http://frontend"
        )
        location = _redirect_location(resp)
        assert _query_param(location, "bind_success") == "discord"
        assert "bind_error" not in location
        binding = await _binding_for(session, "discord", "d-bind-ok")
        assert binding is not None
        assert binding.user_id == user.id

    async def test_bind_unknown_user_rejected(self, session: AsyncSession) -> None:
        info = OAuthUserInfo(
            provider="github",
            id="gh-ghost",
            username="x",
            email="ghost@example.com",
            display_name="Ghost",
            email_verified=True,
        )
        resp = await _handle_bind(
            session, _bind_state("does-not-exist"), info, "github", "http://frontend"
        )
        assert _query_param(_redirect_location(resp), "bind_error") == "user_not_found"


# ===========================================================================
# /bind-ticket issuance
# ===========================================================================


class TestBindTicketIssuance:
    async def test_issue_bind_ticket_returns_verifiable_ticket(self) -> None:
        user = User(id="ticket-user", username="tu", email="tu@example.com")
        result = await issue_bind_ticket(current_user=user)
        assert "ticket" in result
        # The issued ticket verifies back to the same user id.
        assert verify_bind_ticket(result["ticket"]) == "ticket-user"

    async def test_issued_ticket_is_not_a_plain_oauth_state(self) -> None:
        user = User(id="ticket-user-2", username="tu2", email="tu2@example.com")
        result = await issue_bind_ticket(current_user=user)
        # A bind ticket must NOT validate as an oauth_state (distinct token types).
        assert verify_oauth_state(result["ticket"]) is None

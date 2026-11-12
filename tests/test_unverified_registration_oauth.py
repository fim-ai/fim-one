"""An address nobody proved is never a way into someone else's sign-in.

Properties:

* A registration that did not prove its address (no emailed code) is
  stored with ``email_verified = False``; one that did, with ``True``.
* A provider sign-in for an address is never placed in an account whose
  address is unproven, and is never blocked by one: the owner gets an
  account of their own, which the holder of the unproven account cannot
  enter by any sign-in method.
* Lookups by address stay well-defined when two accounts hold it.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy import make_url, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.db import get_session
from fim_one.db.base import Base
from fim_one.db.models import User, UserOAuthBinding
from fim_one.db.models.email_verification import EmailVerification
from fim_one.web.api import auth as auth_api
from fim_one.web.api.oauth import _handle_login
from fim_one.web.exceptions import AppError
from fim_one.web.oauth import OAuthUserInfo

_ADDRESS = "owner@example.com"
_FIRST_PW = "first-password-1"


class _Env:
    def __init__(self, client: AsyncClient, sessions: async_sessionmaker[AsyncSession], settings: dict[str, str]) -> None:
        self.client = client
        self.sessions = sessions
        self.settings = settings

    async def users(self, email: str = _ADDRESS) -> list[User]:
        async with self.sessions() as s:
            return list((await s.scalars(select(User).where(User.email == email).order_by(User.created_at))).all())

    async def oauth_login(self, *, provider: str = "google", oauth_id: str = "g-owner",
                          email: str = _ADDRESS, verified: bool = True) -> str:
        """Sign in through a provider; return the id of the account entered."""
        async with self.sessions() as s:
            await _handle_login(
                s,
                OAuthUserInfo(provider=provider, id=oauth_id, username="v", email=email,
                              display_name="V", email_verified=verified),
                provider,
                "http://frontend",
            )
            binding = (await s.scalars(select(UserOAuthBinding).where(
                UserOAuthBinding.provider == provider, UserOAuthBinding.oauth_id == oauth_id))).one()
            return str(binding.user_id)

    async def add_code(self, purpose: str, email: str = _ADDRESS, code: str = "424242") -> None:
        async with self.sessions() as s:
            s.add(EmailVerification(email=email, code=code, purpose=purpose,
                                    expires_at=datetime.now(UTC) + timedelta(minutes=5)))
            await s.commit()

    async def register(self, email: str = _ADDRESS, password: str = _FIRST_PW, **extra: Any) -> Any:
        return await self.client.post("/api/auth/register", json={"email": email, "password": password, **extra})

    async def password_login(self, email: str, password: str) -> Any:
        return await self.client.post("/api/auth/login", json={"email": email, "password": password})


_PG = os.environ.get("FIM_TEST_PG_URL")
_BACKENDS = [
    "sqlite",
    pytest.param("postgresql", marks=pytest.mark.skipif(not _PG, reason="FIM_TEST_PG_URL not set")),
]


@pytest_asyncio.fixture(params=_BACKENDS)
async def env(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[_Env]:
    if request.param == "sqlite":
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    else:
        engine = create_async_engine(make_url(_PG or "").set(drivername="postgresql+asyncpg"))
    async with engine.begin() as conn:
        if request.param == "postgresql":
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    # An installed system: the first account is the administrator's.
    async with sessions() as s:
        s.add(User(email="admin@example.com", username="admin", password_hash="x", is_admin=True, email_verified=True))
        await s.commit()

    settings = {"email_verification_enabled": "false", "registration_mode": "open"}

    async def _setting(db: Any, key: str, default: str = "") -> str:
        return settings.get(key, default)

    monkeypatch.setattr(auth_api, "get_setting", _setting)
    monkeypatch.setattr("fim_one.web.api.oauth.get_setting", _setting)
    monkeypatch.setattr(auth_api, "_smtp_configured", lambda: True)
    monkeypatch.setattr(auth_api, "spawn_background", lambda coro: coro.close())

    app = FastAPI()
    app.include_router(auth_api.router)

    @app.exception_handler(AppError)
    async def _app_error(_request: Any, exc: AppError) -> JSONResponse:
        return JSONResponse({"error_code": exc.error_code}, status_code=exc.status_code)

    async def _session() -> AsyncIterator[AsyncSession]:
        async with sessions() as s:
            yield s

    app.dependency_overrides[get_session] = _session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        yield _Env(client, sessions, settings)
    await engine.dispose()


class TestRegistrationRecordsWhetherTheAddressWasProven:
    async def test_without_a_code_the_address_is_unproven(self, env: _Env) -> None:
        r = await env.register()
        assert r.status_code == 200
        (user,) = await env.users()
        assert user.email_verified is False

    async def test_smtp_missing_means_unproven_even_when_verification_is_on(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env.settings["email_verification_enabled"] = "true"
        monkeypatch.setattr(auth_api, "_smtp_configured", lambda: False)
        assert (await env.register()).status_code == 200
        (user,) = await env.users()
        assert user.email_verified is False

    async def test_a_code_supplied_but_not_required_proves_nothing(self, env: _Env) -> None:
        await env.add_code("register")
        assert (await env.register(verification_code="000000")).status_code == 200
        (user,) = await env.users()
        assert user.email_verified is False

    async def test_with_the_emailed_code_the_address_is_proven(self, env: _Env) -> None:
        env.settings["email_verification_enabled"] = "true"
        await env.add_code("register")
        assert (await env.register(verification_code="111111")).status_code != 200
        assert (await env.register(verification_code="424242")).status_code == 200
        (user,) = await env.users()
        assert user.email_verified is True

    async def test_the_first_account_is_the_installers(self, tmp_path: Path, env: _Env) -> None:
        async with env.sessions() as s:
            for user in (await s.scalars(select(User))).all():
                await s.delete(user)
            await s.commit()
        assert (await env.register(email="root@example.com")).status_code == 200
        (user,) = await env.users("root@example.com")
        assert user.is_admin is True and user.email_verified is True


class TestProviderSignInIsNeitherMergedNorBlocked:
    @pytest.mark.parametrize("provider_verified", [True, False])
    async def test_owner_gets_an_account_of_their_own(
        self, env: _Env, provider_verified: bool
    ) -> None:
        # An account is registered for the address with a password and no code.
        assert (await env.register()).status_code == 200
        (unproven,) = await env.users()

        # The address's owner signs in with a provider for that address.
        owner_id = await env.oauth_login(verified=provider_verified)

        assert owner_id != unproven.id                      # not merged
        accounts = {u.id: u for u in await env.users()}
        assert set(accounts) == {unproven.id, owner_id}      # and not blocked
        assert accounts[owner_id].password_hash is None

        # That password opens the unproven account only.
        r = await env.password_login(_ADDRESS, _FIRST_PW)
        assert r.status_code == 200
        assert r.json()["user"]["id"] == unproven.id

        # Signing in again lands in the same, separate account.
        assert await env.oauth_login(verified=provider_verified) == owner_id

    async def test_an_emailed_code_signs_in_to_the_proven_account(self, env: _Env) -> None:
        assert (await env.register()).status_code == 200
        (unproven,) = await env.users()
        owner_id = await env.oauth_login()

        await env.add_code("login")
        r = await env.client.post("/api/auth/login-with-code", json={"email": _ADDRESS, "code": "424242"})
        assert r.status_code == 200
        assert r.json()["user"]["id"] == owner_id

        await env.add_code("reset_password")
        r = await env.client.post("/api/auth/verify-forgot-code", json={"email": _ADDRESS, "code": "424242"})
        token = r.json()["data"]["reset_token"]
        r = await env.client.post("/api/auth/forgot-password", json={
            "email": _ADDRESS, "reset_token": token, "new_password": "owners-reset-password"})
        assert r.status_code == 200
        accounts = {u.id: u for u in await env.users()}
        assert accounts[owner_id].password_hash is not None
        # Each password still opens its own account.
        assert (await env.password_login(_ADDRESS, "owners-reset-password")).json()["user"]["id"] == owner_id
        assert (await env.password_login(_ADDRESS, _FIRST_PW)).json()["user"]["id"] == unproven.id
        assert (await env.password_login(_ADDRESS, "neither-password")).status_code == 401

    async def test_two_holders_of_an_address_do_not_break_lookups(self, env: _Env) -> None:
        assert (await env.register()).status_code == 200
        await env.oauth_login()
        assert len(await env.users()) == 2

        assert (await env.register()).json()["error_code"] == "email_already_registered"
        r = await env.client.post("/api/auth/send-verification-code", json={"email": _ADDRESS})
        assert r.json()["error_code"] == "email_already_registered"
        # A second provider, same proven address: the proven account, not a third one.
        first = await env.oauth_login()
        assert await env.oauth_login(provider="github", oauth_id="gh-owner") == first
        assert len(await env.users()) == 2

    async def test_a_proven_registration_still_binds(self, env: _Env) -> None:
        """The ordinary case is unchanged: same person, proven address, one account."""
        env.settings["email_verification_enabled"] = "true"
        await env.add_code("register")
        assert (await env.register(verification_code="424242")).status_code == 200
        (owner,) = await env.users()
        assert await env.oauth_login() == owner.id
        assert len(await env.users()) == 1

    async def test_an_unproven_provider_address_never_enters_a_proven_account(self, env: _Env) -> None:
        env.settings["email_verification_enabled"] = "true"
        await env.add_code("register")
        assert (await env.register(verification_code="424242")).status_code == 200
        (owner,) = await env.users()
        assert await env.oauth_login(provider="github", oauth_id="gh-other", verified=False) != owner.id


class TestAddressLookupsElsewhere:
    async def test_adding_an_org_member_by_address_names_the_proven_account(self, env: _Env) -> None:
        """An organization that adds the address gets the account that proved it."""
        from fim_one.db.models.organization import Organization, OrgMembership
        from fim_one.web.api import organizations
        from fim_one.web.auth import get_current_user

        assert (await env.register()).status_code == 200
        (unproven,) = await env.users()
        owner_id = await env.oauth_login()

        async with env.sessions() as s:
            admin = (await s.scalars(select(User).where(User.email == "admin@example.com"))).one()
            org = Organization(name="Org", slug="org", owner_id=admin.id)
            s.add(org)
            await s.flush()
            s.add(OrgMembership(org_id=org.id, user_id=admin.id, role="owner"))
            await s.commit()
            org_id, admin_id = org.id, admin.id

        app = FastAPI()
        app.include_router(organizations.router)

        async def _session() -> AsyncIterator[AsyncSession]:
            async with env.sessions() as s:
                yield s

        async def _admin() -> User:
            async with env.sessions() as s:
                user = await s.get(User, admin_id)
                assert user is not None
                return user

        app.dependency_overrides[get_session] = _session
        app.dependency_overrides[get_current_user] = _admin
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            prefix = organizations.router.prefix
            r = await client.post(f"{prefix}/{org_id}/members", json={"email": _ADDRESS})
        assert r.status_code == 200, r.text

        async with env.sessions() as s:
            members = set((await s.scalars(
                select(OrgMembership.user_id).where(OrgMembership.org_id == org_id))).all())
        assert owner_id in members
        assert unproven.id not in members


async def _verification_in_force(env: _Env, monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    """The stored setting and SMTP, as the address lookups read them."""
    from fim_one.db.models import SystemSetting

    for name in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
        monkeypatch.setenv(name, "x")
    async with env.sessions() as s:
        s.add(SystemSetting(key="email_verification_enabled", value="true" if on else "false"))
        await s.commit()


class TestAnUnprovenAddressNamesNoAccountWhenVerificationIsOn:
    async def _org(self, env: _Env) -> tuple[str, str]:
        from fim_one.db.models.organization import Organization, OrgMembership

        async with env.sessions() as s:
            admin = (await s.scalars(select(User).where(User.email == "admin@example.com"))).one()
            org = Organization(name="Org", slug="org", owner_id=admin.id)
            s.add(org)
            await s.flush()
            s.add(OrgMembership(org_id=org.id, user_id=admin.id, role="owner"))
            await s.commit()
            return org.id, admin.id

    async def _add(self, env: _Env, org_id: str, admin_id: str, body: dict[str, str]) -> Any:
        from fim_one.web.api import organizations
        from fim_one.web.auth import get_current_user

        app = FastAPI()
        app.include_router(organizations.router)

        @app.exception_handler(AppError)
        async def _app_error(_request: Any, exc: AppError) -> JSONResponse:
            return JSONResponse({"error_code": exc.error_code}, status_code=exc.status_code)

        async def _session() -> AsyncIterator[AsyncSession]:
            async with env.sessions() as s:
                yield s

        async def _admin() -> User:
            async with env.sessions() as s:
                user = await s.get(User, admin_id)
                assert user is not None
                return user

        app.dependency_overrides[get_session] = _session
        app.dependency_overrides[get_current_user] = _admin
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            return await client.post(f"{organizations.router.prefix}/{org_id}/members", json=body)

    @pytest.mark.parametrize("on", [True, False])
    async def test_adding_an_org_member(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch, on: bool
    ) -> None:
        assert (await env.register()).status_code == 200
        (unproven,) = await env.users()
        await _verification_in_force(env, monkeypatch, on)
        org_id, admin_id = await self._org(env)

        r = await self._add(env, org_id, admin_id, {"email": _ADDRESS})
        if on:
            assert r.status_code == 404 and r.json()["error_code"] == "user_not_found"
        else:
            assert r.status_code == 200, r.text
        if on:
            # A user name still names the account.
            async with env.sessions() as s:
                row = await s.get(User, unproven.id)
                assert row is not None
                row.username = "holder"
                await s.commit()
            r = await self._add(env, org_id, admin_id, {"username": "holder"})
            assert r.status_code == 200, r.text

    @pytest.mark.parametrize("on", [True, False])
    async def test_assigning_an_approval(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch, on: bool
    ) -> None:
        from types import SimpleNamespace

        from fim_one.core.assembly.confirmation import validate_assignee
        from fim_one.core.assembly.errors import AssemblyRefusal

        assert (await env.register()).status_code == 200
        (unproven,) = await env.users()
        await _verification_in_force(env, monkeypatch, on)
        me = SimpleNamespace(user_id=unproven.id, org_ids=set())
        async with env.sessions() as s:
            if on:
                with pytest.raises(AssemblyRefusal):
                    await validate_assignee(s, assignee=_ADDRESS, initiator=me, org_id=None)
            else:
                assert await validate_assignee(s, assignee=_ADDRESS, initiator=me, org_id=None) == unproven.id
            # An id still names exactly that account.
            assert await validate_assignee(s, assignee=unproven.id, initiator=me, org_id=None) == unproven.id


class TestFirstProofOfAnAddress:
    """An emailed code accepted for an account whose address was unproven
    makes the account the code holder's alone: earlier ways in are closed
    and what was left to run on the account's behalf is switched off."""

    async def _unproven_with_every_way_in(self, env: _Env) -> dict[str, Any]:
        import pyotp

        from fim_one.db.models import ApiKey
        from fim_one.web.auth import create_access_token

        assert (await env.register()).status_code == 200
        (unproven,) = await env.users()
        async with env.sessions() as s:
            user = await s.get(User, unproven.id)
            assert user is not None
            user.totp_enabled = True
            user.totp_secret = pyotp.random_base32()
            user.totp_backup_codes = '["x"]'
            user.oauth_provider, user.oauth_id = "github", "gh-other"
            s.add(ApiKey(user_id=unproven.id, name="k", key_prefix="fim_abcd", key_hash="h1", is_active=True))
            s.add(UserOAuthBinding(user_id=unproven.id, provider="github", oauth_id="gh-other", email=_ADDRESS))
            await s.commit()
            old_refresh = user.refresh_token
            await self._set_up_on_behalf(s, user)
        assert old_refresh is not None
        return {"id": unproven.id, "old_token": create_access_token(unproven.id, _ADDRESS), "old_refresh": old_refresh}

    async def _set_up_on_behalf(self, s: AsyncSession, user: User) -> None:
        """What an account holder can leave running for later."""
        from fim_one.db.models import Agent, Workflow, WorkflowRun
        from fim_one.db.models.channel import Channel, ConfirmationRequest
        from fim_one.db.models.connector import Connector
        from fim_one.db.models.connector_credential import ConnectorCredential
        from fim_one.db.models.mcp_server import MCPServer
        from fim_one.db.models.mcp_server_credential import MCPServerCredential
        from fim_one.db.models.model_config import ModelConfig
        from fim_one.db.models.organization import Organization, OrgMembership

        admin = (await s.scalars(select(User).where(User.email == "admin@example.com"))).one()
        theirs = Organization(id="org-theirs", name="Theirs", slug="theirs", owner_id=admin.id)
        own = Organization(id="org-own", name="Own", slug="own", owner_id=user.id)
        s.add_all([theirs, own])
        await s.flush()
        s.add_all([
            OrgMembership(org_id="org-theirs", user_id=user.id, role="admin"),
            OrgMembership(org_id="org-own", user_id=user.id, role="owner"),
            Workflow(id="wf-1", user_id=user.id, name="wf", blueprint={}, schedule_cron="* * * * *",
                     schedule_enabled=True, webhook_url="https://hooks.example.com/x",
                     api_key_hash="a" * 64, api_key_prefix="wf_abcd"),
            ModelConfig(id="mc-ep", user_id=user.id, name="ep", provider="openai", model_name="m",
                        base_url="https://llm.example.com/v1", api_key="sk-x"),
            ModelConfig(id="mc-plain", user_id=user.id, name="plain", provider="openai", model_name="m"),
            Agent(id="ag-inline", user_id=user.id, name="inline",
                  model_config_json={"base_url": "https://llm.example.com/v1", "api_key": "sk-x"}),
            Agent(id="ag-cfg", user_id=user.id, name="cfg", model_config_json={"model_config_id": "mc-ep"}),
            Agent(id="ag-plain", user_id=user.id, name="plain", model_config_json={"model_name": "m"}),
            Connector(id="cn-1", user_id=user.id, name="c", base_url="https://api.example.com"),
            MCPServer(id="mcp-1", user_id=user.id, name="m", transport="sse", url="https://mcp.example.com"),
            Channel(id="ch-1", name="ch", type="feishu", org_id="org-own", config={"k": "v"},
                    created_by=user.id),
            Agent(id="ag-notify", user_id=user.id, name="notify", model_config_json={
                "model_name": "m", "notifications": {"on_complete": {"enabled": True, "channel_id": "ch-1"}}}),
            # Tools of others, with this account's own stored credentials.
            Connector(id="cn-theirs", user_id=admin.id, name="theirs", base_url="https://api.example.com"),
            MCPServer(id="mcp-org", user_id=admin.id, org_id="org-theirs", name="org",
                      transport="sse", url="https://mcp.example.com"),
        ])
        await s.flush()
        s.add_all([
            ConnectorCredential(connector_id="cn-theirs", user_id=user.id, credentials_blob="t1"),
            MCPServerCredential(server_id="mcp-org", user_id=user.id, env_blob={"TOKEN": "t2"}),
            ConnectorCredential(connector_id="cn-1", user_id=user.id, credentials_blob="own"),
            WorkflowRun(id="run-waiting", workflow_id="wf-1", user_id=user.id, blueprint_snapshot={},
                        status="waiting"),
            WorkflowRun(id="run-done", workflow_id="wf-1", user_id=user.id, blueprint_snapshot={},
                        status="completed"),
            ConfirmationRequest(id="cr-1", user_id=user.id, mode="inline", status="pending"),
        ])
        user.default_agent_id = "ag-plain"
        user.system_instructions = "always reply in French"
        await s.commit()

    async def _assert_switched_off(self, s: AsyncSession, user_id: str) -> None:
        """Switched off by flags, nothing of the account's own lost."""
        from fim_one.db.models import Agent, Workflow, WorkflowRun
        from fim_one.db.models.channel import Channel, ConfirmationRequest
        from fim_one.db.models.connector import Connector
        from fim_one.db.models.connector_credential import ConnectorCredential
        from fim_one.db.models.mcp_server import MCPServer
        from fim_one.db.models.mcp_server_credential import MCPServerCredential
        from fim_one.db.models.model_config import ModelConfig
        from fim_one.db.models.organization import OrgMembership

        user = await s.get(User, user_id)
        assert user is not None
        assert user.default_agent_id is None and user.system_instructions is None
        assert user.security_notice is not None
        assert user.security_notice["saved"] == {
            "system_instructions": "always reply in French", "default_agent": "ag-plain"}
        wf = await s.get(Workflow, "wf-1")
        assert wf is not None
        assert wf.schedule_enabled is False and wf.access_suspended_at is not None
        # Kept, so that turning them back on restores them as they were.
        assert wf.webhook_url == "https://hooks.example.com/x" and wf.api_key_hash == "a" * 64
        assert wf.schedule_cron == "* * * * *"
        active = {
            row_id: is_active
            for model in (Agent, ModelConfig, Connector, MCPServer, Channel)
            for row_id, is_active in (await s.execute(select(model.id, model.is_active))).all()
        }
        assert active == {
            "ag-inline": False, "ag-cfg": False, "ag-plain": True, "ag-notify": True,
            "mc-ep": False, "mc-plain": True,
            "cn-1": False, "cn-theirs": True, "mcp-1": False, "mcp-org": True, "ch-1": False,
        }
        notify = await s.get(Agent, "ag-notify")
        assert notify is not None
        assert notify.model_config_json["notifications"]["on_complete"] == {"enabled": False, "channel_id": "ch-1"}
        # The one deletion: credentials stored on tools of others.
        creds = set((await s.execute(select(ConnectorCredential.connector_id, ConnectorCredential.user_id))).all())
        assert creds == {("cn-1", user_id)}
        assert (await s.scalars(select(MCPServerCredential))).all() == []
        runs = dict((await s.execute(select(WorkflowRun.id, WorkflowRun.status))).all())
        assert runs == {"run-waiting": "cancelled", "run-done": "completed"}
        request = await s.get(ConfirmationRequest, "cr-1")
        assert request is not None and request.status == "expired"
        # Memberships and roles stay.
        roles = dict((await s.execute(
            select(OrgMembership.org_id, OrgMembership.role).where(OrgMembership.user_id == user_id))).all())
        assert roles == {"org-theirs": "admin", "org-own": "owner"}

    async def _assert_claimed(self, env: _Env, unproven: dict[str, Any], *, password_cleared: bool) -> User:
        import jwt

        from fim_one.db.models import ApiKey
        from fim_one.web.auth import ALGORITHM, SECRET_KEY

        async with env.sessions() as s:
            user = await s.get(User, unproven["id"])
            assert user is not None
            assert user.email_verified is True
            assert user.refresh_token != unproven["old_refresh"]  # gone, or replaced by this request's own
            assert user.totp_enabled is False and user.totp_secret is None and user.totp_backup_codes is None
            assert user.oauth_provider is None and user.oauth_id is None
            assert (await s.scalars(select(ApiKey.is_active).where(ApiKey.user_id == user.id))).all() == [False]
            assert (await s.scalars(select(UserOAuthBinding).where(UserOAuthBinding.user_id == user.id))).all() == []
            if password_cleared:
                assert user.password_hash is None
            await self._assert_switched_off(s, user.id)
            # Every access token issued before the proof is cut off.
            issued = jwt.decode(unproven["old_token"], SECRET_KEY, algorithms=[ALGORITHM])["iat"]
            cutoff = user.tokens_invalidated_at
            assert cutoff is not None
            assert issued <= cutoff.replace(tzinfo=UTC).timestamp()
        # The earlier holder's password no longer opens the account.
        assert (await env.password_login(_ADDRESS, _FIRST_PW)).status_code == 401
        return user

    async def test_code_sign_in(self, env: _Env) -> None:
        import asyncio

        import jwt

        from fim_one.web.auth import ALGORITHM, SECRET_KEY

        unproven = await self._unproven_with_every_way_in(env)
        await asyncio.sleep(2.1)  # the earlier token is from an earlier second
        await env.add_code("login")
        r = await env.client.post("/api/auth/login-with-code", json={"email": _ADDRESS, "code": "424242"})
        # Signed in directly: the second factor enrolled earlier is gone.
        assert r.status_code == 200 and "access_token" in r.json()
        assert r.json()["user"]["id"] == unproven["id"]
        user = await self._assert_claimed(env, unproven, password_cleared=True)
        # The session issued by this very request is on the valid side of the cut-off.
        new_iat = jwt.decode(r.json()["access_token"], SECRET_KEY, algorithms=[ALGORITHM])["iat"]
        assert user.tokens_invalidated_at is not None
        assert new_iat > user.tokens_invalidated_at.replace(tzinfo=UTC).timestamp()

    async def test_password_reset_by_code(self, env: _Env) -> None:
        import asyncio

        unproven = await self._unproven_with_every_way_in(env)
        await asyncio.sleep(1.1)
        await env.add_code("reset_password")
        r = await env.client.post("/api/auth/verify-forgot-code", json={"email": _ADDRESS, "code": "424242"})
        token = r.json()["data"]["reset_token"]
        r = await env.client.post("/api/auth/forgot-password", json={
            "email": _ADDRESS, "reset_token": token, "new_password": "owners-new-password"})
        assert r.status_code == 200
        await self._assert_claimed(env, unproven, password_cleared=False)
        assert (await env.password_login(_ADDRESS, "owners-new-password")).status_code == 200

    async def test_the_holder_is_told_and_can_turn_things_back_on(self, env: _Env) -> None:
        unproven = await self._unproven_with_every_way_in(env)
        await env.add_code("login")
        r = await env.client.post("/api/auth/login-with-code", json={"email": _ADDRESS, "code": "424242"})
        headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
        notice = (await env.client.get("/api/auth/me", headers=headers)).json()["data"]["security_notice"]
        kinds = {item["kind"]: item for item in notice["items"]}
        assert set(kinds) == {
            "workflow_schedules", "workflow_access", "agents_custom_endpoint", "agent_notifications",
            "model_configs", "connectors", "mcp_servers", "channels", "credentials_removed",
            "runs_cancelled", "requests_closed", "memberships",
        }
        assert {i["id"] for i in kinds["credentials_removed"]["items"]} == {"cn-theirs", "mcp-org"}
        assert {(i["id"], i["role"]) for i in kinds["memberships"]["items"]} == {
            ("org-theirs", "admin"), ("org-own", "owner")}
        assert notice["sign_in"]["second_factor_removed"] is True
        assert notice["sign_in"]["providers_unlinked"] == ["github"]
        assert [k["name"] for k in notice["sign_in"]["api_keys"]] == ["k"]
        assert notice["restorable"] == ["system_instructions", "default_agent"]
        assert "always reply in French" not in str(notice)

        r = await env.client.post("/api/auth/me/security-notice/restore", headers=headers,
                                  json={"item": "system_instructions"})
        assert r.status_code == 200
        r = await env.client.post("/api/auth/me/security-notice/restore", headers=headers,
                                  json={"item": "system_instructions"})
        assert r.status_code == 404
        assert (await env.client.post("/api/auth/me/security-notice/dismiss", headers=headers)).status_code == 200
        me = (await env.client.get("/api/auth/me", headers=headers)).json()["data"]
        assert me["security_notice"] is None
        assert me["system_instructions"] == "always reply in French"
        # Dismissed, the default agent can still be put back.
        r = await env.client.post("/api/auth/me/security-notice/restore", headers=headers,
                                  json={"item": "default_agent"})
        assert r.status_code == 200
        async with env.sessions() as s:
            user = await s.get(User, unproven["id"])
            assert user is not None and user.default_agent_id == "ag-plain"

    async def test_a_suspended_workflow_neither_calls_its_webhook_nor_takes_its_key(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        from fim_one.core.workflow import run_service as rs
        from fim_one.db.models import Workflow
        from fim_one.web.api import workflows as wf_api
        from fim_one.web.services.workflow_trigger_keys import (
            TriggerKeyRejectedError,
            hash_trigger_key,
            resolve_trigger_key,
        )

        await self._unproven_with_every_way_in(env)
        key = "wf_" + "k" * 40
        async with env.sessions() as s:
            wf = await s.get(Workflow, "wf-1")
            assert wf is not None
            wf.api_key_hash = hash_trigger_key(key)
            await s.commit()
        await env.add_code("login")
        r = await env.client.post("/api/auth/login-with-code", json={"email": _ADDRESS, "code": "424242"})
        assert r.status_code == 200

        sent: list[str] = []
        monkeypatch.setattr("fim_one.core.utils.spawn_background", lambda coro, **_k: (sent.append("x"), coro.close()))
        outcome = SimpleNamespace(status="completed", run_id="r", duration_ms=1, outputs=None, error=None)
        async with env.sessions() as s:
            wf = await s.get(Workflow, "wf-1")
            assert wf is not None
            rs.WorkflowRunService._fire_webhook(None, wf, outcome, rs.RunMode.TRIGGER, None)  # type: ignore[arg-type]
            assert sent == []
            with pytest.raises(TriggerKeyRejectedError) as refused:
                await resolve_trigger_key(key, s)
            assert refused.value.error_code == "workflow_trigger_suspended"

            owner = await s.get(User, wf.user_id)
            assert owner is not None
            data = (await wf_api.resume_workflow_access("wf-1", current_user=owner, db=s)).data
            assert data["access_suspended_at"] is None and data["webhook_url"] == "https://hooks.example.com/x"
            assert (await resolve_trigger_key(key, s)).id == "wf-1"
            monkeypatch.setattr(rs, "runs_as_author", lambda ctx: False)
            rs.WorkflowRunService._fire_webhook(None, wf, outcome, rs.RunMode.TRIGGER, None)  # type: ignore[arg-type]
            assert sent == ["x"]

    async def test_a_wrong_code_changes_nothing(self, env: _Env) -> None:
        unproven = await self._unproven_with_every_way_in(env)
        await env.add_code("login")
        r = await env.client.post("/api/auth/login-with-code", json={"email": _ADDRESS, "code": "000000"})
        assert r.status_code != 200
        async with env.sessions() as s:
            user = await s.get(User, unproven["id"])
            assert user is not None
            assert user.email_verified is False and user.password_hash is not None and user.totp_enabled

    async def test_a_proven_account_keeps_everything(self, env: _Env) -> None:
        from fim_one.db.models import ApiKey

        env.settings["email_verification_enabled"] = "true"
        await env.add_code("register")
        assert (await env.register(verification_code="424242", password="owners-password-1")).status_code == 200
        (owner,) = await env.users()
        async with env.sessions() as s:
            s.add(ApiKey(user_id=owner.id, name="k", key_prefix="fim_abcd", key_hash="h2", is_active=True))
            await s.commit()
            row = await s.get(User, owner.id)
            assert row is not None
            await self._set_up_on_behalf(s, row)
        await env.add_code("login")
        r = await env.client.post("/api/auth/login-with-code", json={"email": _ADDRESS, "code": "424242"})
        assert r.status_code == 200
        async with env.sessions() as s:
            user = await s.get(User, owner.id)
            assert user is not None and user.password_hash is not None and user.tokens_invalidated_at is None
            assert (await s.scalars(select(ApiKey.is_active).where(ApiKey.user_id == owner.id))).all() == [True]
            from fim_one.db.models import Workflow
            from fim_one.db.models.organization import OrgMembership

            assert user.default_agent_id == "ag-plain"
            wf = await s.get(Workflow, "wf-1")
            assert wf is not None and wf.schedule_enabled and wf.webhook_url and wf.api_key_hash
            assert len((await s.scalars(select(OrgMembership).where(OrgMembership.user_id == owner.id))).all()) == 2
        assert (await env.password_login(_ADDRESS, "owners-password-1")).status_code == 200


class TestAssigneeByAddress:
    async def test_an_address_held_twice_names_the_proven_account(self, env: _Env) -> None:
        from types import SimpleNamespace

        from fim_one.core.assembly.confirmation import validate_assignee
        from fim_one.core.assembly.errors import AssemblyRefusal

        assert (await env.register()).status_code == 200
        (unproven,) = await env.users()
        owner_id = await env.oauth_login()
        async with env.sessions() as s:
            me = SimpleNamespace(user_id=owner_id, org_ids=set())
            assert await validate_assignee(s, assignee=_ADDRESS, initiator=me, org_id=None) == owner_id
            assert await validate_assignee(s, assignee=_ADDRESS.upper(), initiator=me, org_id=None) == owner_id
            # An id still names exactly that account.
            other = SimpleNamespace(user_id=unproven.id, org_ids=set())
            assert await validate_assignee(s, assignee=unproven.id, initiator=other, org_id=None) == unproven.id
            with pytest.raises(AssemblyRefusal):
                await validate_assignee(s, assignee="nobody@example.com", initiator=me, org_id=None)

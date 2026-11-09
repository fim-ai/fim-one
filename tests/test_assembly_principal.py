"""Principals and the refusal shape (``core/assembly/{principal,errors}.py``)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.assembly.errors import AssemblyRefusal
from fim_one.core.assembly.principal import (
    AuthMethod,
    Binding,
    CallContext,
    provisional_principal,
    resolve_external_actor,
    resolve_principal,
    user_org_ids,
)
from fim_one.core.workflow.run_service import RunRefusedError
from fim_one.db.base import Base
from fim_one.db.models.oauth_binding import UserOAuthBinding
from fim_one.db.models.organization import OrgMembership
from fim_one.db.models.user import User


@pytest.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add(User(id="u-admin", email="admin@x.io", is_admin=True))
        s.add(User(id="u-plain", email="plain@x.io"))
        s.add(User(id="u-off", email="off@x.io", is_active=False))
        s.add(OrgMembership(org_id="org-1", user_id="u-plain"))
        s.add(OrgMembership(org_id="org-2", user_id="u-plain"))
        s.add(UserOAuthBinding(user_id="u-plain", provider="feishu", oauth_id="ou_plain"))
        s.add(UserOAuthBinding(user_id="u-off", provider="feishu", oauth_id="ou_off"))
        await s.commit()
        yield s
    await engine.dispose()


class TestResolvePrincipal:
    async def test_loads_orgs_and_admin_flag(self, session: AsyncSession) -> None:
        plain = await resolve_principal(session, user_id="u-plain", auth=AuthMethod.SESSION)
        admin = await resolve_principal(
            session, user_id="u-admin", auth=AuthMethod.API_KEY, scopes=frozenset({"chat"})
        )

        assert plain.org_ids == frozenset({"org-1", "org-2"})
        assert (plain.is_admin, plain.auth, plain.scopes) == (False, AuthMethod.SESSION, None)
        assert (admin.is_admin, admin.org_ids) == (True, frozenset())
        assert admin.scopes == frozenset({"chat"})

    @pytest.mark.parametrize("user_id", ["u-off", "u-nobody"])
    async def test_missing_or_disabled_account_is_refused(
        self, session: AsyncSession, user_id: str
    ) -> None:
        with pytest.raises(AssemblyRefusal) as exc:
            await resolve_principal(session, user_id=user_id, auth=AuthMethod.SESSION)

        assert (exc.value.error_code, exc.value.status_code) == ("account_disabled", 403)

    async def test_user_org_ids(self, session: AsyncSession) -> None:
        assert sorted(await user_org_ids(session, "u-plain")) == ["org-1", "org-2"]
        assert await user_org_ids(session, "u-admin") == []


class TestResolveExternalActor:
    async def test_linked_account(self, session: AsyncSession) -> None:
        actor = await resolve_external_actor(session, provider="feishu", subject="ou_plain")

        assert actor is not None
        assert (actor.user_id, actor.auth) == ("u-plain", AuthMethod.CHANNEL)

    async def test_unlinked_actor_is_nobody(self, session: AsyncSession) -> None:
        assert await resolve_external_actor(session, provider="feishu", subject="ou_x") is None
        assert await resolve_external_actor(session, provider="slack", subject="ou_plain") is None

    async def test_linked_but_disabled_account_is_refused(self, session: AsyncSession) -> None:
        with pytest.raises(AssemblyRefusal) as exc:
            await resolve_external_actor(session, provider="feishu", subject="ou_off")

        assert exc.value.error_code == "account_disabled"


class TestCallContext:
    def test_subject_is_the_binding_owner_when_there_is_one(self) -> None:
        caller = provisional_principal("u-caller")
        direct = CallContext(principal=caller, binding=None, entry="chat_noagent")
        bound = CallContext(
            principal=caller,
            binding=Binding(kind="agent", id="a1", owner_id="u-owner"),
            entry="chat",
        )

        assert direct.subject_id == "u-caller"
        assert bound.subject_id == "u-owner"

    def test_provisional_principal_carries_the_id_only(self) -> None:
        principal = provisional_principal("u-1", auth=AuthMethod.SCHEDULE)

        assert (principal.user_id, principal.auth) == ("u-1", AuthMethod.SCHEDULE)
        assert (principal.org_ids, principal.is_admin, principal.scopes) == (
            frozenset(),
            False,
            None,
        )


class TestAssemblyRefusal:
    def test_detail_defaults_from_the_code(self) -> None:
        refusal = AssemblyRefusal("resource_not_found", 404)

        assert refusal.detail == "Resource not found"
        assert str(refusal) == "resource_not_found"
        assert refusal.detail_args is None

    def test_run_refused_error_is_a_refusal_with_the_same_constructor(self) -> None:
        exc = RunRefusedError("workflow_inactive", 403, "Workflow is inactive")

        assert isinstance(exc, AssemblyRefusal)
        assert (exc.error_code, exc.status_code, exc.detail) == (
            "workflow_inactive",
            403,
            "Workflow is inactive",
        )
        assert str(exc) == "Workflow is inactive"
        assert RunRefusedError("blueprint_empty", 400).detail == "Blueprint empty"

    async def test_web_handler_renders_it_like_an_app_error(self, session: AsyncSession) -> None:
        from fim_one.web.app import create_app

        @asynccontextmanager
        async def _noop_lifespan(app: FastAPI) -> AsyncIterator[None]:
            yield

        @asynccontextmanager
        async def _create_session() -> AsyncIterator[AsyncSession]:
            yield session

        with patch("fim_one.web.app.lifespan", _noop_lifespan):
            app = create_app()

        @app.get("/_test/refusal")
        async def _refuse() -> None:
            raise AssemblyRefusal(
                "resource_inactive", 409, "Connector 'crm' is disabled", {"name": "crm"}
            )

        @app.get("/_test/refusal-bare")
        async def _refuse_bare() -> None:
            raise AssemblyRefusal("owner_only", 403)

        with (
            patch("fim_one.db.create_session", _create_session),
            patch("fim_one.db.engine.create_session", _create_session),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                full = await client.get("/_test/refusal")
                bare = await client.get("/_test/refusal-bare")

        assert full.status_code == 409
        assert full.json() == {
            "detail": "Connector 'crm' is disabled",
            "error_code": "resource_inactive",
            "error_args": {"name": "crm"},
        }
        assert bare.status_code == 403
        assert bare.json() == {
            "detail": "Owner only",
            "error_code": "owner_only",
            "error_args": {},
        }

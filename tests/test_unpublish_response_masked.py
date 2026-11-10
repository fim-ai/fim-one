"""An admin who unpublishes someone else's resource is not handed its insides.

``POST /api/{resource}/{id}/unpublish`` admits the owner, an admin of the
organization the resource is shared with, and a platform admin. The response
was built with the owner's view for all three, so an org admin who is not
the owner received an MCP server's URL, command, launch arguments and its
decrypted ``env`` / ``headers``, a connector's base URL, actions and
configuration, an agent's instructions, a workflow's blueprint and a
skill's content. Only the owner gets the owner's view.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.db.base import Base
from fim_one.db.models.agent import Agent
from fim_one.db.models.connector import Connector, ConnectorAction
from fim_one.db.models.mcp_server import MCPServer
from fim_one.db.models.organization import Organization, OrgMembership
from fim_one.db.models.skill import Skill
from fim_one.db.models.user import User
from fim_one.db.models.workflow import Workflow
from fim_one.web.api.agents import unpublish_agent
from fim_one.web.api.connectors import unpublish_connector
from fim_one.web.api.mcp_servers import unpublish_mcp_server
from fim_one.web.api.skills import unpublish_skill
from fim_one.web.api.workflows import unpublish_workflow

OWNER = "u-owner"
ORG_ADMIN = "u-org-admin"
PLATFORM_ADMIN = "u-platform-admin"
ORG = "org-1"
RID = "res-1"

# Every value below must be absent from what a non-owner is answered.
SECRETS = (
    "SECRET-ENV-VALUE",
    "SECRET-HEADER-VALUE",
    "secret-host.example",
    "--secret-arg",
    "/secret/workdir",
    "secret-command",
    "SECRET-AUTH-CONFIG",
    "SECRET-DB-HOST",
    "secret-action-path",
    "SECRET-INSTRUCTIONS",
    "SECRET-MODEL-CONFIG",
    "SECRET-BLUEPRINT",
    "SECRET-WEBHOOK",
    "SECRET-SKILL-CONTENT",
    "SECRET-SKILL-SCRIPT",
)

_SHARED: dict[str, Any] = {"visibility": "org", "org_id": ORG}


def _mcp_server() -> list[Any]:
    return [
        MCPServer(
            id=RID,
            user_id=OWNER,
            name="srv",
            transport="stdio",
            command="/usr/bin/secret-command",
            args=["--secret-arg", "x"],
            env={"API_KEY": "SECRET-ENV-VALUE"},
            url="https://secret-host.example/mcp",
            working_dir="/secret/workdir",
            headers={"Authorization": "SECRET-HEADER-VALUE"},
            **_SHARED,
        )
    ]


def _connector() -> list[Any]:
    return [
        Connector(
            id=RID,
            user_id=OWNER,
            name="conn",
            type="api",
            base_url="https://secret-host.example/api",
            auth_type="bearer",
            auth_config={"token_prefix": "SECRET-AUTH-CONFIG"},
            db_config={"host": "SECRET-DB-HOST"},
            status="published",
            **_SHARED,
        ),
        ConnectorAction(
            id="act-1", connector_id=RID, name="ping", method="GET", path="/secret-action-path"
        ),
    ]


def _agent() -> list[Any]:
    return [
        Agent(
            id=RID,
            user_id=OWNER,
            name="agent",
            instructions="SECRET-INSTRUCTIONS",
            model_config_json={"note": "SECRET-MODEL-CONFIG"},
            **_SHARED,
        )
    ]


def _workflow() -> list[Any]:
    return [
        Workflow(
            id=RID,
            user_id=OWNER,
            name="wf",
            blueprint={"nodes": [{"id": "SECRET-BLUEPRINT"}], "edges": []},
            webhook_url="https://hooks.example/SECRET-WEBHOOK",
            status="active",
            **_SHARED,
        )
    ]


def _skill() -> list[Any]:
    return [
        Skill(
            id=RID,
            user_id=OWNER,
            name="skill",
            content="SECRET-SKILL-CONTENT",
            script="SECRET-SKILL-SCRIPT",
            **_SHARED,
        )
    ]


RESOURCES: dict[str, tuple[Any, Any]] = {
    "mcp_server": (_mcp_server, unpublish_mcp_server),
    "connector": (_connector, unpublish_connector),
    "agent": (_agent, unpublish_agent),
    "workflow": (_workflow, unpublish_workflow),
    "skill": (_skill, unpublish_skill),
}


@pytest.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        for uid in (OWNER, ORG_ADMIN, PLATFORM_ADMIN):
            s.add(User(id=uid, email=f"{uid}@x.io", username=uid))
        s.add(Organization(id=ORG, name=ORG, slug=ORG, owner_id=OWNER))
        s.add(OrgMembership(org_id=ORG, user_id=OWNER, role="owner"))
        s.add(OrgMembership(org_id=ORG, user_id=ORG_ADMIN, role="admin"))
        await s.commit()
        yield s
    await engine.dispose()


def _user(user_id: str) -> Any:
    return SimpleNamespace(id=user_id, username=user_id, is_admin=user_id == PLATFORM_ADMIN)


async def _unpublish(session: AsyncSession, kind: str, user_id: str) -> dict[str, Any]:
    rows, endpoint = RESOURCES[kind]
    for row in rows():
        session.add(row)
    await session.commit()
    session.expunge_all()
    response = await endpoint(RID, current_user=_user(user_id), db=session)
    data: dict[str, Any] = response.data
    return data


@pytest.mark.parametrize("kind", list(RESOURCES))
@pytest.mark.parametrize("user_id", [ORG_ADMIN, PLATFORM_ADMIN])
async def test_an_admin_who_is_not_the_owner_gets_no_secret_value(
    session: AsyncSession, kind: str, user_id: str
) -> None:
    data = await _unpublish(session, kind, user_id)

    assert data["visibility"] == "personal"  # the unpublish itself went through
    text = repr(data)
    leaked = [secret for secret in SECRETS if secret in text]
    assert not leaked, f"{kind} unpublish answered {user_id} with {leaked}"


@pytest.mark.parametrize("kind", list(RESOURCES))
async def test_the_owner_still_gets_the_owners_view(session: AsyncSession, kind: str) -> None:
    data = await _unpublish(session, kind, OWNER)

    assert data["visibility"] == "personal"
    assert any(secret in repr(data) for secret in SECRETS)


async def test_an_mcp_servers_fields_are_the_non_owner_ones(session: AsyncSession) -> None:
    data = await _unpublish(session, "mcp_server", ORG_ADMIN)

    assert (data["url"], data["command"], data["args"], data["working_dir"]) == (None,) * 4
    assert data["env"] == {"API_KEY": "***"}
    assert data["headers"] == {"Authorization": "***"}

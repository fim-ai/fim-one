"""A stdio MCP server is a command run in the application container.

The rules:

- only a system administrator may create, edit, copy or publish a stdio
  server (``web/api/mcp_servers.py``);
- only a stdio server whose owner is an active system administrator *now*
  is ever started, whichever way it is opened, the test-connection endpoint
  included, with the same answer (``core/assembly/stdio.py``);
- somebody who is not an administrator and brings their own credential to
  a shared stdio server may only fill in the variables its owner declared
  (the environment decides what a command loads).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.db.base import Base
from fim_one.db.models.mcp_server import MCPServer
from fim_one.db.models.user import User
from fim_one.web.api import mcp_servers as api
from fim_one.web.api import workflows as workflows_api
from fim_one.web.exceptions import AppError
from fim_one.web.schemas.common import PublishRequest
from fim_one.web.schemas.mcp_server import (
    MCPMyCredentialUpsert,
    MCPServerCreate,
    MCPServerForkRequest,
    MCPServerUpdate,
)
from fim_one.web.services.mcp_tool_listing import listings

from .assembly import matrix as m

pytestmark = pytest.mark.timeout(60)

ADMIN = "u-admin"
USER = "u-user"
ORG = "org-1"
ARGS = ["-c", "print('started')"]


def _as(user_id: str) -> Any:
    return SimpleNamespace(id=user_id, username=user_id, is_admin=user_id == ADMIN)


@pytest.fixture()
def stdio_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stdio is enabled on the instance."""
    monkeypatch.setenv("ALLOW_STDIO_MCP", "true")


@pytest.fixture()
async def session(stdio_on: None) -> AsyncIterator[AsyncSession]:
    from fim_one.db.models.organization import Organization, OrgMembership

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        for uid in (ADMIN, USER):
            s.add(User(id=uid, email=f"{uid}@x.io", username=uid, is_admin=uid == ADMIN))
        s.add(Organization(id=ORG, name=ORG, slug=ORG, owner_id=ADMIN))
        for uid in (ADMIN, USER):
            s.add(OrgMembership(org_id=ORG, user_id=uid, role="member"))
        await s.commit()
        yield s
    await engine.dispose()


async def _row(session: AsyncSession, owner: str, **fields: Any) -> MCPServer:
    """A row as it is in the database, whoever wrote it and however."""
    values: dict[str, Any] = {
        "user_id": owner,
        "name": f"srv-{owner}",
        "transport": "stdio",
        "command": "python3",
        "args": list(ARGS),
        "is_active": True,
    }
    values.update(fields)
    server = MCPServer(**values)
    session.add(server)
    await session.flush()
    if values.get("visibility") == "org":
        # Shared with the organisation and installed by the ordinary user.
        from fim_one.db.models.resource_subscription import ResourceSubscription

        session.add(
            ResourceSubscription(
                user_id=USER, resource_type="mcp_server", resource_id=server.id, org_id=ORG
            )
        )
    await session.commit()
    return server


async def _refused(awaitable: Any) -> AppError:
    with pytest.raises(AppError) as caught:
        await awaitable
    return caught.value


class TestCreate:
    async def test_an_ordinary_user_cannot_create_a_stdio_server(
        self, session: AsyncSession
    ) -> None:
        body = MCPServerCreate(name="x", transport="stdio", command="python3", args=ARGS)

        error = await _refused(api.create_mcp_server(body, current_user=_as(USER), db=session))

        assert (error.status_code, error.error_code) == (403, "stdio_mcp_admin_only")
        assert (await session.execute(select(MCPServer))).scalars().all() == []

    async def test_an_administrator_can(self, session: AsyncSession) -> None:
        body = MCPServerCreate(name="x", transport="stdio", command="npx", args=["-y", "srv"])

        created = (await api.create_mcp_server(body, current_user=_as(ADMIN), db=session)).data

        assert created["transport"] == "stdio" and created["stdio_blocked"] is None

    async def test_nobody_can_while_stdio_is_off_on_the_instance(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALLOW_STDIO_MCP", "false")
        body = MCPServerCreate(name="x", transport="stdio", command="npx")

        error = await _refused(api.create_mcp_server(body, current_user=_as(ADMIN), db=session))

        assert (error.status_code, error.error_code) == (403, "stdio_mcp_disabled")

    async def test_an_ordinary_user_still_creates_http_servers(self, session: AsyncSession) -> None:
        body = MCPServerCreate(
            name="x", transport="streamable_http", url="https://mcp.example.com/mcp"
        )
        with patch.object(api, "validate_url", lambda _url: None):
            created = (await api.create_mcp_server(body, current_user=_as(USER), db=session)).data

        assert created["transport"] == "streamable_http"


class TestUpdate:
    @pytest.mark.parametrize(
        "change",
        [
            {"command": "node"},
            {"args": ["-e", "console.log(1)"]},
            {"env": {"NODE_OPTIONS": "--max-old-space-size=64"}},
            {"working_dir": "/srv"},
            {"name": "renamed"},
            {"is_active": True},
            # A null transport leaves the server stdio.
            {"transport": None, "command": "node"},
        ],
        ids=["command", "args", "env", "working_dir", "name", "is_active", "null-transport"],
    )
    async def test_an_ordinary_user_cannot_edit_their_stdio_server(
        self, session: AsyncSession, change: dict[str, Any]
    ) -> None:
        server = await _row(session, USER)

        error = await _refused(
            api.update_mcp_server(
                server.id, MCPServerUpdate(**change), current_user=_as(USER), db=session
            )
        )

        assert (error.status_code, error.error_code) == (403, "stdio_mcp_admin_only")
        await session.refresh(server)
        assert (server.command, server.args) == ("python3", ARGS)

    async def test_an_ordinary_user_cannot_switch_a_server_to_stdio(
        self, session: AsyncSession
    ) -> None:
        server = await _row(
            session, USER, transport="sse", command=None, args=None, url="https://x.example.com/sse"
        )

        error = await _refused(
            api.update_mcp_server(
                server.id,
                MCPServerUpdate(transport="stdio", command="python3", args=ARGS),
                current_user=_as(USER),
                db=session,
            )
        )

        assert (error.status_code, error.error_code) == (403, "stdio_mcp_admin_only")
        await session.refresh(server)
        assert server.transport == "sse" and server.command is None

    async def test_a_command_parked_on_an_http_server_cannot_be_switched_on_later(
        self, session: AsyncSession
    ) -> None:
        """The rule is applied to the transport the server would end up with."""
        server = await _row(
            session, USER, transport="sse", command=None, args=None, url="https://x.example.com/sse"
        )
        with patch.object(api, "validate_url", lambda _url: None):
            await api.update_mcp_server(
                server.id,
                MCPServerUpdate(command="python3", args=ARGS),
                current_user=_as(USER),
                db=session,
            )

        error = await _refused(
            api.update_mcp_server(
                server.id, MCPServerUpdate(transport="stdio"), current_user=_as(USER), db=session
            )
        )

        assert error.error_code == "stdio_mcp_admin_only"

    async def test_an_ordinary_user_may_turn_their_stdio_server_into_an_http_one(
        self, session: AsyncSession
    ) -> None:
        server = await _row(session, USER)
        with patch.object(api, "validate_url", lambda _url: None):
            updated = (
                await api.update_mcp_server(
                    server.id,
                    MCPServerUpdate(transport="sse", url="https://x.example.com/sse"),
                    current_user=_as(USER),
                    db=session,
                )
            ).data

        assert updated["transport"] == "sse" and updated["stdio_blocked"] is None

    async def test_nobody_edits_somebody_elses_server(self, session: AsyncSession) -> None:
        server = await _row(session, ADMIN, command="npx", args=["-y", "srv"])

        error = await _refused(
            api.update_mcp_server(
                server.id, MCPServerUpdate(args=ARGS), current_user=_as(USER), db=session
            )
        )

        assert error.status_code == 404

    async def test_an_administrator_edits_their_stdio_server(self, session: AsyncSession) -> None:
        server = await _row(session, ADMIN, command="npx", args=["-y", "srv"])

        updated = (
            await api.update_mcp_server(
                server.id,
                MCPServerUpdate(args=["-y", "other"], env={"TOKEN": "t"}),
                current_user=_as(ADMIN),
                db=session,
            )
        ).data

        assert updated["args"] == ["-y", "other"]


class TestForkPublishDelete:
    async def test_an_ordinary_user_cannot_copy_their_stdio_server(
        self, session: AsyncSession
    ) -> None:
        server = await _row(session, USER)

        error = await _refused(
            api.fork_mcp_server(
                server.id, MCPServerForkRequest(name="copy"), current_user=_as(USER), db=session
            )
        )

        assert (error.status_code, error.error_code) == (403, "stdio_mcp_admin_only")
        assert len((await session.execute(select(MCPServer))).scalars().all()) == 1

    async def test_an_ordinary_user_cannot_copy_an_administrators_published_server(
        self, session: AsyncSession
    ) -> None:
        """A copy would be theirs to change: only the owner may copy."""
        server = await _row(
            session, ADMIN, command="npx", args=["-y", "srv"], visibility="org", org_id=ORG
        )

        error = await _refused(
            api.fork_mcp_server(server.id, None, current_user=_as(USER), db=session)
        )

        assert (error.status_code, error.error_code) == (403, "fork_denied")

    async def test_an_administrator_can_copy_theirs(self, session: AsyncSession) -> None:
        server = await _row(session, ADMIN, command="npx", args=["-y", "srv"])

        copy = (
            await api.fork_mcp_server(server.id, None, current_user=_as(ADMIN), db=session)
        ).data

        assert copy["transport"] == "stdio" and copy["id"] != server.id

    @pytest.mark.parametrize("status", [None, "rejected"])
    async def test_an_ordinary_user_cannot_publish_their_stdio_server(
        self, session: AsyncSession, status: str | None
    ) -> None:
        server = await _row(session, USER, publish_status=status)
        call = (
            api.resubmit_mcp_server(server.id, current_user=_as(USER), db=session)
            if status
            else api.publish_mcp_server(
                server.id,
                PublishRequest(scope="org", org_id=ORG),
                current_user=_as(USER),
                db=session,
            )
        )

        error = await _refused(call)

        assert error.error_code == "stdio_mcp_admin_only"
        await session.refresh(server)
        assert server.visibility == "personal"

    async def test_an_ordinary_user_can_delete_theirs(self, session: AsyncSession) -> None:
        server = await _row(session, USER)

        await api.delete_mcp_server(server.id, current_user=_as(USER), db=session)

        assert (await session.execute(select(MCPServer))).scalars().all() == []


class TestTestConnection:
    @pytest.fixture()
    def client(self) -> Any:
        client = AsyncMock()
        client.connect_stdio = AsyncMock(return_value=[])
        with patch("fim_one.core.mcp.MCPClient", return_value=client):
            yield client

    async def test_an_ordinary_users_row_is_not_started(
        self, session: AsyncSession, client: Any
    ) -> None:
        """Answered as the start-time refusal and the server list report it."""
        server = await _row(session, USER)

        error = await _refused(api.test_mcp_server(server.id, current_user=_as(USER), db=session))

        assert (error.status_code, error.error_code) == (403, "stdio_mcp_owner_not_admin")
        detail = (await api.get_mcp_server(server.id, current_user=_as(USER), db=session)).data
        assert detail["stdio_blocked"] == error.error_code
        client.connect_stdio.assert_not_awaited()

    async def test_nothing_is_started_while_stdio_is_off(
        self, session: AsyncSession, client: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = await _row(session, ADMIN, command="npx", args=["-y", "srv"])
        monkeypatch.setenv("ALLOW_STDIO_MCP", "false")

        error = await _refused(api.test_mcp_server(server.id, current_user=_as(ADMIN), db=session))

        assert (error.status_code, error.error_code) == (403, "stdio_mcp_disabled")
        client.connect_stdio.assert_not_awaited()

    async def test_a_row_whose_owner_stopped_being_an_administrator_is_not_started(
        self, session: AsyncSession, client: Any
    ) -> None:
        """The request still carries the old flag; the row's owner is looked up."""
        server = await _row(session, ADMIN, command="npx", args=["-y", "srv"])
        await session.execute(update(User).where(User.id == ADMIN).values(is_admin=False))
        await session.commit()

        error = await _refused(api.test_mcp_server(server.id, current_user=_as(ADMIN), db=session))

        assert (error.status_code, error.error_code) == (403, "stdio_mcp_owner_not_admin")
        client.connect_stdio.assert_not_awaited()

    async def test_an_administrators_server_is_started(
        self, session: AsyncSession, client: Any
    ) -> None:
        server = await _row(session, ADMIN, command="npx", args=["-y", "srv"])

        result = (await api.test_mcp_server(server.id, current_user=_as(ADMIN), db=session)).data

        assert result["ok"] is True
        client.connect_stdio.assert_awaited_once()


class TestWhatIsShown:
    async def test_capabilities_say_who_may_and_why_not(self, stdio_on: None) -> None:
        assert await api.get_capabilities(current_user=_as(ADMIN)) == {
            "allow_stdio": True,
            "stdio_enabled": True,
            "stdio_reason": None,
        }
        assert await api.get_capabilities(current_user=_as(USER)) == {
            "allow_stdio": False,
            "stdio_enabled": True,
            "stdio_reason": "admin_only",
        }

    async def test_capabilities_when_stdio_is_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ALLOW_STDIO_MCP", raising=False)

        assert await api.get_capabilities(current_user=_as(ADMIN)) == {
            "allow_stdio": False,
            "stdio_enabled": False,
            "stdio_reason": "disabled",
        }

    async def test_the_owner_is_told_their_row_is_not_started(self, session: AsyncSession) -> None:
        mine = await _row(session, USER)
        shared = await _row(
            session, ADMIN, command="npx", args=["-y", "srv"], visibility="org", org_id=ORG
        )

        listed = await api.list_mcp_servers(page=1, size=50, current_user=_as(USER), db=session)
        detail = (await api.get_mcp_server(mine.id, current_user=_as(USER), db=session)).data

        by_id = {item["id"]: item for item in listed.items}
        assert by_id[mine.id]["stdio_blocked"] == "stdio_mcp_owner_not_admin"
        assert by_id[shared.id]["stdio_blocked"] is None
        assert detail["stdio_blocked"] == "stdio_mcp_owner_not_admin"

    def test_the_messages_exist(self) -> None:
        import json

        messages = json.loads(
            (Path(__file__).resolve().parents[1] / "frontend/messages/en/errors.json").read_text()
        )
        for code in (
            "stdio_mcp_disabled",
            "stdio_mcp_admin_only",
            "stdio_mcp_owner_not_admin",
            "stdio_mcp_env_not_allowed",
        ):
            assert code in messages


class TestPersonalEnvironment:
    """What a user's own credential may set on an administrator's stdio server."""

    async def _shared(self, session: AsyncSession) -> MCPServer:
        return await _row(
            session,
            ADMIN,
            command="npx",
            args=["-y", "srv"],
            env={"API_KEY": "owner", "PATH": "/usr/bin"},
            visibility="org",
            org_id=ORG,
        )

    async def _save(self, session: AsyncSession, server: MCPServer, user: str, env: Any) -> Any:
        return await api.upsert_my_mcp_credentials(
            server.id, MCPMyCredentialUpsert(env=env), current_user=_as(user), db=session
        )

    async def test_a_declared_variable_is_saved(self, session: AsyncSession) -> None:
        server = await self._shared(session)

        assert (await self._save(session, server, USER, {"API_KEY": "mine"})).data == {
            "saved": True
        }

    @pytest.mark.parametrize(
        "env",
        [
            {"NODE_OPTIONS": "--max-old-space-size=64"},
            {"API_KEY": "mine", "PYTHONPATH": "/srv/lib"},
            {"LD_PRELOAD": "/lib/x.so"},
            # Declared by the owner, and still not the user's to set.
            {"PATH": "/srv/bin"},
        ],
        ids=["node-options", "pythonpath", "ld-preload", "declared-path"],
    )
    async def test_a_variable_that_changes_what_runs_is_refused(
        self, session: AsyncSession, env: dict[str, str]
    ) -> None:
        server = await self._shared(session)

        error = await _refused(self._save(session, server, USER, env))

        assert (error.status_code, error.error_code) == (400, "stdio_mcp_env_not_allowed")

    async def test_an_administrator_sets_what_they_like(self, session: AsyncSession) -> None:
        server = await self._shared(session)

        assert (await self._save(session, server, ADMIN, {"EXTRA": "x"})).data == {"saved": True}

    def test_the_rule_itself(self) -> None:
        from fim_one.core.security.mcp import is_loader_env_name, stdio_env_violations

        assert stdio_env_violations({"API_KEY": ""}, {"API_KEY": "x"}) == []
        assert stdio_env_violations({"API_KEY": ""}, {"OTHER": "x"}) == ["OTHER"]
        assert stdio_env_violations(None, {"A": "x"}) == ["A"]
        assert stdio_env_violations({"A": ""}, None) == []
        for name in (
            "PATH", "node_options", "LD_PRELOAD", "PYTHONSTARTUP", "npm_config_registry",
            # Where it fetches from, whom it trusts, what it starts by name.
            "NODE_TLS_REJECT_UNAUTHORIZED", "SSL_CERT_FILE", "SSL_CERT_DIR",
            "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "HTTP_PROXY", "https_proxy",
            "ALL_PROXY", "NO_PROXY", "TMPDIR", "EDITOR", "PAGER", "BROWSER",
        ):  # fmt: skip
            assert is_loader_env_name(name), name
        for name in ("API_KEY", "GITHUB_TOKEN", "REGION"):
            assert not is_loader_env_name(name), name

    async def test_a_credential_saved_before_the_rule_is_refused_when_used(
        self, session: AsyncSession
    ) -> None:
        from fim_one.core.assembly.credentials import resolve_credential
        from fim_one.core.assembly.errors import AssemblyRefusal
        from fim_one.core.assembly.principal import provisional_principal
        from fim_one.db.models.mcp_server_credential import MCPServerCredential

        server = await self._shared(session)
        session.add(
            MCPServerCredential(
                server_id=server.id,
                user_id=USER,
                env_blob={"NODE_OPTIONS": "--max-old-space-size=64"},
            )
        )
        await session.commit()

        with pytest.raises(AssemblyRefusal) as refused:
            await resolve_credential(session, provisional_principal(USER), server)

        assert refused.value.error_code == "stdio_mcp_env_not_allowed"


# ---------------------------------------------------------------------------
# At run time: a row whose owner is not an administrator is never started
# ---------------------------------------------------------------------------


@pytest.fixture()
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stdio.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
    listings.clear()


async def _scene(factory: async_sessionmaker[AsyncSession], *, owner_is_admin: bool) -> m.Scene:
    """Every resource, owned by ``owner`` and used by them. The matrix seeds an
    administrator; here the owner may be an ordinary user."""
    scene = await m.seed_scene(
        factory, m.SceneKey(role="owner", allow_fallback=True, is_active=True, has_personal=False)
    )
    if not owner_is_admin:
        async with factory() as db:
            await db.execute(
                update(User).where(User.id == scene.users["owner"]).values(is_admin=False)
            )
            await db.commit()
    return scene


def _stdio_started(scene: m.Scene, out: m.Outside) -> list[str]:
    stdio_names = {scene.names[r] for r in m.MCP_SERVERS if r.startswith("mcp_stdio")}
    return sorted(stdio_names & set(out.mcp_connects))


class TestNotStartedAtRunTime:
    async def test_agent_turn(self, factory: async_sessionmaker[AsyncSession]) -> None:
        from fim_one.web.api import chat

        scene = await _scene(factory, owner_is_admin=False)
        with m.outside_world(factory) as out:
            cfg = await chat._resolve_agent_config(scene.carrier_agent_id, None, scene.actor)
            tools = await chat._resolve_tools(cfg, None, user_id=scene.actor)
            await chat._connect_pending_mcp_servers(tools, cfg)

        assert _stdio_started(scene, out) == []
        # The HTTP servers of the same owner are untouched by the rule.
        assert scene.names["mcp_http"] in out.mcp_connects

    async def test_chat_without_an_agent(self, factory: async_sessionmaker[AsyncSession]) -> None:
        from fim_one.web.api import chat

        scene = await _scene(factory, owner_is_admin=False)
        with m.outside_world(factory) as out:
            tools = await chat._resolve_tools(None, None, user_id=scene.actor)
            await chat._connect_pending_mcp_servers(tools, None)

        assert _stdio_started(scene, out) == []

    async def test_workflow_step(self, factory: async_sessionmaker[AsyncSession]) -> None:
        from fim_one.core.workflow.nodes import MCPExecutor
        from fim_one.core.workflow.types import NodeStatus, NodeType
        from fim_one.core.workflow.variable_store import VariableStore

        scene = await _scene(factory, owner_is_admin=False)
        with m.outside_world(factory) as out:
            node = m._node(
                NodeType.MCP,
                {"server_id": scene.ids["mcp_stdio"], "tool_name": "echo", "parameters": {}},
            )
            result = await MCPExecutor().execute(node, VariableStore(), m._context(scene))

        assert result.status == NodeStatus.FAILED
        assert "system administrator" in (result.error or "")
        assert _stdio_started(scene, out) == [] and out.mcp_calls == []

    async def test_tools_listing(self, factory: async_sessionmaker[AsyncSession]) -> None:
        from fim_one.core.assembly.errors import AssemblyRefusal

        scene = await _scene(factory, owner_is_admin=False)
        with m.outside_world(factory) as out, pytest.raises(AssemblyRefusal) as refused:
            async with factory() as db:
                await workflows_api.list_step_mcp_tools(
                    scene.ids["mcp_stdio"], current_user=SimpleNamespace(id=scene.actor), db=db
                )

        assert refused.value.error_code == "stdio_mcp_owner_not_admin"
        assert _stdio_started(scene, out) == []

    async def test_an_inactive_administrator_counts_as_none(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        from fim_one.core.assembly.stdio import stdio_refusals

        scene = await _scene(factory, owner_is_admin=True)
        async with factory() as db:
            await db.execute(
                update(User).where(User.id == scene.users["owner"]).values(is_active=False)
            )
            await db.commit()
            server = await db.get(MCPServer, scene.ids["mcp_stdio"])
            with patch("fim_one.core.security.is_stdio_allowed", lambda: True):
                refusals = await stdio_refusals(db, [server])

        assert refusals[scene.ids["mcp_stdio"]].error_code == "stdio_mcp_owner_not_admin"

    async def test_not_knowing_the_owner_starts_nothing(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        from fim_one.core.assembly.stdio import stdio_refusals

        scene = await _scene(factory, owner_is_admin=True)
        async with factory() as db:
            server = await db.get(MCPServer, scene.ids["mcp_stdio"])
        broken = AsyncMock()
        broken.execute = AsyncMock(side_effect=RuntimeError("database is gone"))
        with patch("fim_one.core.security.is_stdio_allowed", lambda: True):
            refusals = await stdio_refusals(broken, [server])

        assert refusals[scene.ids["mcp_stdio"]].error_code == "stdio_mcp_owner_not_admin"

    async def test_an_administrators_server_is_started_for_everyone_it_is_shared_with(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Using one an administrator published stays possible."""
        from fim_one.web.api import chat

        scene = await m.seed_scene(
            factory,
            m.SceneKey(role="subscriber", allow_fallback=True, is_active=True, has_personal=False),
        )
        with m.outside_world(factory) as out:
            tools = await chat._resolve_tools(None, None, user_id=scene.actor)
            await chat._connect_pending_mcp_servers(tools, None)

        assert scene.names["mcp_stdio"] in out.mcp_connects


class TestNoOtherWayIn:
    """Pins the places a stdio server can come from or be started from.

    A new one must go through the same rule: adding it here is the reminder.
    """

    SRC = Path(__file__).resolve().parents[1] / "src" / "fim_one"

    def _hits(self, pattern: str) -> set[str]:
        found: set[str] = set()
        for path in self.SRC.rglob("*.py"):
            if "migrations" in path.parts:
                continue
            if re.search(pattern, path.read_text(encoding="utf-8")):
                found.add(path.relative_to(self.SRC).as_posix())
        return found

    def test_rows_are_only_made_by_the_mcp_server_api(self) -> None:
        """Create and fork. No import, market install, builder tool or agent copy makes one."""
        assert self._hits(r"(?<!class )\bMCPServer(?:Model)?\(") == {"web/api/mcp_servers.py"}

    def test_stdio_is_only_started_from_two_places(self) -> None:
        assert self._hits(r"\.connect_stdio\(") == {
            # Every agent turn, workflow step and tool listing: checked by stdio_refusals.
            "core/assembly/toolset.py",
            # The test-connection endpoint: checked by stdio_refusals.
            "web/api/mcp_servers.py",
            # A docstring example.
            "core/mcp/client.py",
        }

    def test_the_connection_settings_are_only_written_by_the_mcp_server_api(self) -> None:
        writes = self._hits(r"\.(?:command|args|working_dir|transport)\s*=[^=]")
        assert "web/api/mcp_servers.py" not in writes  # it uses setattr() from the update body
        assert not {w for w in writes if w.startswith("web/api/")}


class TestNoServersFromTheEnvironment:
    """Stdio servers come from the portal only; ``MCP_SERVERS`` is not read."""

    def test_the_loader_is_gone(self) -> None:
        from fim_one.web import deps

        assert not hasattr(deps, "get_mcp_tools")
        source = Path(deps.__file__).read_text(encoding="utf-8")
        assert "MCP_SERVERS" not in source and "connect_stdio" not in source

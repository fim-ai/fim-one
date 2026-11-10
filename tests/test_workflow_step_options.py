"""The MCP step editor's tool listing: ``GET /api/workflows/step-options/mcp-servers/{id}/tools``.

An MCP step could only be configured by typing a tool name, and its
arguments could not be entered at all. The editor now asks the server for
its tools and their argument schemas, opened the way a step by the same
user would open it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.assembly.errors import AssemblyRefusal
from fim_one.db.base import Base
from fim_one.web.api import workflows as api
from fim_one.web.exceptions import AppError
from fim_one.web.services.mcp_tool_listing import (
    MAX_IN_FLIGHT_PER_USER,
    McpToolListings,
    TooManyListings,
    listing_fingerprint,
    listings,
)

from .assembly import matrix as m

pytestmark = pytest.mark.timeout(60)


@pytest.fixture()
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'options.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
def _nothing_remembered() -> Iterator[None]:
    """Each test starts with no remembered listing and no fetch in flight."""
    listings.clear()
    yield
    listings.clear()


async def _scene(
    factory: async_sessionmaker[AsyncSession], role: str = "owner", **switches: bool
) -> m.Scene:
    return await m.seed_scene(
        factory,
        m.SceneKey(
            role=role,
            allow_fallback=switches.get("allow_fallback", True),
            is_active=switches.get("is_active", True),
            has_personal=switches.get("has_personal", False),
        ),
    )


async def _list(
    factory: async_sessionmaker[AsyncSession], scene: m.Scene, resource: str = "mcp_http"
) -> Any:
    async with factory() as db:
        return await api.list_step_mcp_tools(
            scene.ids[resource], current_user=SimpleNamespace(id=scene.actor), db=db
        )


class TestListingAServersTools:
    async def test_the_owner_gets_each_tool_with_its_argument_schema(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await _scene(factory)
        with m.outside_world(factory) as out:
            response = await _list(factory, scene)
        assert response.data == {
            "tools": [
                {
                    # The name the step stores, not the prefixed one chat registers.
                    "name": "echo",
                    "description": "echo",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ]
        }
        # Listing calls no tool.
        assert out.mcp_calls == []

    async def test_it_is_opened_with_the_credential_a_step_by_this_user_would_use(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await _scene(factory, "subscriber", has_personal=True)
        with m.outside_world(factory) as out:
            await _list(factory, scene)
        assert out.mcp_connects[scene.names["mcp_http"]]["headers"] == m.PERSONAL_HEADERS

    @pytest.mark.parametrize(
        ("role", "switches", "code"),
        [
            ("outsider", {}, "resource_not_found"),
            ("owner", {"is_active": False}, "resource_inactive"),
            ("subscriber", {"allow_fallback": False}, "credential_required"),
        ],
    )
    async def test_someone_a_step_would_refuse_is_refused_the_same_way(
        self,
        factory: async_sessionmaker[AsyncSession],
        role: str,
        switches: dict[str, bool],
        code: str,
    ) -> None:
        scene = await _scene(factory, role, **switches)
        with m.outside_world(factory) as out, pytest.raises(AssemblyRefusal) as refused:
            await _list(factory, scene)
        assert refused.value.error_code == code
        assert out.mcp_connects == {}


class TestAServerThatCannotBeListed:
    async def test_a_connection_error_is_reported_without_its_text(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """The address may carry a token the caller is not shown."""
        scene = await _scene(factory, "subscriber")

        async def _fail(*_args: Any, **_kwargs: Any) -> Any:
            raise ConnectionError("https://mcp.example.test/?token=SECRET refused")

        with (
            m.outside_world(factory),
            patch("fim_one.core.assembly.toolset._connect_mcp_server", _fail),
            pytest.raises(AppError) as failed,
        ):
            await _list(factory, scene)
        assert (failed.value.error_code, failed.value.status_code) == ("mcp_tools_unavailable", 502)
        assert "SECRET" not in failed.value.detail
        assert failed.value.detail_args == {
            "name": scene.names["mcp_http"],
            "reason": "ConnectionError",
        }

    async def test_a_server_that_does_not_answer_times_out(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await _scene(factory)

        async def _hang(*_args: Any, **_kwargs: Any) -> Any:
            await asyncio.sleep(3600)

        with (
            m.outside_world(factory),
            patch("fim_one.core.assembly.toolset._connect_mcp_server", _hang),
            patch.object(api, "_MCP_TOOL_LISTING_TIMEOUT_SECONDS", 0.05),
            pytest.raises(AppError) as failed,
        ):
            await _list(factory, scene)
        assert failed.value.error_code == "mcp_tools_unavailable"
        assert "no answer within" in failed.value.detail

    async def test_stdio_switched_off_on_the_instance_says_so(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await _scene(factory)
        with (
            m.outside_world(factory),
            patch("fim_one.core.security.is_stdio_allowed", lambda: False),
            pytest.raises(AppError) as failed,
        ):
            await _list(factory, scene, "mcp_stdio")
        assert failed.value.error_code == "mcp_tools_unavailable"
        assert "STDIO MCP transport is disabled" in failed.value.detail


def _counting_connect(calls: list[str], before: Any = None) -> Any:
    """Opens the (fake) server as usual, counting; *before* runs first."""
    from fim_one.core.assembly import toolset

    real = toolset._connect_mcp_server

    async def _connect(client: Any, server: Any, credential: Any) -> list[Any]:
        calls.append(server.id)
        if before is not None:
            await before()
        return await real(client, server, credential)

    return _connect


class TestTheDatabaseConnectionIsNotHeldWhileTheServerIsAsked:
    async def test_the_requests_connection_is_back_in_the_pool_before_connecting(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await _scene(factory)
        engine = factory.kw["bind"]
        seen: list[int] = []
        calls: list[str] = []

        async def _look_at_the_pool() -> None:
            seen.append(engine.sync_engine.pool.checkedout())

        connect = _counting_connect(calls, _look_at_the_pool)
        with (
            m.outside_world(factory),
            patch("fim_one.core.assembly.toolset._connect_mcp_server", connect),
        ):
            async with factory() as db:
                await api.list_step_mcp_tools(
                    scene.ids["mcp_http"], current_user=SimpleNamespace(id=scene.actor), db=db
                )
                # Still inside the request: its session holds no connection.
                assert engine.sync_engine.pool.checkedout() == 0
        assert calls == [scene.ids["mcp_http"]]
        assert seen == [0]


class TestAListingIsRememberedBriefly:
    async def test_asking_again_does_not_open_the_server_again(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await _scene(factory)
        calls: list[str] = []
        with (
            m.outside_world(factory),
            patch("fim_one.core.assembly.toolset._connect_mcp_server", _counting_connect(calls)),
        ):
            first = await _list(factory, scene)
            second = await _list(factory, scene)
        assert len(calls) == 1
        assert second.data == first.data

    async def test_it_is_not_served_to_another_user(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await _scene(factory, "subscriber")
        calls: list[str] = []
        with (
            m.outside_world(factory),
            patch("fim_one.core.assembly.toolset._connect_mcp_server", _counting_connect(calls)),
        ):
            await _list(factory, scene)
            async with factory() as db:
                await api.list_step_mcp_tools(
                    scene.ids["mcp_http"],
                    current_user=SimpleNamespace(id=scene.users["owner"]),
                    db=db,
                )
        assert len(calls) == 2

    async def test_someone_who_lost_access_is_refused_not_served_from_memory(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        from fim_one.db.models.mcp_server import MCPServer

        scene = await _scene(factory)
        with m.outside_world(factory):
            await _list(factory, scene)
            async with factory() as s:
                server = await s.get(MCPServer, scene.ids["mcp_http"])
                assert server is not None
                server.is_active = False
                await s.commit()
            with pytest.raises(AssemblyRefusal) as refused:
                await _list(factory, scene)
        assert refused.value.error_code == "resource_inactive"

    async def test_a_changed_server_is_asked_again(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        from fim_one.db.models.mcp_server import MCPServer

        scene = await _scene(factory)
        calls: list[str] = []
        with (
            m.outside_world(factory),
            patch("fim_one.core.assembly.toolset._connect_mcp_server", _counting_connect(calls)),
        ):
            await _list(factory, scene)
            async with factory() as s:
                server = await s.get(MCPServer, scene.ids["mcp_http"])
                assert server is not None
                server.url = "https://mcp.example.test/v2"
                await s.commit()
            await _list(factory, scene)
        assert len(calls) == 2

    async def test_a_failed_listing_is_not_remembered(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await _scene(factory)
        calls: list[str] = []

        async def _fail(*_args: Any, **_kwargs: Any) -> Any:
            raise ConnectionError("refused")

        with m.outside_world(factory):
            with (
                patch("fim_one.core.assembly.toolset._connect_mcp_server", _fail),
                pytest.raises(AppError),
            ):
                await _list(factory, scene)
            with patch(
                "fim_one.core.assembly.toolset._connect_mcp_server", _counting_connect(calls)
            ):
                await _list(factory, scene)
        assert len(calls) == 1

    def test_a_changed_credential_has_another_fingerprint(self) -> None:
        server = SimpleNamespace(
            updated_at=None, transport="sse", url="https://x", command=None, args=None
        )
        mine = SimpleNamespace(source="personal", env=None, headers={"Authorization": "a"})
        rotated = SimpleNamespace(source="personal", env=None, headers={"Authorization": "b"})
        owners = SimpleNamespace(source="owner", env=None, headers={"Authorization": "a"})
        prints = {listing_fingerprint(server, c) for c in (mine, rotated, owners)}
        assert len(prints) == 3
        assert all("Authorization" not in p for p in prints)

    def test_it_is_forgotten_after_its_time_and_for_another_fingerprint(self) -> None:
        now = [100.0]
        memory = McpToolListings(ttl=30, clock=lambda: now[0])
        memory.put("u1", "s1", "f1", [{"name": "echo"}])
        assert memory.get("u1", "s1", "f1") == [{"name": "echo"}]
        assert memory.get("u2", "s1", "f1") is None
        now[0] = 129.9
        assert memory.get("u1", "s1", "f1") is not None
        now[0] = 130.0
        assert memory.get("u1", "s1", "f1") is None
        memory.put("u1", "s1", "f1", [])
        assert memory.get("u1", "s1", "f2") is None
        # ...and the stale one is gone, not waiting for f1 to come back.
        assert memory.get("u1", "s1", "f1") is None


class TestAUserCanOnlyHaveAFewListingsInFlight:
    async def test_one_more_than_the_limit_is_answered_429(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await _scene(factory)
        release = asyncio.Event()
        calls: list[str] = []
        with (
            m.outside_world(factory),
            patch(
                "fim_one.core.assembly.toolset._connect_mcp_server",
                _counting_connect(calls, release.wait),
            ),
        ):
            waiting = [
                asyncio.create_task(_list(factory, scene)) for _ in range(MAX_IN_FLIGHT_PER_USER)
            ]
            while len(calls) < MAX_IN_FLIGHT_PER_USER:
                await asyncio.sleep(0.01)
            with pytest.raises(AppError) as refused:
                await _list(factory, scene)
            assert (refused.value.error_code, refused.value.status_code) == ("mcp_tools_busy", 429)
            # Another user is not held up by this one.
            other = asyncio.create_task(
                _list_as(factory, scene.ids["mcp_http"], scene.users["subscriber"])
            )
            while len(calls) < MAX_IN_FLIGHT_PER_USER + 1:
                await asyncio.sleep(0.01)
            release.set()
            await asyncio.gather(*waiting, other)
            # The slots are free again; this one is served from memory.
            await _list(factory, scene)
        assert len(calls) == MAX_IN_FLIGHT_PER_USER + 1

    def test_a_slot_is_given_back_when_the_fetch_fails(self) -> None:
        memory = McpToolListings(max_in_flight=1)
        with pytest.raises(RuntimeError), memory.fetching("u1"):
            raise RuntimeError("server down")
        with memory.fetching("u1"), pytest.raises(TooManyListings), memory.fetching("u1"):
            pass

    def test_the_code_has_a_message_for_the_interface(self) -> None:
        import json

        messages = json.loads(
            (Path(__file__).resolve().parents[1] / "frontend/messages/en/errors.json").read_text()
        )
        assert "mcp_tools_busy" in messages


async def _list_as(
    factory: async_sessionmaker[AsyncSession], server_id: str, user_id: str
) -> Any:
    async with factory() as db:
        return await api.list_step_mcp_tools(
            server_id, current_user=SimpleNamespace(id=user_id), db=db
        )

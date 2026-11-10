"""The MCP step editor's tool listing: ``GET /api/workflows/step-options/mcp-servers/{id}/tools``.

An MCP step could only be configured by typing a tool name, and its
arguments could not be entered at all. The editor now asks the server for
its tools and their argument schemas, opened the way a step by the same
user would open it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
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

from .assembly import matrix as m

pytestmark = pytest.mark.timeout(60)


@pytest.fixture()
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'options.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


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

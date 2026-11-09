"""Chat with no agent uses connectors under the same rule as chat with one.

Before the fix the auto-discovered connectors resolved no credential at all:
every call went out unauthenticated (for the owner too), a subscriber the
owner had not opened fallback to was offered the connector anyway, and no
call record was written for API or database calls.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.db.base import Base
from fim_one.db.models.connector_call_log import ConnectorCallLog
from tests.assembly import matrix as m

pytestmark = pytest.mark.timeout(60)


@pytest.fixture()
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'noagent.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _tools(scene: m.Scene, conversation_id: str | None = None) -> Any:
    from fim_one.web.api import chat

    return await chat._resolve_tools(None, conversation_id, user_id=scene.actor)


async def _ping(tools: Any, scene: m.Scene, resource: str = "api_bearer") -> str:
    result: str = await tools.get("connector").run(
        subcommand="execute", connector=scene.names[resource], action="ping", parameters={}
    )
    return result


async def test_the_owners_call_carries_the_stored_credential(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    scene = await m.seed_scene(factory, m.SceneKey("owner", False, True, False))
    with m.outside_world(factory) as out:
        await _ping(await _tools(scene), scene)

    assert out.http[-1]["authorization"] == f"Bearer {m.OWNER_TOKEN}"


async def test_a_users_own_credential_is_used(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    scene = await m.seed_scene(factory, m.SceneKey("subscriber", True, True, True))
    with m.outside_world(factory) as out:
        await _ping(await _tools(scene), scene)

    assert out.http[-1]["authorization"] == f"Bearer {m.PERSONAL_TOKEN}"


async def test_a_subscriber_borrows_the_owners_only_when_fallback_is_on(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    opened = await m.seed_scene(factory, m.SceneKey("subscriber", True, True, False))
    closed = await m.seed_scene(factory, m.SceneKey("subscriber", False, True, False))
    with m.outside_world(factory) as out:
        await _ping(await _tools(opened), opened)
        closed_tools = await _tools(closed)

    assert out.http[-1]["authorization"] == f"Bearer {m.OWNER_TOKEN}"
    # Not offered at all, rather than offered and called with no credential.
    connector_tool = closed_tools.get("connector")
    offered = connector_tool.connector_names if connector_tool is not None else []
    assert closed.names["api_bearer"] not in offered


async def test_every_call_leaves_a_record(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    scene = await m.seed_scene(factory, m.SceneKey("owner", True, True, False))
    with m.outside_world(factory):
        tools = await _tools(scene)
        await _ping(tools, scene)
        database_tool = tools.get("database")

    async with factory() as s:
        rows = list((await s.execute(select(ConnectorCallLog))).scalars().all())
    assert [(r.connector_id, r.user_id, r.success) for r in rows] == [
        (scene.ids["api_bearer"], scene.actor, True)
    ]
    # The database tool records through the same writer (its fence audit
    # rides on that record); there is no database to query here.
    assert database_tool is not None and database_tool._on_call_complete is not None

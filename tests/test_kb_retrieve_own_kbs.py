"""``kb_retrieve`` with no bound knowledge bases reads only what the caller may.

Without an agent (or with an agent that binds no knowledge base) the model
names a knowledge base per call. The retrieval layer checked ownership but
not the on/off switch, so a knowledge base its owner had switched off was
still read from chat.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.assembly.principal import Binding, CallContext, provisional_principal
from fim_one.core.assembly.toolset import kb_binding
from fim_one.core.tool.builtin.kb_retrieve import KBRetrieveTool
from fim_one.db.base import Base
from fim_one.db.models.knowledge_base import KnowledgeBase
from tests.assembly import matrix as m

pytestmark = pytest.mark.timeout(60)


@pytest.fixture()
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'kb.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class _Manager:
    def __init__(self) -> None:
        self.asked: list[tuple[str, str]] = []

    async def retrieve(self, _query: str, *, kb_id: str, user_id: str, **_: Any) -> list[Any]:
        self.asked.append((kb_id, user_id))
        return []


async def _run(tool: KBRetrieveTool, kb_id: str) -> tuple[str, _Manager]:
    manager = _Manager()
    with patch("fim_one.web.deps.get_kb_manager", lambda: manager):
        return await tool.run(query="q", kb_id=kb_id), manager


class TestTheTool:
    async def test_a_kb_the_resolver_does_not_return_is_refused_unread(self) -> None:
        async def _none(_ids: Sequence[str]) -> dict[str, str]:
            return {}

        text, manager = await _run(KBRetrieveTool(user_id="u-1", kb_resolver=_none), "kb-1")

        assert text.startswith("[Error] Knowledge base 'kb-1' is not available")
        assert manager.asked == []

    async def test_a_kb_the_resolver_returns_is_read(self) -> None:
        async def _mine(ids: Sequence[str]) -> dict[str, str]:
            return {kb_id: "u-1" for kb_id in ids}

        text, manager = await _run(KBRetrieveTool(user_id="u-1", kb_resolver=_mine), "kb-1")

        assert text == "No relevant documents found."
        assert manager.asked == [("kb-1", "u-1")]

    async def test_bound_kbs_are_not_asked_about_again(self) -> None:
        async def _never(_ids: Sequence[str]) -> dict[str, str]:
            raise AssertionError("bound knowledge bases were decided at assembly")

        tool = KBRetrieveTool(
            user_id="u-1", kb_ids=["kb-1"], kb_owner_map={"kb-1": "u-owner"}, kb_resolver=_never
        )
        _, manager = await _run(tool, "ignored")

        assert manager.asked == [("kb-1", "u-owner")]


class TestKbBinding:
    async def test_own_and_switched_on_only(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        async with factory() as s:
            s.add(KnowledgeBase(id="on", user_id="u-1", name="on", is_active=True))
            s.add(KnowledgeBase(id="off", user_id="u-1", name="off", is_active=False))
            s.add(KnowledgeBase(id="theirs", user_id="u-2", name="theirs", is_active=True))
            await s.commit()
            direct = CallContext(
                principal=provisional_principal("u-1"), binding=None, entry="chat_noagent"
            )
            through_agent = CallContext(
                principal=provisional_principal("u-1"),
                binding=Binding(kind="agent", id="a-1", owner_id="u-2"),
                entry="chat",
            )

            assert await kb_binding(s, direct, ["on", "off", "theirs", "nope"]) == {"on": "u-1"}
            # Through a binding the binding's owner is the subject.
            assert await kb_binding(s, through_agent, ["on", "theirs"]) == {"theirs": "u-2"}


class TestChatWithoutAnAgent:
    @pytest.mark.parametrize(("is_active", "read"), [(True, True), (False, False)])
    async def test_the_owners_kb_is_read_only_while_switched_on(
        self, factory: async_sessionmaker[AsyncSession], is_active: bool, read: bool
    ) -> None:
        from fim_one.web.api import chat

        scene = await m.seed_scene(factory, m.SceneKey("owner", True, is_active, False))
        with m.outside_world(factory):
            tools = await chat._resolve_tools(None, None, user_id=scene.actor)
            tool = tools.get("kb_retrieve")
            assert isinstance(tool, KBRetrieveTool)
            _, manager = await _run(tool, scene.ids["kb"])

        assert bool(manager.asked) is read

"""Bound-KB delegation is gated by AGENT-OWNER visibility (audit B4).

A KB bound to an agent delegates its content to whoever runs the agent —
that's documented behavior (the agent needs its KB to function). But the
delegation must only hold while the agent owner can still see the KB.
Previously ``chat._resolve_tools`` built its ``kb_owner_map`` by bare
``id IN (...)``, so stale bindings kept delegating foreign KBs.

``_resolve_bound_kb_owner_map`` filters by the agent owner's visibility.
Knowledge bases are not shareable (no KB subscriptions can exist), so in
practice that reduces to owner-only: any bound KB the agent owner does not
own is dropped and never reaches the retrieval tools with an owner mapping.
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fim_one.db.base import Base
from fim_one.db.models.knowledge_base import KnowledgeBase
from fim_one.web.api.chat import _resolve_bound_kb_owner_map

KB_OWNER = "kb-owner"
AGENT_OWNER = "agent-owner"
OWN_KB = "kb-own"
FOREIGN_KB = "kb-foreign"


@pytest.fixture()
async def patched_session(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add(KnowledgeBase(id=OWN_KB, user_id=AGENT_OWNER, name="own"))
        s.add(KnowledgeBase(id=FOREIGN_KB, user_id=KB_OWNER, name="foreign"))
        await s.commit()
    monkeypatch.setattr("fim_one.db.create_session", factory)
    yield
    await engine.dispose()


@pytest.mark.usefixtures("patched_session")
class TestBoundKbOwnerMap:
    @pytest.mark.asyncio
    async def test_own_kb_resolves(self) -> None:
        owner_map = await _resolve_bound_kb_owner_map([OWN_KB], AGENT_OWNER)
        assert owner_map == {OWN_KB: AGENT_OWNER}

    @pytest.mark.asyncio
    async def test_foreign_kb_is_dropped(self) -> None:
        """KBs are not shareable: a binding referencing someone else's KB
        (e.g. left over from the pre-recall sharing era) must not delegate."""
        owner_map = await _resolve_bound_kb_owner_map(
            [OWN_KB, FOREIGN_KB], AGENT_OWNER
        )
        assert FOREIGN_KB not in owner_map
        assert owner_map == {OWN_KB: AGENT_OWNER}

    @pytest.mark.asyncio
    async def test_no_owner_id_resolves_unfiltered(self) -> None:
        """System callers (no agent owner) keep the unfiltered lookup —
        matches _resolve_owner_for_caller's trusted-caller semantics."""
        owner_map = await _resolve_bound_kb_owner_map([FOREIGN_KB], None)
        assert owner_map == {FOREIGN_KB: KB_OWNER}

    @pytest.mark.asyncio
    async def test_missing_kb_absent(self) -> None:
        owner_map = await _resolve_bound_kb_owner_map(["nope"], AGENT_OWNER)
        assert owner_map == {}

"""Bound-KB delegation is gated by AGENT-OWNER visibility (audit B4).

A KB bound to an agent delegates its content to whoever runs the agent —
that's documented behavior (the agent needs its KB to function). But the
delegation must only hold while the agent owner can still see the KB:
bindings are validated once at bind time, and subscriptions can be
reclaimed later (org exit). Previously ``chat._resolve_tools`` built its
``kb_owner_map`` by bare ``id IN (...)``, so a user who left an org kept
reading an org KB through their own agent's stale binding.

``_resolve_bound_kb_owner_map`` now filters by the agent owner's
visibility (own + subscribed); dropped KBs never reach the retrieval
tools with an owner mapping.
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fim_one.db.base import Base
from fim_one.db.models.knowledge_base import KnowledgeBase
from fim_one.db.models.resource_subscription import ResourceSubscription
from fim_one.web.api.chat import _resolve_bound_kb_owner_map

KB_OWNER = "kb-owner"
AGENT_OWNER = "agent-owner"
OWN_KB = "kb-own"
SUBSCRIBED_KB = "kb-subscribed"
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
        s.add(KnowledgeBase(id=SUBSCRIBED_KB, user_id=KB_OWNER, name="sub"))
        s.add(KnowledgeBase(id=FOREIGN_KB, user_id=KB_OWNER, name="foreign"))
        s.add(
            ResourceSubscription(
                user_id=AGENT_OWNER,
                resource_type="knowledge_base",
                resource_id=SUBSCRIBED_KB,
                org_id="org-1",
            )
        )
        await s.commit()
    monkeypatch.setattr("fim_one.db.create_session", factory)
    yield
    await engine.dispose()


@pytest.mark.usefixtures("patched_session")
class TestBoundKbOwnerMap:
    @pytest.mark.asyncio
    async def test_own_and_subscribed_resolve(self) -> None:
        owner_map = await _resolve_bound_kb_owner_map(
            [OWN_KB, SUBSCRIBED_KB], AGENT_OWNER
        )
        assert owner_map == {OWN_KB: AGENT_OWNER, SUBSCRIBED_KB: KB_OWNER}

    @pytest.mark.asyncio
    async def test_unsubscribed_kb_is_dropped(self) -> None:
        """The B4 scenario: subscription reclaimed (org exit) but the
        binding still references the KB — the map must not delegate it."""
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

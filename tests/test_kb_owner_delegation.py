"""KB retrieval resolves the owner from kb_id, gated by caller access (PR-1.4).

The vector store for a KB lives under the *owner's* directory. Callers that
only know the calling user (the workflow KB node, agentless KB chat) used to
pass that caller id straight through as the path owner, so a KB the caller
didn't personally own produced the wrong path and silently returned nothing.

Owner resolution now lives in ``KnowledgeBaseManager`` and is access-checked:
the owner is returned for the owner or a trusted system caller (the agent
delegation path); otherwise ``None`` (→ empty result, not a wrong-path
lookup). Knowledge bases are not shareable, so a ResourceSubscription row —
which can no longer be created for KBs — grants nothing.
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fim_one.db.base import Base
from fim_one.db.models.knowledge_base import KnowledgeBase
from fim_one.db.models.resource_subscription import ResourceSubscription
from fim_one.rag.manager import KnowledgeBaseManager

OWNER = "owner-user"
SUBSCRIBER = "subscriber-user"
STRANGER = "stranger-user"
KID = "kb-1"


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
        s.add(KnowledgeBase(id=KID, user_id=OWNER, name="kb"))
        s.add(
            ResourceSubscription(
                user_id=SUBSCRIBER,
                resource_type="knowledge_base",
                resource_id=KID,
                org_id="org-1",
            )
        )
        await s.commit()
    # _resolve_owner_for_caller does `async with create_session() as db`; the
    # sessionmaker call returns a fresh session usable as an async CM.
    monkeypatch.setattr("fim_one.db.create_session", factory)
    yield
    await engine.dispose()


@pytest.mark.usefixtures("patched_session")
class TestResolveOwner:
    @pytest.mark.asyncio
    async def test_owner_resolves_to_self(self) -> None:
        owner = await KnowledgeBaseManager._resolve_owner_for_caller(KID, OWNER)
        assert owner == OWNER

    @pytest.mark.asyncio
    async def test_subscription_row_grants_nothing(self) -> None:
        # KBs are not shareable: even a (stale) ResourceSubscription row
        # no longer grants access — the subscriber is denied like a stranger.
        owner = await KnowledgeBaseManager._resolve_owner_for_caller(KID, SUBSCRIBER)
        assert owner is None

    @pytest.mark.asyncio
    async def test_stranger_is_denied(self) -> None:
        owner = await KnowledgeBaseManager._resolve_owner_for_caller(KID, STRANGER)
        assert owner is None

    @pytest.mark.asyncio
    async def test_system_caller_resolves_to_owner(self) -> None:
        owner = await KnowledgeBaseManager._resolve_owner_for_caller(KID, None)
        assert owner == OWNER

    @pytest.mark.asyncio
    async def test_missing_kb_returns_none(self) -> None:
        owner = await KnowledgeBaseManager._resolve_owner_for_caller("nope", OWNER)
        assert owner is None

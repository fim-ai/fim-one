"""conversations.agent_id is cleared (not blocked) when its agent is deleted.

Regression guard for the FK delete bomb: once SQLite enforces foreign keys
(``PRAGMA foreign_keys=ON``), deleting an :class:`Agent` that a
:class:`Conversation` still references must NULL the reference via
``ON DELETE SET NULL`` rather than raising a FK violation.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.db.base import Base
from fim_one.db.models.agent import Agent
from fim_one.db.models.conversation import Conversation
from fim_one.db.models.user import User


@pytest.fixture()
async def async_session() -> AsyncIterator[AsyncSession]:
    import fim_one.db.models  # noqa: F401 — register all models

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn: Any, _record: Any) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_deleting_agent_nulls_conversation_reference(
    async_session: AsyncSession,
) -> None:
    user = User(id=uuid.uuid4().hex, username="u", email="u@example.com")
    agent = Agent(id=uuid.uuid4().hex, user_id=user.id, name="A")
    conv = Conversation(
        id=uuid.uuid4().hex, user_id=user.id, mode="agent", agent_id=agent.id
    )
    async_session.add_all([user, agent, conv])
    await async_session.commit()

    # Deleting the referenced agent must succeed (no FK violation)...
    await async_session.delete(agent)
    await async_session.commit()

    # ...and the conversation survives with its agent reference cleared.
    # Select the column directly so the value reflects DB state (the SET NULL)
    # rather than the stale identity-map object.
    row = (
        await async_session.execute(
            select(Conversation.id, Conversation.agent_id).where(
                Conversation.id == conv.id
            )
        )
    ).one()
    assert row.id == conv.id
    assert row.agent_id is None

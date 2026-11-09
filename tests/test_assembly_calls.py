"""The call record writer (``core/assembly/calls.py``)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.assembly.calls import call_recorder
from fim_one.core.assembly.principal import Binding, CallContext, provisional_principal
from fim_one.db.base import Base
from fim_one.db.models.connector_call_log import ConnectorCallLog


@pytest.fixture()
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'calls.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _create_session() -> AsyncIterator[AsyncSession]:
        async with maker() as s:
            yield s

    with patch("fim_one.db.create_session", _create_session):
        yield maker
    await engine.dispose()


async def _rows(factory: async_sessionmaker[AsyncSession]) -> list[ConnectorCallLog]:
    async with factory() as s:
        return list((await s.execute(select(ConnectorCallLog))).scalars().all())


def _ctx() -> CallContext:
    return CallContext(
        principal=provisional_principal("u-1"),
        binding=Binding(kind="agent", id="agent-1", owner_id="u-owner"),
        entry="chat",
        conversation_id="conv-1",
        agent_id="agent-1",
    )


async def test_a_connector_call_is_attributed_to_who_made_it_and_where(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    record = call_recorder(_ctx(), kind="connector")

    await record(
        connector_id="conn-1",
        connector_name="crm",
        action_id="act-1",
        action_name="list contacts",
        request_method="GET",
        request_url="https://crm.example.test/contacts",
        response_status=200,
        response_time_ms=12,
        success=True,
        error_message=None,
    )

    (row,) = await _rows(factory)
    assert (row.connector_id, row.connector_name, row.action_id) == ("conn-1", "crm", "act-1")
    assert (row.user_id, row.conversation_id, row.agent_id) == ("u-1", "conv-1", "agent-1")
    assert (row.request_method, row.response_status, row.success) == ("GET", 200, True)
    assert row.scope_rules_applied is None


async def test_a_database_call_keeps_the_fences_it_ran_under(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    record = call_recorder(_ctx(), kind="database")

    await record(
        connector_id="db-1",
        connector_name="warehouse",
        action_name="query",
        request_method="SQL",
        request_url="SELECT 1",
        success=True,
        scope_rules_applied='{"read_only": true}',
    )

    (row,) = await _rows(factory)
    assert row.scope_rules_applied == '{"read_only": true}'


async def test_a_record_that_cannot_be_written_does_not_fail_the_call() -> None:
    def _broken() -> AsyncSession:
        raise RuntimeError("db down")

    with patch("fim_one.db.create_session", _broken):
        await call_recorder(_ctx(), kind="connector")(connector_id="c", success=True)

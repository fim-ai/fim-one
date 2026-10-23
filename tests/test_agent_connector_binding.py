"""A connector its owner switched off must leave every agent bound to it.

``POST /api/connectors/{id}/toggle`` flips ``is_active``. Auto-discovery
(no-agent chat) honoured the flag, but the agent-bound path in
``chat._resolve_tools`` loaded connectors by id alone, so a disabled
connector stayed fully callable through any agent that referenced it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.db.base import Base
from fim_one.db.models.connector import Connector, ConnectorAction
from fim_one.web.api.chat import _resolve_tools

OWNER = "owner-1"


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
        for cid, active in (("live", True), ("switched-off", False)):
            s.add(
                Connector(
                    id=cid,
                    user_id=OWNER,
                    name=cid,
                    type="api",
                    base_url="https://api.example.com",
                    auth_type="none",
                    is_active=active,
                )
            )
            s.add(ConnectorAction(connector_id=cid, name="ping", method="GET", path="/ping"))
        await s.commit()
    monkeypatch.setattr("fim_one.db.create_session", factory)
    # _resolve_tools builds (and caches) the model registry from this test's
    # env; restore the module cache afterwards so later tests rebuild theirs.
    monkeypatch.setattr("fim_one.web.deps._cached_registry", None)
    monkeypatch.setattr("fim_one.web.deps._cached_registry_version", -1)
    yield
    await engine.dispose()


@pytest.mark.usefixtures("patched_session")
class TestAgentBoundConnectors:
    @pytest.mark.asyncio
    async def test_disabled_connector_not_offered(self) -> None:
        registry = await _resolve_tools(
            {"connector_ids": ["live", "switched-off"], "owner_user_id": OWNER},
            user_id=OWNER,
        )

        meta = registry.get("connector")
        assert meta is not None
        assert list(meta._stubs) == ["live"]

    @pytest.mark.asyncio
    async def test_only_disabled_connectors_registers_no_tool(self) -> None:
        registry = await _resolve_tools(
            {"connector_ids": ["switched-off"], "owner_user_id": OWNER},
            user_id=OWNER,
        )

        assert registry.get("connector") is None

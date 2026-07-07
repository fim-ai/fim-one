"""Module soft-shelve feature flags (Reduce Feature wave 2).

Skills and Workflows are off by default; an admin turns them on. The flag
lives in system_settings and reads False for anything but the literal
"true", so a fresh install starts with both modules shelved.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.db.base import Base
from fim_one.db.models.system_setting import SystemSetting
from fim_one.web.services.feature_flags import (
    SETTING_FEATURE_SKILLS,
    SETTING_FEATURE_WORKFLOWS,
    are_features_enabled,
    is_feature_enabled,
    require_skills_enabled,
    require_workflows_enabled,
)


@pytest.fixture()
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(eng, expire_on_commit=False)
    await eng.dispose()


async def _set(
    factory: async_sessionmaker[AsyncSession], key: str, value: str
) -> None:
    async with factory() as db:
        db.add(SystemSetting(key=key, value=value))
        await db.commit()


class TestIsFeatureEnabled:
    @pytest.mark.asyncio
    async def test_absent_defaults_false(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        async with session_factory() as db:
            assert await is_feature_enabled(db, SETTING_FEATURE_SKILLS) is False
            assert await is_feature_enabled(db, SETTING_FEATURE_WORKFLOWS) is False

    @pytest.mark.asyncio
    async def test_true_string_enables(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _set(session_factory, SETTING_FEATURE_SKILLS, "true")
        async with session_factory() as db:
            assert await is_feature_enabled(db, SETTING_FEATURE_SKILLS) is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", ["false", "1", "yes", "TRUE ", "", "on"])
    async def test_only_literal_true_enables(
        self, session_factory: async_sessionmaker[AsyncSession], value: str
    ) -> None:
        await _set(session_factory, SETTING_FEATURE_SKILLS, value)
        async with session_factory() as db:
            expected = value.strip().lower() == "true"
            assert await is_feature_enabled(db, SETTING_FEATURE_SKILLS) is expected

    @pytest.mark.asyncio
    async def test_are_features_enabled_shape(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _set(session_factory, SETTING_FEATURE_WORKFLOWS, "true")
        async with session_factory() as db:
            flags = await are_features_enabled(db)
        assert flags == {"skills": False, "workflows": True}


class TestRouterGate:
    """The dependencies 404 when a module is off and pass when on."""

    async def _client(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> AsyncClient:
        from fim_one.db import get_session

        app = FastAPI()

        @app.get("/skills-probe", dependencies=[Depends(require_skills_enabled)])
        async def _skills_probe() -> dict[str, bool]:
            return {"ok": True}

        @app.get(
            "/workflows-probe",
            dependencies=[Depends(require_workflows_enabled)],
        )
        async def _workflows_probe() -> dict[str, bool]:
            return {"ok": True}

        async def _override() -> AsyncIterator[AsyncSession]:
            async with session_factory() as s:
                yield s

        app.dependency_overrides[get_session] = _override
        return AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )

    @pytest.mark.asyncio
    async def test_skills_off_404s(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        async with await self._client(session_factory) as client:
            resp = await client.get("/skills-probe")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "module_disabled"

    @pytest.mark.asyncio
    async def test_skills_on_passes(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _set(session_factory, SETTING_FEATURE_SKILLS, "true")
        async with await self._client(session_factory) as client:
            resp = await client.get("/skills-probe")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_workflows_off_404s(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        async with await self._client(session_factory) as client:
            resp = await client.get("/workflows-probe")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_workflows_on_passes(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _set(session_factory, SETTING_FEATURE_WORKFLOWS, "true")
        async with await self._client(session_factory) as client:
            resp = await client.get("/workflows-probe")
        assert resp.status_code == 200

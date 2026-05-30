"""Integration tests for _resolve_model_supports_vision.

Uses a real in-memory SQLite database (NOT mocks) to verify vision
resolution against the actual ORM models.  This prevents the
refactoring-residue bug where mock dicts pass but real ORM objects fail.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.db.base import Base
from fim_one.web.api.chat import _resolve_model_supports_vision
from fim_one.db.models.model_provider import (
    ModelGroup,
    ModelProvider,
    ModelProviderModel,
)


@pytest.fixture()
async def db_session() -> Any:
    """Create an in-memory SQLite DB with all tables and yield a session."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


def _make_provider(name: str = "TestProvider") -> ModelProvider:
    return ModelProvider(
        id=str(uuid.uuid4()),
        name=name,
        base_url="https://api.test.com/v1",
        api_key="sk-test",
    )


def _make_model(
    provider_id: str,
    *,
    name: str = "test-model",
    supports_vision: bool = False,
) -> ModelProviderModel:
    return ModelProviderModel(
        id=str(uuid.uuid4()),
        provider_id=provider_id,
        name=name,
        model_name=name,
        supports_vision=supports_vision,
    )


def _make_group(
    *,
    name: str = "TestGroup",
    general_model_id: str | None = None,
    is_active: bool = True,
) -> ModelGroup:
    return ModelGroup(
        id=str(uuid.uuid4()),
        name=name,
        general_model_id=general_model_id,
        is_active=is_active,
    )


class TestResolveModelSupportsVision:
    """Integration tests using real ORM objects + in-memory SQLite."""

    @pytest.mark.asyncio
    async def test_no_config_no_group_returns_false(
        self, db_session: AsyncSession
    ) -> None:
        """No model configs, no groups → False."""
        result = await _resolve_model_supports_vision(None, db_session)
        assert result is False

    @pytest.mark.asyncio
    async def test_active_group_vision_enabled(
        self, db_session: AsyncSession
    ) -> None:
        """Active group with vision-enabled general model → True."""
        provider = _make_provider()
        model = _make_model(provider.id, name="claude-sonnet", supports_vision=True)
        group = _make_group(general_model_id=model.id, is_active=True)

        db_session.add_all([provider, model, group])
        await db_session.commit()

        result = await _resolve_model_supports_vision(None, db_session)
        assert result is True

    @pytest.mark.asyncio
    async def test_active_group_vision_disabled(
        self, db_session: AsyncSession
    ) -> None:
        """Active group with vision-disabled general model → False."""
        provider = _make_provider()
        model = _make_model(provider.id, name="deepseek-v3", supports_vision=False)
        group = _make_group(general_model_id=model.id, is_active=True)

        db_session.add_all([provider, model, group])
        await db_session.commit()

        result = await _resolve_model_supports_vision(None, db_session)
        assert result is False

    @pytest.mark.asyncio
    async def test_inactive_group_ignored(
        self, db_session: AsyncSession
    ) -> None:
        """Inactive group is ignored → False."""
        provider = _make_provider()
        model = _make_model(provider.id, name="claude-sonnet", supports_vision=True)
        group = _make_group(general_model_id=model.id, is_active=False)

        db_session.add_all([provider, model, group])
        await db_session.commit()

        result = await _resolve_model_supports_vision(None, db_session)
        assert result is False

    @pytest.mark.asyncio
    async def test_group_without_general_model(
        self, db_session: AsyncSession
    ) -> None:
        """Active group with no general model assigned → False."""
        group = _make_group(general_model_id=None, is_active=True)

        db_session.add(group)
        await db_session.commit()

        result = await _resolve_model_supports_vision(None, db_session)
        assert result is False

    @pytest.mark.asyncio
    async def test_general_model_is_orm_object_not_dict(
        self, db_session: AsyncSession
    ) -> None:
        """Critical regression test: general_model must be an ORM object,
        not a dict.  The old code did isinstance(group.general_model, dict)
        which silently returned False.
        """
        provider = _make_provider()
        model = _make_model(provider.id, name="claude-opus", supports_vision=True)
        group = _make_group(general_model_id=model.id, is_active=True)

        db_session.add_all([provider, model, group])
        await db_session.commit()

        # Re-fetch to ensure we get a fully loaded ORM object
        from sqlalchemy import select

        stmt = select(ModelGroup).where(ModelGroup.id == group.id)
        result = await db_session.execute(stmt)
        loaded_group = result.scalar_one()

        # This is the critical assertion: general_model is an ORM object
        assert loaded_group.general_model is not None
        assert not isinstance(loaded_group.general_model, dict)
        assert isinstance(loaded_group.general_model, ModelProviderModel)
        assert loaded_group.general_model.supports_vision is True
